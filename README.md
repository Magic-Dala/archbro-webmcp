# Archbro

Archbro is a human-guided agentic project workspace that turns a Goal into a living architecture, actionable tasks, project health, and reviewable architecture changes.

## Product loop

```text
Goal
-> Architecture
-> Tasks
-> Human Execution
-> Project Signals
-> Agent Evaluation
-> Update / Proposal
-> Human Review
```

## Team-oriented layout

```text
frontend/                       # Shaun — Frontend / Product
src/archbro/backend/            # Jim — Core Backend / Agent
src/archbro/integrations/       # Ayushi — Firebase Auth / GitHub
src/archbro/platform/           # Max — Firestore / Platform / Infra
tests/                          # contract/regression tests
qa/                             # demo and browser acceptance harnesses
docs/OWNERSHIP.md               # ownership + dependency rules
```

See `docs/OWNERSHIP.md` before adding a new module.

## Run locally

Use a project-local virtual environment. Do not install Archbro into a shared/global Python environment because Strands and other local MCP projects may require different MCP package versions.

```powershell
cd archbro
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m uvicorn archbro.main:app --host 127.0.0.1 --port 8011
```

Open `http://127.0.0.1:8011/`.

For the current local deterministic demo harness, `qa.manual_demo_app:app` can be launched with the same virtual environment.

## Environment

`.env` is loaded automatically. Archbro uses product-specific `ARCHBRO_*` settings and keeps the old `HUMAN_AGENT_*` names only as a compatibility fallback during migration.

```env
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.7-flash
ARCHBRO_PROVIDER=gemini
ARCHBRO_PERSISTENCE=sqlite
ARCHBRO_DB=archbro.db
FIREBASE_PROJECT_ID=
FIRESTORE_PROJECT_ID=
FIRESTORE_DATABASE_ID=(default)
ARCHBRO_FIRESTORE_PREFIX=archbro
ARCHBRO_GOAL_REQUEST_TIMEOUT_SECONDS=30
```

Gemini routing continues to use the existing `GEMINI_*` model/fallback settings.

### Reusing Keys by Friday

Archbro deliberately reuses KBF's Firebase Admin / Firebase ID-token / Firestore repository pattern instead of adding an AWS identity or database path. Local development stays on SQLite; cloud deployment can switch to Firestore with `ARCHBRO_PERSISTENCE=firestore`.

Firebase Auth is Ayushi-owned identity integration. Firestore Project/Architecture/Task persistence is Max-owned platform infrastructure.

## Runtime composition

```text
Shaun frontend
    -> Jim backend API
        -> Jim core / agent / LLM contracts
            -> backend-owned ProjectRepositoryPort
                -> Max persistence implementation

Ayushi Firebase Auth / GitHub
    -> trusted identity + normalized events
        -> Jim API / Max event pipeline
            -> Jim agent evaluation

Max platform/runtime
    -> Firestore or SQLite persistence
    -> composes persistence + provider + backend API + frontend
```

## Architecture approval boundary

The Agent may update normal project/task state when justified. It cannot silently replace accepted architecture. Material architecture drift creates a pending proposal; only explicit human acceptance increments the architecture version.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The real Gemini smoke test runs when a Gemini API key is available. Deterministic tests do not require a model call.