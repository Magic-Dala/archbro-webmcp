# Firebase / Google Cloud integration — Ayushi

This boundary intentionally reuses the proven Keys by Friday Firebase pattern
instead of introducing AWS identity services.

Authentication flow:

```text
Firebase ID token
    -> Firebase verification
    -> provider-neutral TrustedPrincipal
    -> Jim-owned project authorization
```

Owned here:

- Firebase Admin initialization using Google Cloud / Application Default
  Credentials.
- Firebase ID-token verification and normalized user identity.
- Mapping the verified Firebase UID to Archbro's trusted identity contract.
- Safe translation of Firebase credential and availability failures.
- Authentication security and GCP IAM guidance.

Not owned here:

- Bearer-token extraction, HTTP error responses, or project permissions. Those are
  Jim-owned backend responsibilities.
- Runtime provider construction, Cloud Run service identity, or deployment secret
  injection. Those are Max-owned platform responsibilities.
- Project/Architecture/Task persistence. The concrete Firestore repository is
  Max-owned under `platform/persistence/`.
- Agent decisions or architecture mutation.
- Raw credentials or service-account JSON.

For the first owner-only MVP, the verified Firebase UID is the trusted `user_id`,
`team_ids` is empty, and real Firebase users are never marked as local-development
users.

See [SECURITY.md](SECURITY.md) before configuring a Firebase project, local Google
credentials, OAuth providers, Cloud Run identity, or production authentication.

The implementation is adapted from the KBF `backend/app/firebase.py` and
`backend/app/auth.py` patterns.
