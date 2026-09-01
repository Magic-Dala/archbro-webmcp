# Firebase / Google Cloud integration

This boundary owns Firebase-specific identity integration while keeping the backend identity contract provider-neutral.

Authentication flow:

```text
Firebase ID token
    -> Firebase verification
    -> provider-neutral TrustedPrincipal
    -> project authorization
```

Owned here:

- Firebase Admin initialization using Google Cloud / Application Default Credentials.
- Firebase ID-token verification and normalized user identity.
- Mapping the verified Firebase UID to Archbro's trusted identity contract.
- Safe translation of Firebase credential and availability failures.
- Authentication security and GCP IAM guidance.

Not owned here:

- Bearer-token extraction, HTTP error responses, or project permissions; those belong to the backend API/authorization boundary.
- Runtime provider construction or deployment secret injection; those belong to platform/runtime.
- Project/Architecture/Task persistence; concrete repositories live under `platform/persistence/`.
- Agent decisions or architecture mutation.
- Raw credentials or service-account JSON.

For the owner-only identity model, the verified Firebase UID is the trusted `user_id`, `team_ids` may be empty, and real Firebase users are never marked as local-development users.

See [SECURITY.md](SECURITY.md) before configuring Firebase credentials, OAuth providers, runtime identity, or production authentication.
