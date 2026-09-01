# Development

Everything runs in Docker. You do not need Python, PostgreSQL, or any Google
Cloud credential on your machine.

## Requirements

Docker Desktop with Compose **v2.24 or newer** (`docker compose version`).
Earlier versions reject the `env_file:` long form this project uses.

## Start the stack

```bash
docker compose up -d --wait
```

`--wait` blocks until both containers report healthy, so when the command
returns the service is genuinely up rather than merely started.

| Command | What it does |
| --- | --- |
| `docker compose up -d --wait` | Start everything, block until healthy |
| `docker compose ps` | Show what is running and whether it is healthy |
| `docker compose logs -f app` | Follow application logs |
| `docker compose down` | Stop, keeping data |
| `docker compose down -v` | Stop **and delete the databases** |
| `docker compose build app` | Rebuild after changing dependencies |

## Where to look

| URL | What |
| --- | --- |
| http://localhost:8080/ | The application |
| http://localhost:8080/?mode=webmcp | WebMCP mode |
| **http://localhost:8080/docs** | **Interactive API docs — call any endpoint from the browser** |
| http://localhost:8080/healthz | Liveness probe, returns `{"status":"ok"}` |

`/docs` is usually the fastest way to try an endpoint by hand; it is generated
from the route definitions, so it is never out of date.

## Editing code

The repository is bind-mounted into the container and the app runs under
`uvicorn --reload`. Save a file and the server restarts by itself — no rebuild,
no restart command. Watch it happen with `docker compose logs -f app`.

The whole repository is mounted rather than a list of directories, because the
suite reaches beyond `src/` and `tests/`: it imports from `qa/`, executes
`deploy/deploy-stack.sh`, and reads files under `docs/`. Any omission shows up
as a collection error, which aborts the entire run instead of failing one file.

Rebuild only when `pyproject.toml` changes: `docker compose build app`.

## Tests

```bash
docker compose run --rm app python -m pytest              # everything
docker compose run --rm app python -m pytest tests/test_api_contract.py
docker compose run --rm app python -m pytest -k healthz   # by name
```

Do not add `--no-deps`: almost every test needs the database container, and
without `DATABASE_URL` they silently **skip** rather than fail.

The suite should pass completely. Without a real Gemini API key, only the seven
`test_real_gemini.py` cases are expected to skip.

## Persistence

PostgreSQL is the only store. The application talks to
`ProjectRepositoryPort`, never to a database directly, so the domain code stays
independent of it -- but there is nothing to switch: `ARCHBRO_PERSISTENCE`
accepts only `postgres`, and the app refuses to start without `DATABASE_URL`.

There used to be SQLite and Firestore implementations as well. Three
implementations meant three chances to diverge on a detail no test pins down --
the last such bug was a Postgres repository raising a different exception type
than SQLite for a duplicate observation. One implementation cannot disagree
with itself.

```bash
docker compose up -d --wait
```

**How the app reaches the database.** Compose builds `DATABASE_URL` from the
`POSTGRES_*` values and injects it:

```
postgresql://archbro:archbro@db:5432/archbro
             ^user   ^password ^service name in the Compose network
```

`db` is the service name, and Compose's internal DNS resolves it. The port is
also published on `127.0.0.1:5432` so a GUI client on your machine can connect;
it is bound to loopback deliberately, because that password really is
`archbro`.

Inspect the database directly:

```bash
docker compose exec db psql -U archbro -d archbro
\dt                          # list tables
select count(*) from projects;
```

## Configuration

`.env` is optional — the stack starts without one. Compose reads it if present,
and `.env` is gitignored.

Defaults chosen so a new machine needs no secrets:

| Variable | Default | Why |
| --- | --- | --- |
| `ARCHBRO_AUTH_MODE` | `local` | No Firebase project needed |
| `ARCHBRO_PROVIDER` | `fake` | Deterministic model, no API key needed |
| `ARCHBRO_PERSISTENCE` | `postgres` | The `db` service is part of the stack |

For real model calls, put `GEMINI_API_KEY=...` and `ARCHBRO_PROVIDER=gemini`
in `.env`. See `.env.example` for every variable.

`docker-compose.yml` pins `ARCHBRO_ENV` and `ARCHBRO_AUTH_MODE` to `local` and
a `.env` cannot override them, because `environment:` outranks `env_file:`.
That is deliberate: this file must never be usable as a deployment.

## When something is wrong

**Port 8080 or 5432 already taken.** Put `ARCHBRO_PORT=8081` or
`POSTGRES_PORT=5433` in `.env`.

**Changed `POSTGRES_PASSWORD` and now the app cannot connect.** PostgreSQL sets
the password only when it first initialises the data directory. An existing
volume keeps the old one. Delete it: `docker compose down -v` — which also
deletes the data.

**Import errors after pulling.** Dependencies changed. `docker compose build app`.

**Tests pass locally but the PostgreSQL ones say `skipped`.** `DATABASE_URL` is
not reaching them, usually from `--no-deps`. Those tests skip themselves when
it is absent, so a skip means they never ran.

**A stale container.** `docker compose down && docker compose up -d --wait`.

## Working agreements

**Write the test first.** Every change with logic starts with a failing test.
A test written alongside its implementation has never demonstrated that it
detects anything.

**Respect the ownership boundaries** in [OWNERSHIP.md](OWNERSHIP.md). The one
that bites hardest: `backend/core/repository.py` defines the persistence port,
and backend code must not import `PostgresProjectRepository`. Import the port.
That is what keeps the domain independent of the store.

**`platform/runtime/app.py` is the only place that chooses concrete
implementations.** Feature code never imports the runtime layer.

**Material architecture changes go through Proposal → Human Review → Accept.**
Accepted architecture is never silently replaced; see [../ARCHBRO.md](../ARCHBRO.md).

**Run the whole suite before opening a PR**, not just the file you touched.
CI runs it against a real PostgreSQL and will fail if the database tests skip.

**An apparent inconsistency is usually deliberate.** Check the file header and
the surrounding comments for the reason before "fixing" it.
