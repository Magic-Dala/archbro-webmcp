# Archbro ownership and boundaries

This file maps team ownership to stable product boundaries. Folder names describe responsibilities, not people, so the structure survives team changes.

## Ownership

| Area | Primary owner | Responsibilities |
| --- | --- | --- |
| `frontend/` | Shaun | Goal / Ask, Architecture View, Task View, Proposal Review, browser UX |
| `src/archbro/backend/` | Jim | Project State, Goal contract, Architecture, Tasks, Agent logic, drift evaluation, change proposals, product API |
| `src/archbro/integrations/` | Ayushi | Firebase Auth, user/team identity, permissions, GitHub integration, external-event normalization |
| `src/archbro/platform/` | Max | Firestore/SQLite persistence, runtime composition, event pipeline, CI/CD/Cloud Run, logging/observability |

## Google Cloud / KBF reuse decision

Archbro intentionally reuses the Firebase/Firestore infrastructure pattern already proven in Keys by Friday. Do not introduce a parallel AWS identity/database stack unless the team explicitly changes platform direction.

```text
Browser / Firebase Auth
        -> Ayushi Firebase identity adapter
        -> normalized user/team identity
        -> Jim product API / authorization contract

Project / Architecture / Tasks
        -> Jim ProjectRepositoryPort
        -> Max FirestoreProjectRepository
        -> Cloud Firestore
```

Firebase Authentication answers **who is this user**. Firestore persistence answers **what durable Archbro project state is stored**. These are separate ownership boundaries.

## Dependency direction

```text
frontend/web
    -> backend/api
        -> backend/core + backend/agent

integrations/firebase
    -> integrations/auth
        -> backend/api identity/permission boundary

integrations/github
    -> integrations/events
        -> platform/pipeline
            -> backend/agent

backend/core
    -> ProjectRepositoryPort
platform/persistence
    -> SQLite or Firestore implementation

platform/runtime
    = compose frontend + backend API + persistence + model provider
```

The important rule is that backend code depends on backend-owned contracts, not Firebase Admin, Firestore, SQLite, or deployment details.

## Team flow

```text
Shaun -> Jim
Ayushi -> Jim
Max -> Jim

Firebase Auth -> Ayushi -> Jim
GitHub -> Ayushi -> Max -> Jim
Firestore -> Max -> Jim repository contract
Jim -> LLM
```

## Change rules

1. **Frontend product work** stays under `frontend/` unless an API contract must change.
2. **Domain/API/Agent changes** stay under `src/archbro/backend/`.
3. **Firebase Auth/GitHub provider-specific code** stays under `src/archbro/integrations/`; it establishes trusted identity or normalizes external signals but does not mutate domain state directly.
4. **Firestore/SQLite/runtime/deploy/logging changes** stay under `src/archbro/platform/`.
5. `backend/core/contracts.py` is the shared product contract. Changes there should be reviewed by Jim and any owner whose boundary consumes the changed contract.
6. `backend/core/repository.py` defines the persistence port. Max implements it; Jim should not import concrete Firestore/SQLite classes.
7. `platform/runtime/app.py` is the composition root. It may import all concrete implementations; feature modules should not import the runtime layer.
8. Do not move provider-specific Firebase/GitHub details into Agent prompts or domain contracts.
9. Do not let frontend directly access privileged Firestore project state. Product state crosses the backend API/event contract.
10. Firebase ID-token verification is not the same as authorization. Per-project access control begins only when User/Team ownership is explicit in the product contract.

## Product loop

```text
Goal
-> Architecture
-> Tasks
-> Human Execution
-> Project Signals
-> Agent Evaluation
-> Update / Proposal
-> Human Review when architecture changes
```
