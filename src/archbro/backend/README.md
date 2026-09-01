# Core Backend / Agent

Owns ArchBro product semantics and decision logic:

- `api/` — product REST/event contract
- `core/` — Project/Goal/Architecture/Task contracts, action execution, persistence port
- `agent/` — orchestration and drift evaluation
- `llm/` — model-provider abstraction and concrete/fake providers

Backend code must not import `platform/runtime`. It depends on `ProjectRepositoryPort` instead of concrete database implementations.

## Drift evaluation boundary

For normal post-architecture agent runs, the model returns a structured `DriftEvaluation` before mutations are applied.

Classifications:

- `ALIGNED`
- `IMPLEMENTATION_ISSUE`
- `ARCHITECTURE_DRIFT`
- `INSUFFICIENT_EVIDENCE`

Flow:

`ProjectEvent -> Provider -> DriftEvaluation + proposed AgentAction[] -> DriftPolicy -> ActionExecutor`

`DriftPolicy` is deterministic. Architecture proposals are rejected unless the evaluation is `ARCHITECTURE_DRIFT`, and proposal `affected_components` must match the evaluated boundary.

Explicit task Start/Done transitions remain deterministic and do not require model evaluation.

## Trusted external observation ingestion

The public `/projects/{project_id}/events` API accepts user/frontend observations only. Trusted provider and `SYSTEM` provenance are server-controlled and must not be asserted by a request body.

Example GitHub path:

`verified GitHub webhook -> integration adapter -> normalized GitHubChangePayload -> server-constructed ProjectEvent(source=GITHUB, type=GITHUB_CHANGE) -> AgentOrchestrator`

Provider verification remains integration-owned. The backend owns normalized contract validation, idempotent observation processing, evidence linkage, and domain mutation policy.
