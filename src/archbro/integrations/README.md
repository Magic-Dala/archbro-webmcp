# Integrations

Owns external identity and provider boundaries without leaking provider-specific behavior into the core product model.

- `firebase/` — Firebase Admin, Firebase ID-token verification, Google Cloud identity adapter.
- `auth/` — provider-neutral user/team/permission boundary consumed by the product API.
- `github/` — GitHub API/webhook/repository integration.
- `events/` — normalize provider-specific input into Archbro project signals.

Provider-specific payloads stop here. Integrations may establish trusted identity or emit normalized signals, but must not directly mutate Project, Living Architecture, or Task state.

Project-state persistence is not owned here; concrete persistence adapters live under `platform/persistence/`.
