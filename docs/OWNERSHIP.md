# Archbro ownership and boundaries

This file maps product responsibilities to stable code boundaries. Ownership is described by role rather than by individual name so the structure survives team changes.

## Ownership

| Area | Primary responsibility | Responsibilities |
| --- | --- | --- |
| `frontend/` | Shaun | Goal / Ask, Architecture View, Task View, Proposal Review, browser UX |
| `src/archbro/backend/` | Jim | Project State, Goal contract, Architecture, Tasks, Agent logic, drift evaluation, change proposals, product API |
| `src/archbro/integrations/` | Ayushi | Firebase Auth, user/team identity, permissions |
| `src/archbro/integrations/github/` | Max | GitHub ingestion and normalization into pipeline signals |
| `src/archbro/platform/` | Max | PostgreSQL persistence, runtime composition, event pipeline, CI/CD/Cloud Run, logging/observability |

## Platform direction

Archbro intentionally reuses the Firebase Authentication pattern already proven in Keys by Friday, with PostgreSQL for durable state. Do not introduce a parallel AWS identity/database stack unless the team explicitly changes platform direction.

```text
Browser / authenticated principal
        -> identity adapter
        -> normalized user/team identity
        -> product API / authorization contract

Project / Architecture / Tasks
        -> Jim ProjectRepositoryPort
        -> Max PostgresProjectRepository
        -> PostgreSQL
```

Firebase Authentication answers **who is this user**. PostgreSQL persistence answers **what durable Archbro project state is stored**. These are separate ownership boundaries. Archbro uses Firebase for Auth only; it stores no project state in Firestore.

## Dependency direction

```text
frontend/web
    -> backend/api
        -> backend/core + backend/agent

integrations/auth
    -> backend/api identity/permission boundary

integrations/providers
    -> normalized events
        -> platform/pipeline
            -> backend/agent

backend/core
    -> ProjectRepositoryPort
platform/persistence
    -> PostgreSQL implementation

platform/runtime
    = compose frontend + backend API + persistence + model provider
```

The important rule is that backend code depends on backend-owned contracts, not Firebase Admin, PostgreSQL, or deployment details.

## Team flow

```text
Shaun -> Jim
Ayushi -> Jim
Max -> Jim

Firebase Auth -> Ayushi -> Jim
GitHub -> Max -> Jim
PostgreSQL -> Max -> Jim repository contract
Jim -> LLM
```

## Change rules

1. **Frontend product work** stays under `frontend/` unless an API contract must change.
2. **Domain/API/Agent changes** stay under `src/archbro/backend/`.
3. **Firebase Auth/GitHub provider-specific code** stays under `src/archbro/integrations/`; it establishes trusted identity or normalizes external signals but does not mutate domain state directly.
4. **Persistence/runtime/deploy/logging changes** stay under `src/archbro/platform/`.
5. `backend/core/contracts.py` is the shared product contract. Changes there should be reviewed by Jim and any owner whose boundary consumes the changed contract.
6. `backend/core/repository.py` defines the persistence port. Max implements it; Jim should not import `PostgresProjectRepository`.
7. `platform/runtime/app.py` is the composition root. It may import all concrete implementations; feature modules should not import the runtime layer.
8. Do not move provider-specific Firebase/GitHub details into Agent prompts or domain contracts.
9. Do not let frontend directly access privileged project state. Product state crosses the backend API/event contract.
10. Firebase ID-token verification is not the same as authorization. Per-project access control begins only when User/Team ownership is explicit in the product contract.

## Product loop

```text
Goal
-> Living Architecture
-> Tasks
-> Human / Agent Execution
-> Project Signals & Evidence
-> Agent Evaluation
-> Deterministic Update or Architecture Proposal
-> Human Review for consequential architecture change
```

### Trusted event provenance

Provider provenance is not caller-controlled authentication metadata. A public API caller cannot prove an event came from GitHub, Slack, or another provider merely by setting a source field. Provider adapters verify/authorize the external source, normalize it, and only server-side code constructs trusted project events passed to the backend orchestrator.
