# Platform / Infra — Max

Owns concrete runtime and operational infrastructure:

- `persistence/` — PostgreSQL, the only store, implementing the backend-owned `ProjectRepositoryPort`.
- `runtime/` — FastAPI composition root and dependency wiring.
- `pipeline/` — normalized event delivery / buffering boundary.
- `observability/` — logs, metrics, telemetry.
- `deploy/` — deployment-facing helpers. The deployment assets themselves live in the repository root `deploy/`.

This intentionally reuses KBF's Google Cloud/Firebase direction rather than introducing an AWS platform path. Platform composes concrete dependencies but should not absorb Agent/domain semantics. `runtime/app.py` is the only layer expected to know the concrete frontend, repository, and model provider at the same time.
