# Archbro

Archbro is a human-guided agentic project workspace where humans and AI agents share one living architecture, execution state, and review boundary.

Instead of letting an agent guess the UI or maintain a separate plan, Archbro exposes semantic browser-native WebMCP Site Tools. The host agent can read current project reality, reason about architecture drift, submit a reviewable recommendation, and continue execution after a human decision.

## Product loop

```text
Goal
-> Living Architecture
-> Tasks
-> Human / Agent Execution
-> Project Signals & Evidence
-> Agent Evaluation
-> Update or Architecture Proposal
-> Human Review for Consequential Changes
-> Reconciled Execution
```

## WebMCP integration

The WebMCP integration uses the imperative browser API:

```js
document.modelContext.registerTool(...)
```

The production WebMCP implementation lives directly in this repository and has no runtime or build dependency on an external adapter repository.

Default semantic Site Tools (14 when no connected MCP gateway is configured):

| Tool | Purpose |
| --- | --- |
| `archbro_ping` | Verify the native WebMCP connection without mutation or model invocation |
| `archbro_get_agent_context` | Read compact project and connected-source context |
| `archbro_get_architecture_diagram` | Read root/subsystem projections from the backend-authored Living Architecture graph |
| `archbro_get_architecture_node_context` | Read bounded upstream/downstream dependency context for a stable Living Architecture node |
| `archbro_find_architecture_path` | Find a directed authored dependency path between architecture nodes |
| `archbro_bootstrap_project` | Atomically commit a host-designed Architecture v1 only after SYSTEM_MAP → recursive per-scope evaluation → RECONCILE planning; every SYSTEM_MAP root must expand, and every component must be EXPANDED or a JUSTIFIED_LEAF and the trace is validated against the final hierarchy |
| `archbro_expand_architecture_scope` | Propose an additive one-level decomposition under an existing component; human acceptance remains required |
| `archbro_get_architecture_decision_context` | Read accepted Living Architecture, execution state, evidence, and governance rules |
| `archbro_submit_architecture_recommendation` | Submit architecture-specific reasoning; changes become `PENDING` human review |
| `archbro_publish_code_architecture` | Validate and persist revision-pinned Code Architecture evidence; accepted Living Architecture is unchanged |
| `archbro_get_code_architecture` | Read the latest persisted Code Architecture implementation-evidence snapshot |
| `archbro_create_task` | Create normal execution work inside the accepted Living Architecture without invoking the built-in model |
| `archbro_update_task_status` | Start or complete an existing task through the deterministic task boundary without invoking the built-in model |
| `archbro_record_project_observation` | Persist external evidence/project facts without pretending they are architecture recommendations |

If the deployment configures connected MCP servers, three gateway tools are added: `archbro_list_connected_mcp_servers`, `archbro_list_connected_mcp_tools`, and `archbro_call_connected_mcp_tool`, for 17 total. They are absent when no gateway is configured. The calling host agent owns reasoning. Archbro owns validation, state, governance, and deterministic execution. A WebMCP agent can recommend an architecture change but cannot approve it.

Connected provider access is principal-scoped and fail-closed. Public or tunneled provider routes require a verified per-user identity rather than the shared local-development principal. GitHub keeps the official MCP read-only mode and adds an Archbro-owned backstop: only tools explicitly advertising MCP `annotations.readOnlyHint=true` are exposed or callable, so missing, false, malformed, and unknown tool metadata are rejected before provider dispatch. Google Drive OAuth requests read-only Drive access. Microsoft Teams is read-only by default; write scopes and write tools appear only when `ARCHBRO_TEAMS_ENABLE_WRITE=true`. Reconnect flows force account selection for GitHub, Google Drive, and Microsoft Teams, while Slack keeps workspace selection under the user's control.

The Architecture workspace has two deliberately separate views. **Living** is the human-approved canonical design intent and keeps stable `node:<component_id>` identities. **Code** is derived implementation evidence at one exact GitHub commit and uses the separate `code-node:*` namespace. Publishing a Code Architecture snapshot does not mutate the accepted Living Architecture; implementation drift still requires a normal reviewable architecture proposal.

Architecture diagrams are positioned by the backend with deterministic topology-aware layout and routing. The diagram endpoint supports `MAP`, `READ`, and `FULL` reading modes: `MAP` may reduce redundant relationship edges for a clearer overview, while `READ` and `FULL` retain the complete authored relationship set. Node placement and relationship routing remain stable across reading modes so changing information density does not reshuffle the graph.

See [`docs/WEBMCP.md`](docs/WEBMCP.md) for the complete contract and governance invariants.

## Why WebMCP matters here

```text
External host agent
      |
      v
Archbro Site Tools
      |
      +--> reads current project + evidence
      +--> reasons about architecture drift
      +--> submits reviewable recommendation
      |
      v
Human Accept / Reject
      |
      v
Architecture version + tasks reconciled
      |
      v
Agent continues execution
```

The agent does not need DOM automation, and Archbro does not expose an unsafe direct architecture-mutation tool.

## WebMCP acceptance mode

Open:

```text
/?mode=webmcp
```

This mode disables the human New Project flow, built-in architecture generation, built-in agent messaging, and manual task Start/Done controls so a WebMCP acceptance run cannot silently fall back to browser automation. Human architecture Accept/Reject remains enabled.

## Project layout

```text
frontend/                       # Web UI + WebMCP browser integration
src/archbro/backend/            # Core backend, agent, API, governance
src/archbro/integrations/       # Firebase Auth / external integrations
src/archbro/platform/           # PostgreSQL persistence / runtime composition
tests/                          # contract + regression + golden WebMCP flow
qa/                             # browser and WebMCP acceptance harnesses
docs/OWNERSHIP.md               # ownership + dependency rules
docs/WEBMCP.md                  # WebMCP contract and governance
docs/DEMO.md                    # concise WebMCP demo script
```

## Run the stack with Docker Compose

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full development guide:
startup, database access, troubleshooting, and working agreements.

The recommended way to get a working environment. One command, and no Google
Cloud credentials or Gemini API key are needed:

```bash
docker compose up -d --wait
```

Requires Docker Compose v2.24 or newer, which is when `env_file:` gained the
long form that lets a missing `.env` be non-fatal.

That builds the app image, starts PostgreSQL, blocks until both containers
report healthy, and serves the app on the Compose application port.

| Command | What it does |
| --- | --- |
| `docker compose up -d --wait` | Start everything, block until healthy |
| `docker compose run --rm app python -m pytest` | Run the test suite in the container |
| `docker compose logs -f app` | Follow application logs |
| `docker compose down` | Stop the stack, keeping data |
| `docker compose down -v` | Stop and delete the database volume |

`src/`, `tests/`, and `frontend/` are bind-mounted and the app runs with
`--reload`, so edits take effect without a rebuild. Rebuild only when
dependencies change: `docker compose build app`.

The defaults are chosen so a new team member needs no secrets: authentication
uses the local development principal (`ARCHBRO_AUTH_MODE=local`) and the model
provider is the deterministic fake (`ARCHBRO_PROVIDER=fake`). To exercise real
model calls, put `GEMINI_API_KEY` and `ARCHBRO_PROVIDER=gemini` in `.env`; the
app container reads that file when it exists.

`/healthz` is the container liveness probe. It reports only that the process is
serving and deliberately does not touch persistence, so a transient database
outage cannot trigger a restart storm.

The `db` service runs PostgreSQL 17 and is reachable inside the Compose network
through the `db` service on port `5432`, using the `archbro` database. It is the
only store Archbro has: `ARCHBRO_PERSISTENCE` accepts only `postgres` and the app
refuses to start without `DATABASE_URL`. Compose builds that connection value
from the `POSTGRES_*` settings.

Both published ports bind to `127.0.0.1`, so the development database -- whose
password really is `archbro` -- is not reachable from the rest of the network.

`docker-compose.yml` is for development only and must never be deployed. Its
`environment:` block outranks `env_file:`, pinning `ARCHBRO_ENV` and
`ARCHBRO_AUTH_MODE` to `local`; a `.env` on a server cannot override them. A
deployment using this file would serve real traffic on the local development
principal, and the production guard in `create_app()` would stay silent because
it only fires when `ARCHBRO_ENV` says `production`. Production gets its own
compose file.

## Run locally without Docker

Use a project-local virtual environment:

```powershell
cd archbro
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m uvicorn archbro.main:app --host 127.0.0.1 --port 8011
```

After starting Uvicorn on port `8011`, use the normal local product surface. Enable WebMCP mode when running the stricter acceptance flow.

## Environment

`.env` is loaded automatically.

```env
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.7-flash
ARCHBRO_PROVIDER=gemini
ARCHBRO_ENV=local
ARCHBRO_AUTH_MODE=local
ARCHBRO_PERSISTENCE=postgres
FIREBASE_PROJECT_ID=
ARCHBRO_FIREBASE_API_KEY=
ARCHBRO_FIREBASE_AUTH_DOMAIN=
ARCHBRO_FIREBASE_APP_ID=
ARCHBRO_GOAL_REQUEST_TIMEOUT_SECONDS=30
```

For deterministic WebMCP acceptance without built-in model calls:

```env
ARCHBRO_PROVIDER=fake
```

Both deployed stacks (`archbro-main` and `archbro-dev`) must set
`ARCHBRO_ENV=production` and `ARCHBRO_AUTH_MODE=firebase`. Deployment validation
fails closed if Firebase identity or its public browser config is missing; the
local-development principal is valid only for direct local development and cannot
be used by an externally reachable deployed stack.

`qa/setup_archbro_identity_platform.ps1` provisions the complete browser login
boundary directly through Google Cloud Identity Platform. It enables email/password,
disables anonymous login, configures Google and GitHub, authorizes the requested
hostnames, and creates a browser API key restricted to Identity Toolkit/Secure Token.
The required `-AuthDomain` is written into the generated, gitignored
`.archbro-firebase-public.json`; an empty popup-auth domain is never emitted.

Before running the setup script, inject these setup-only values from the approved
secret store into the PowerShell process environment:

```text
ARCHBRO_FIREBASE_GOOGLE_OAUTH_CLIENT_ID
ARCHBRO_FIREBASE_GOOGLE_OAUTH_CLIENT_SECRET
ARCHBRO_FIREBASE_GITHUB_OAUTH_CLIENT_ID
ARCHBRO_FIREBASE_GITHUB_OAUTH_CLIENT_SECRET
```

Then run, for example:

```powershell
.\qa\setup_archbro_identity_platform.ps1 `
  -ProjectId "your-firebase-project" `
  -AuthDomain "<firebase-auth-domain>" `
  -PublicHost "<production-host>" `
  -StagingHost "<development-host>"
```

Do not place the OAuth client secrets on the command line, in the generated public
JSON, or in repository environment examples. The setup process sends them only to
Identity Platform. This keeps auth independent from Firebase Hosting while remaining
compatible with Firebase Admin ID-token verification.

Privileged project state remains behind FastAPI + Firebase Admin ID-token
verification + project authorization; the browser never reaches the database
directly. Firebase is used for Authentication only -- Archbro stores no project
state in Firestore, so there are no client-facing database rules to deploy.

## Runtime composition

```text
Frontend / WebMCP
    -> backend API
        -> core / agent / governance contracts
            -> ProjectRepositoryPort
                -> PostgreSQL

Firebase Auth / integrations
    -> trusted identity + normalized evidence
        -> backend / event pipeline
            -> agent evaluation
```

## Architecture approval boundary

Normal execution state can advance deterministically. Material architecture drift creates a pending proposal. Only explicit human acceptance increments the architecture version.

When a component is replaced, unfinished work is re-scoped to the accepted replacement and becomes ready again. Work tied to a removed component remains blocked until it is redefined.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The real Gemini smoke test runs when a Gemini API key is available. Deterministic tests do not require a model call.

The golden WebMCP governance loop is covered by `tests/test_webmcp_golden_flow.py`.

Browser-native local probes live under `qa/`, including `qa/probe_webmcp_live.py`.

## Container deployment

A production-oriented `Dockerfile` is included. The container listens on `$PORT` (default `8080`). Configure environment variables in the deployment platform rather than baking credentials into the image.

Two deployments exist.

**Current deployment.** `main` and `dev` run as two isolated Compose stacks on one GCE instance, each with its own PostgreSQL. GitHub Actions builds, pushes, and deploys on a push to either branch. [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) records every resource, why it is set up that way, and how to rebuild it; see also [`deploy/`](deploy/) and `.github/workflows/deploy.yml`. Both are reached only through separate Cloudflare Tunnels — the instance publishes no HTTP port at all — and `dev` additionally sits behind Cloudflare Access with an email allowlist. `.env` files are placed on the instance by hand and are never written by the workflow.

**Development:** the `dev` stack is live through its dedicated tunnel.

**Production:** the `main` stack remains reserved until production enablement; the current deployment does not use the retired Worker/Cloud Run challenge route.

## License

MIT. See [`LICENSE`](LICENSE).
