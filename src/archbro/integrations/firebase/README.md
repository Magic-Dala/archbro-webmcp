# Firebase / Google Cloud integration — Ayushi

This boundary intentionally reuses the proven Keys by Friday Firebase pattern instead of introducing AWS identity services.

Owned here:

- Firebase Admin initialization using Google Cloud / Application Default Credentials.
- Firebase ID-token verification and normalized user identity.
- User/team identity and permission adapters as the Archbro ownership contract grows.

Not owned here:

- Project/Architecture/Task persistence. The concrete Firestore repository is Max-owned under `platform/persistence/`.
- Agent decisions or architecture mutation.
- Raw credentials or service-account JSON.

The implementation is adapted from the KBF `backend/app/firebase.py` and `backend/app/auth.py` patterns.
