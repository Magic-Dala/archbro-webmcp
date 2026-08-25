# Persistence — Max

Archbro backend code depends on `ProjectRepositoryPort`; concrete storage belongs here.

- `repository.py` — SQLite implementation for local development and deterministic demos.
- `firestore.py` — Firestore implementation for durable Google Cloud deployment, adapted from the repository pattern proven in Keys by Friday.

Runtime selection:

```text
ARCHBRO_PERSISTENCE=sqlite      -> local SQLite
ARCHBRO_PERSISTENCE=firestore   -> Firebase Admin / Cloud Firestore
```

The frontend never imports the Firestore SDK and Jim's agent/API code never imports this concrete adapter.
