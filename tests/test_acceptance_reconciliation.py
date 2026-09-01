import psycopg
import pytest

from archbro.backend.core.action_executor import ActionExecutor
from archbro.backend.core.contracts import (
    AgentAction,
    AgentActionType,
    Architecture,
    ArchitectureChangeProposal,
    ArchitectureOption,
    Component,
    Project,
    ProposalStatus,
    Task,
    TaskOwner,
    TaskSource,
    TaskStatus,
)
from archbro.platform.persistence.postgres import PostgresProjectRepository
from conftest import requires_database

pytestmark = requires_database


def _repo_with_project(dsn):
    repo = PostgresProjectRepository(dsn)
    project = Project(
        name="M5 Reconciliation",
        goal="Keep execution aligned with accepted architecture.",
        architecture_version=1,
    )
    repo.save_project(project)
    repo.save_architecture(
        project.id,
        Architecture(
            version=1,
            components=[
                Component(
                    id="data",
                    name="Data & State",
                    type="system",
                    responsibility="Own application state.",
                    children=[
                        Component(
                            id="primary_store",
                            name="Store A",
                            type="store_a",
                            responsibility="Persist durable application state.",
                        )
                    ],
                ),
                Component(
                    id="api",
                    name="API",
                    type="service",
                    responsibility="Serve product APIs.",
                ),
            ],
        ),
    )
    return repo, project


def _proposal(project_id: str, *, affected_components=None, proposed_changes=None):
    return ArchitectureChangeProposal(
        project_id=project_id,
        base_architecture_version=1,
        reason="The accepted persistence boundary changed.",
        evidence=["The team approved Store B as the new primary store."],
        observed_change="Primary persistence changes from Store A to Store B.",
        affected_components=affected_components or ["primary_store"],
        proposed_changes=proposed_changes
        or [
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store B",
                "new_type": "store_b",
                "new_responsibility": "Persist durable application state.",
            }
        ],
        impact="Persistence implementation and in-flight data work must be reconciled.",
        recommended_option=ArchitectureOption.ACCEPT_PROPOSED_CHANGE,
    )


def test_acceptance_reconciles_tasks_without_technology_specific_rules(dsn):
    repo, project = _repo_with_project(dsn)
    todo = Task(
        title="Implement Store A persistence",
        description="Wire durable application persistence.",
        status=TaskStatus.TODO,
        owner=TaskOwner.HUMAN,
        source=TaskSource.ARCHITECTURE,
        related_component="primary_store",
    )
    in_progress = Task(
        title="Validate persistence recovery",
        status=TaskStatus.IN_PROGRESS,
        related_component="primary_store",
    )
    done = Task(
        title="Document old persistence assumptions",
        description="Historical work should stay complete.",
        status=TaskStatus.DONE,
        related_component="primary_store",
    )
    unrelated = Task(
        title="Implement API health endpoint",
        status=TaskStatus.TODO,
        related_component="api",
    )
    for task in (todo, in_progress, done, unrelated):
        repo.save_task(project.id, task)

    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    accepted = ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert accepted.status == ProposalStatus.ACCEPTED
    architecture = repo.get_architecture(project.id)
    assert architecture.version == 2
    assert architecture.find_component("primary_store").name == "Store B"
    assert architecture.root_component_id_for("primary_store") == "data"

    reconciled_todo = repo.get_task(todo.id)
    assert reconciled_todo.status == TaskStatus.BLOCKED
    assert "Store A to Store B" in reconciled_todo.description
    reconciled_in_progress = repo.get_task(in_progress.id)
    assert reconciled_in_progress.status == TaskStatus.BLOCKED
    assert "re-evaluate this task" in reconciled_in_progress.description

    assert repo.get_task(done.id).status == TaskStatus.DONE
    assert repo.get_task(done.id).description == done.description
    assert repo.get_task(unrelated.id).status == TaskStatus.TODO

    migration_tasks = [
        task
        for task in repo.list_tasks(project.id)
        if task.id not in {todo.id, in_progress.id, done.id, unrelated.id}
    ]
    assert len(migration_tasks) == 1
    migration = migration_tasks[0]
    assert migration.title == "Migrate Store A to Store B"
    assert migration.status == TaskStatus.TODO
    assert migration.owner == TaskOwner.HUMAN
    assert migration.source == TaskSource.ARCHITECTURE
    assert migration.related_component == "primary_store"
    assert migration.acceptance_criteria


def test_acceptance_blocks_other_impacted_component_tasks_but_does_not_invent_extra_task(dsn):
    repo, project = _repo_with_project(dsn)
    primary_task = Task(title="Implement persistence", related_component="primary_store")
    api_task = Task(
        title="Update API persistence adapter",
        status=TaskStatus.IN_PROGRESS,
        related_component="api",
    )
    repo.save_task(project.id, primary_task)
    repo.save_task(project.id, api_task)

    proposal = _proposal(
        project.id,
        affected_components=["primary_store", "api"],
    )
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_task(primary_task.id).status == TaskStatus.BLOCKED
    impacted_api = repo.get_task(api_task.id)
    assert impacted_api.status == TaskStatus.BLOCKED
    assert "impacts this component" in impacted_api.description

    generated = [task for task in repo.list_tasks(project.id) if task.source == TaskSource.ARCHITECTURE]
    generated_migrations = [task for task in generated if task.title == "Migrate Store A to Store B"]
    assert len(generated_migrations) == 1
    assert not any(task.title.startswith("Migrate API") for task in generated)


def test_acceptance_does_not_duplicate_existing_active_migration_task(dsn):
    repo, project = _repo_with_project(dsn)
    existing = Task(
        title="Migrate Store A to Store B",
        description="Human already created the migration work.",
        status=TaskStatus.TODO,
        owner=TaskOwner.HUMAN,
        source=TaskSource.HUMAN,
        related_component="primary_store",
    )
    repo.save_task(project.id, existing)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    matching = [
        task for task in repo.list_tasks(project.id) if task.title == "Migrate Store A to Store B"
    ]
    assert len(matching) == 1
    assert matching[0].id == existing.id
    assert matching[0].source == TaskSource.HUMAN
    assert matching[0].status == TaskStatus.TODO


def test_migration_task_deduplication_is_component_scoped(dsn):
    repo, project = _repo_with_project(dsn)
    architecture = repo.get_architecture(project.id)
    data = architecture.find_component("data")
    data.children.append(
        Component(
            id="archive_store",
            name="Store A",
            type="store_a",
            responsibility="Persist archive state.",
        )
    )
    repo.save_architecture(project.id, architecture)

    proposal = _proposal(
        project.id,
        affected_components=["primary_store", "archive_store"],
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store B",
            },
            {
                "operation": "replace_component",
                "component_id": "archive_store",
                "new_name": "Store B",
            },
        ],
    )
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    migrations = [
        task
        for task in repo.list_tasks(project.id)
        if task.title == "Migrate Store A to Store B"
    ]
    assert {task.related_component for task in migrations} == {"primary_store", "archive_store"}


def test_completed_matching_migration_task_prevents_duplicate_generation(dsn):
    repo, project = _repo_with_project(dsn)
    completed = Task(
        title="Migrate Store A to Store B",
        status=TaskStatus.DONE,
        source=TaskSource.HUMAN,
        related_component="primary_store",
    )
    repo.save_task(project.id, completed)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    matching = [
        task for task in repo.list_tasks(project.id) if task.title == "Migrate Store A to Store B"
    ]
    assert len(matching) == 1
    assert matching[0].id == completed.id
    assert matching[0].status == TaskStatus.DONE


def test_null_optional_replacement_fields_preserve_existing_component_contract(dsn):
    repo, project = _repo_with_project(dsn)
    proposal = _proposal(
        project.id,
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store B",
                "new_type": None,
                "new_responsibility": None,
            }
        ],
    )
    repo.save_proposal(proposal)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    component = repo.get_architecture(project.id).find_component("primary_store")
    assert component.name == "Store B"
    assert component.type == "store_a"
    assert component.responsibility == "Persist durable application state."


def test_invalid_reconciliation_plan_fails_before_architecture_or_tasks_change(dsn):
    repo, project = _repo_with_project(dsn)
    task = Task(
        title="Persistence task",
        status=TaskStatus.IN_PROGRESS,
        related_component="primary_store",
    )
    repo.save_task(project.id, task)
    proposal = _proposal(
        project.id,
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "ghost",
                "new_name": "Store B",
            }
        ],
        affected_components=["ghost"],
    )
    repo.save_proposal(proposal)

    with pytest.raises(ValueError, match="affected component not found: ghost"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_architecture(project.id).version == 1
    assert repo.get_architecture(project.id).find_component("primary_store").name == "Store A"
    assert repo.get_task(task.id).status == TaskStatus.IN_PROGRESS
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING


def test_acceptance_rejects_multiple_replacements_of_same_component_before_writes(dsn):
    repo, project = _repo_with_project(dsn)
    proposal = _proposal(
        project.id,
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store B",
            },
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store C",
            },
        ],
    )
    repo.save_proposal(proposal)

    with pytest.raises(ValueError, match="changes component more than once"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_architecture(project.id).version == 1
    assert repo.get_architecture(project.id).find_component("primary_store").name == "Store A"
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING


def test_acceptance_rejects_proposal_created_for_an_older_architecture_version(dsn):
    repo, project = _repo_with_project(dsn)
    first = _proposal(project.id)
    second = _proposal(
        project.id,
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store C",
            }
        ],
    )
    repo.save_proposal(first)
    repo.save_proposal(second)

    ActionExecutor(repo).accept_proposal(project.id, first.id)
    assert repo.get_architecture(project.id).version == 2

    with pytest.raises(ValueError, match="stale architecture proposal"):
        ActionExecutor(repo).accept_proposal(project.id, second.id)

    assert repo.get_architecture(project.id).version == 2
    assert repo.get_architecture(project.id).find_component("primary_store").name == "Store B"
    assert repo.get_proposal(second.id).status == ProposalStatus.PENDING


def test_acceptance_rejects_noop_component_replacement_without_bumping_architecture(dsn):
    repo, project = _repo_with_project(dsn)
    architecture = repo.get_architecture(project.id)
    component = architecture.find_component("primary_store")
    assert component is not None
    proposal = _proposal(
        project.id,
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": component.id,
                "new_name": component.name,
                "new_type": component.type,
                "new_responsibility": component.responsibility,
            }
        ],
    )
    repo.save_proposal(proposal)

    with pytest.raises(ValueError, match="replacement is a no-op"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_architecture(project.id).version == architecture.version
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING


def test_acceptance_rejects_replace_component_fields_that_would_be_silently_ignored(dsn):
    repo, project = _repo_with_project(dsn)
    proposal = _proposal(
        project.id,
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store B",
                "remove_children": True,
            }
        ],
    )
    repo.save_proposal(proposal)

    with pytest.raises(ValueError, match="unsupported fields: remove_children"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_architecture(project.id).version == 1
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING


def test_acceptance_expand_scope_adds_exactly_one_level_and_preserves_existing_children_and_tasks(dsn):
    repo, project = _repo_with_project(dsn)
    task = Task(
        title="Keep API contract stable",
        status=TaskStatus.IN_PROGRESS,
        related_component="api",
    )
    repo.save_task(project.id, task)
    proposal = _proposal(
        project.id,
        affected_components=["api"],
        proposed_changes=[
            {
                "operation": "expand_scope",
                "component_id": "api",
                "children": [
                    {
                        "id": "request_validation",
                        "name": "Request Validation",
                        "type": "service",
                        "responsibility": "Validate incoming API requests.",
                        "kind": "SERVICE",
                    },
                    {
                        "id": "request_execution",
                        "name": "Request Execution",
                        "type": "service",
                        "responsibility": "Execute accepted API operations.",
                        "kind": "SERVICE",
                    },
                ],
            }
        ],
    )
    repo.save_proposal(proposal)

    accepted = ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    architecture = repo.get_architecture(project.id)
    assert accepted.status == ProposalStatus.ACCEPTED
    assert architecture.version == 2
    assert architecture.child_component_ids_for("api") == ["request_validation", "request_execution"]
    assert architecture.parent_component_id_for("request_validation") == "api"
    assert architecture.parent_component_id_for("request_execution") == "api"
    # A structural decomposition does not invalidate executable work already
    # attached to the stable parent boundary.
    assert repo.get_task(task.id).status == TaskStatus.IN_PROGRESS
    # Unrelated hierarchy is untouched.
    assert architecture.child_component_ids_for("data") == ["primary_store"]


def test_acceptance_expand_scope_rejects_nested_overwrite_and_existing_id_collision_before_writes(dsn):
    repo, project = _repo_with_project(dsn)
    nested = _proposal(
        project.id,
        affected_components=["api"],
        proposed_changes=[
            {
                "operation": "expand_scope",
                "component_id": "api",
                "children": [
                    {
                        "id": "request_pipeline",
                        "name": "Request Pipeline",
                        "type": "service",
                        "responsibility": "Own request processing.",
                        "children": [
                            {
                                "id": "request_validation",
                                "name": "Request Validation",
                                "type": "service",
                                "responsibility": "Validate requests.",
                            }
                        ],
                    }
                ],
            }
        ],
    )
    repo.save_proposal(nested)

    with pytest.raises(ValueError, match="adds exactly one hierarchy level"):
        ActionExecutor(repo).accept_proposal(project.id, nested.id)
    assert repo.get_architecture(project.id).version == 1

    collision = _proposal(
        project.id,
        affected_components=["api"],
        proposed_changes=[
            {
                "operation": "expand_scope",
                "component_id": "api",
                "children": [
                    {
                        "id": "primary_store",
                        "name": "Duplicate Store Boundary",
                        "type": "service",
                        "responsibility": "Invalid duplicate identity.",
                    }
                ],
            }
        ],
    )
    repo.save_proposal(collision)

    with pytest.raises(ValueError, match="child ids already exist"):
        ActionExecutor(repo).accept_proposal(project.id, collision.id)
    assert repo.get_architecture(project.id).version == 1


def test_proposal_persistence_rebuilds_server_owned_review_provenance(dsn):
    repo, project = _repo_with_project(dsn)
    candidate = _proposal(project.id).model_copy(
        update={
            "id": "proposal_provider_chosen",
            "status": ProposalStatus.ACCEPTED,
            "base_architecture_version": 999,
        }
    )
    agent_action = AgentAction(
        type=AgentActionType.PROPOSE_ARCHITECTURE_CHANGE,
        payload={"proposal": candidate.model_dump(mode="json")},
    )

    proposal_ids = ActionExecutor(repo).apply(project.id, [agent_action])

    assert len(proposal_ids) == 1
    assert proposal_ids[0] != candidate.id
    persisted = repo.get_proposal(proposal_ids[0])
    assert persisted.status == ProposalStatus.PENDING
    assert persisted.base_architecture_version == 1
    assert agent_action.payload["proposal"]["id"] == persisted.id
    with pytest.raises(KeyError):
        repo.get_proposal(candidate.id)


def test_acceptance_rejects_inconsistent_project_architecture_version_before_writes(dsn):
    repo, project = _repo_with_project(dsn)
    repo.save_project(project.model_copy(update={"architecture_version": 0}))
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)
    before_architecture = repo.get_architecture(project.id)

    with pytest.raises(ValueError, match="project architecture version is inconsistent"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert repo.get_architecture(project.id) == before_architecture
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING


def test_acceptance_rolls_back_all_state_if_a_write_fails_mid_transaction(dsn):
    repo, project = _repo_with_project(dsn)
    task = Task(
        title="Implement Store A persistence",
        status=TaskStatus.IN_PROGRESS,
        related_component="primary_store",
    )
    repo.save_task(project.id, task)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)

    with repo._connect() as conn:
        conn.execute(
            """
            CREATE FUNCTION fail_acceptance_project_write() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'injected acceptance failure';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER fail_acceptance_project_write
            BEFORE INSERT ON projects
            FOR EACH ROW EXECUTE FUNCTION fail_acceptance_project_write();
            """
        )

    with pytest.raises(psycopg.errors.RaiseException, match="injected acceptance failure"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    architecture = repo.get_architecture(project.id)
    assert architecture.version == 1
    assert architecture.find_component("primary_store").name == "Store A"
    assert repo.get_project(project.id).architecture_version == 1
    assert repo.get_task(task.id).status == TaskStatus.IN_PROGRESS
    assert repo.get_proposal(proposal.id).status == ProposalStatus.PENDING


def test_acceptance_rechecks_base_version_inside_commit(dsn, monkeypatch):
    repo, project = _repo_with_project(dsn)
    stale_architecture = repo.get_architecture(project.id)
    stale_project = repo.get_project(project.id)
    first = _proposal(project.id)
    second = _proposal(
        project.id,
        proposed_changes=[
            {
                "operation": "replace_component",
                "component_id": "primary_store",
                "new_name": "Store C",
            }
        ],
    )
    repo.save_proposal(first)
    repo.save_proposal(second)

    ActionExecutor(repo).accept_proposal(project.id, first.id)
    assert PostgresProjectRepository.get_architecture(repo, project.id).version == 2

    # Simulate a second request that planned against v1 before the first request
    # committed. The atomic repository check must observe the real persisted v2.
    monkeypatch.setattr(repo, "get_architecture", lambda _project_id: stale_architecture)
    monkeypatch.setattr(repo, "get_project", lambda _project_id: stale_project)

    with pytest.raises(ValueError, match="accepted architecture changed before proposal commit"):
        ActionExecutor(repo).accept_proposal(project.id, second.id)

    accepted = PostgresProjectRepository.get_architecture(repo, project.id)
    assert accepted.version == 2
    assert accepted.find_component("primary_store").name == "Store B"
    assert PostgresProjectRepository.get_proposal(repo, second.id).status == ProposalStatus.PENDING


def test_reject_cannot_overwrite_a_concurrent_accept(dsn, monkeypatch):
    repo, project = _repo_with_project(dsn)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)
    stale_pending = repo.get_proposal(proposal.id)

    ActionExecutor(repo).accept_proposal(project.id, proposal.id)
    assert PostgresProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.ACCEPTED

    # Simulate a reject request that read PENDING before the accept committed.
    monkeypatch.setattr(repo, "get_proposal", lambda _proposal_id: stale_pending)
    with pytest.raises(ValueError, match="proposal status changed before decision commit"):
        ActionExecutor(repo).reject_proposal(project.id, proposal.id)

    assert PostgresProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.ACCEPTED


def test_accept_cannot_overwrite_a_concurrent_reject(dsn, monkeypatch):
    repo, project = _repo_with_project(dsn)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)
    stale_pending = repo.get_proposal(proposal.id)

    ActionExecutor(repo).reject_proposal(project.id, proposal.id)
    assert PostgresProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.REJECTED

    monkeypatch.setattr(repo, "get_proposal", lambda _proposal_id: stale_pending)
    with pytest.raises(ValueError, match="proposal is no longer pending at acceptance commit"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert PostgresProjectRepository.get_architecture(repo, project.id).version == 1
    assert PostgresProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.REJECTED


def test_acceptance_rejects_concurrent_task_update(dsn, monkeypatch):
    from datetime import timedelta

    repo, project = _repo_with_project(dsn)
    task = Task(
        title="Validate persistence recovery",
        status=TaskStatus.IN_PROGRESS,
        related_component="primary_store",
    )
    repo.save_task(project.id, task)
    proposal = _proposal(project.id)
    repo.save_proposal(proposal)
    stale_tasks = repo.list_tasks(project.id)

    original_save = repo.save_acceptance_state

    def concurrent_save(**kwargs):
        current = repo.get_task(task.id)
        repo.save_task(
            project.id,
            current.model_copy(
                update={
                    "status": TaskStatus.DONE,
                    "updated_at": current.updated_at + timedelta(seconds=1),
                }
            ),
        )
        return original_save(**kwargs)

    monkeypatch.setattr(repo, "list_tasks", lambda _project_id: stale_tasks)
    monkeypatch.setattr(repo, "save_acceptance_state", concurrent_save)

    with pytest.raises(ValueError, match="acceptance task changed before proposal commit"):
        ActionExecutor(repo).accept_proposal(project.id, proposal.id)

    assert PostgresProjectRepository.get_task(repo, task.id).status == TaskStatus.DONE
    assert PostgresProjectRepository.get_proposal(repo, proposal.id).status == ProposalStatus.PENDING
    assert PostgresProjectRepository.get_architecture(repo, project.id).version == 1
