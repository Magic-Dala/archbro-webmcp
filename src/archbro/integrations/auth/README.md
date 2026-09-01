# Trusted identity boundary

This directory describes the provider-neutral identity boundary consumed by the backend authorization layer.

Current provider direction: Firebase Authentication, implemented under `../firebase/` with server-side ID-token verification.

The canonical identity contract is:

```text
verified Firebase UID
    -> TrustedPrincipal(
           user_id=<Firebase UID>,
           team_ids=[],
           local_development=False,
       )
```

The backend extracts the Bearer token and calls the principal provider with only that token. The integration verifies identity; the backend separately decides whether that identity can read or modify a project.

The local-development principal is an explicit development convenience. Shared staging and production environments must inject real authentication and fail closed rather than silently becoming a local demo user.

See [the Firebase security and IAM guide](../firebase/SECURITY.md) for credential, provider, secret, logging, and deployment requirements.
