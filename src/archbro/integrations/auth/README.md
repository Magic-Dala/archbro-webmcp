# Trusted identity boundary — Ayushi

This directory describes the provider-neutral identity boundary consumed by Jim's
backend authorization layer.

Current provider direction: Firebase Authentication, implemented under
`../firebase/` using the same server-side ID-token verification pattern already
exercised in Keys by Friday.

The canonical MVP identity contract is:

```text
verified Firebase UID
    -> TrustedPrincipal(
           user_id=<Firebase UID>,
           team_ids=[],
           local_development=False,
       )
```

The backend extracts the Bearer token and calls the async principal provider with
only that token. The integration verifies identity; Jim's backend separately decides
whether that identity can read or modify a project.

The local-development principal is an explicit development convenience. Shared
demo/staging and production environments must inject real authentication and must
fail closed rather than silently becoming the local demo user.

See [the Firebase security and IAM guide](../firebase/SECURITY.md) for credential,
provider, secret, logging, and deployment requirements.
