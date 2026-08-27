# Project observation contract

Archbro treats external changes as **observations of project reality**, not as
commands to the Agent. This contract is provider-agnostic so GitHub, future
integrations, delivery infrastructure, Agent logic, and frontend activity views
can evolve independently.


## GitHub change contract

The GitHub integration owns webhook/App verification, provider payload parsing, and delivery. The backend accepts only normalized `GITHUB_CHANGE` observations with `source=GITHUB` and a stable `source_event_id`.

For the MVP, `payload` is the provider-independent `GitHubChangePayload` contract:

- `repository`: canonical `owner/repo`
- `event_kind`: `PUSH` or `PULL_REQUEST_MERGED`
- `summary`: concise evidence for Agent evaluation
- `ref`: required for `PUSH`
- `commit_sha`: required for both MVP event kinds
- `pull_request_number`: required for `PULL_REQUEST_MERGED`
- optional `actor`, `title`, `changed_files`, and `commits`

Webhook signatures, GitHub installation identity, retries, and raw GitHub payloads remain integration/platform responsibilities and must not be treated as Agent instructions.


## End-to-end boundary

```text
provider-specific signal
        |
        v
integrations/events          Ayushi: normalize provider payloads
        |
        v
platform/pipeline            Max: durable delivery / retry / transport
        |
        v
backend ProjectEvent         Jim: canonical observation contract
        |
        v
Agent evaluation
        |
        v
validated mutation plan + durable AgentRun
        |
        v
frontend activity/evidence   Shaun: present history and review context
```

## Normalized event identity

`ProjectEvent.id` is Archbro-owned. Integrations should not choose it.

For a provider delivery that has a stable identifier, integrations should also
set `source_event_id`. The same `(project_id, source, source_event_id)` must be
re-delivered with the same normalized type and payload. Platform may retry that
event any number of times; Archbro will evaluate and apply it successfully at
most once.

If the same source identity arrives with different normalized observation data,
Archbro rejects it instead of silently changing historical evidence.

## Retry behavior

- completed observation: returns the existing successful AgentRun with
  `replayed=true`; the model is not called again and state is not applied again.
- currently processing observation: returns a conflict so another worker does
  not evaluate it concurrently.
- failed observation: the failure remains in AgentRun history and a later retry
  may claim the same canonical event again.
- abandoned processing claim: a bounded lease allows recovery after a worker
  disappears before commit.

Successful project mutations and their successful AgentRun are committed as one
persistence transition. A failed commit must not expose partial tasks, proposals,
project status, or initial architecture state.

## Evidence trace

Human-readable proposal evidence remains explanatory text. The server also adds
`evidence_event_ids` from the actual observed `ProjectEvent` that produced the
proposal. Provider output cannot choose this provenance.

Persistence rejects an evidence reference that does not exist in the same
project. This gives the frontend a stable chain:

```text
ProjectEvent -> AgentRun -> proposal/action -> accepted project state
```

Read APIs expose `/events`, `/agent-runs`, and `/activity` per project so the UI
does not need to reconstruct this history from logs.

History reads are bounded at the persistence query, not after loading the full
project history into the backend. The Firestore adapter uses these query shapes:

- events: `project_id == <project>` ordered by `data.received_at DESC`, then
  `limit(N)`
- agent runs: `project_id == <project>` ordered by `data.completed_at DESC`, then
  `limit(N)`

Firestore deployments must provision the composite indexes required by those
query shapes for their configured collection prefix. The default collection
names are `archbro_events` and `archbro_agent_runs`; a custom
`ARCHBRO_FIRESTORE_PREFIX` needs equivalent indexes under its own collection
names. Index provisioning stays a Platform/deployment concern rather than being
hard-coded into the backend contract.

## Security boundary

External payloads are untrusted data. Commit messages, issue text, logs, and
other provider text never override the project Goal, accepted Architecture,
system rules, or the human architecture-approval boundary.

`ProjectEvent.source` is **metadata, not authentication or authorization**. A
caller writing `source=GITHUB` does not prove that GitHub sent the request, and a
caller writing `source=HUMAN` does not prove a user's identity. Provider
authentication and project permissions remain the integration/API identity
boundary and must gate production ingestion separately.

Authoritative `TASK_UPDATED` transitions are accepted only through the human /
frontend semantic path. External observations cannot directly set overall
project status.

## Context growth

Durable observation history is an audit/evidence surface, not an instruction to
send the full project history to every model call. Agent context remains bounded
to current accepted architecture, current tasks, pending proposals, recent
notes, and the observation being evaluated.
