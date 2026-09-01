# Platform / Infrastructure

Owns concrete runtime and operational infrastructure:

- `persistence/` — PostgreSQL, the only store, implementing the backend-owned `ProjectRepositoryPort`.
- `runtime/` — FastAPI composition root and dependency wiring.
- `pipeline/` — normalized event delivery / buffering boundary.
- `observability/` — logs, metrics, telemetry.
- `deploy/` — deployment-facing helpers; repository-level deployment assets live under `deploy/`.

Platform composes concrete dependencies but must not absorb agent/domain semantics. `runtime/app.py` is the composition root expected to know the concrete frontend, repository, identity, and model-provider implementations at the same time.
