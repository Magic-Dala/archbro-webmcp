# Integrations — Ayushi

Owns external identity and source-control boundaries. Archbro reuses the Google Cloud / Firebase foundation already proven in Keys by Friday instead of adding an AWS identity stack.

- `firebase/` — Firebase Admin, Firebase ID-token verification, Google Cloud identity adapter.
- `auth/` — provider-neutral user/team/permission boundary consumed by the product API.
- `github/` — GitHub API/webhook/repository integration.
- `events/` — normalize provider-specific input into Archbro project signals.

Provider-specific payloads stop here. Integrations may establish trusted identity or emit normalized signals, but must not directly mutate Project/Architecture/Task state.

Firestore **project-state persistence is not owned here**; Max owns that adapter under `platform/persistence/`.
