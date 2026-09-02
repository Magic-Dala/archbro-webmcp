# Firebase Authentication security and IAM guide

This document defines the security contract for Archbro's Firebase Authentication boundary. It describes required outcomes for identity verification, runtime configuration, authorization, secrets, and production fail-closed behavior without assigning responsibilities to individual people.

## Security boundary

```text
Browser obtains Firebase ID token
    -> backend extracts Bearer token
    -> Firebase integration verifies token
    -> integration returns TrustedPrincipal
    -> backend authorizes principal for one project
```

Responsibilities are separated by boundary:

- **Identity integration** verifies Firebase tokens and returns provider-neutral trusted identity.
- **Backend API/authorization** maps authentication failures and enforces project permissions.
- **Platform/runtime** selects environment mode, runtime identity, deployed configuration, and secret injection.
- **Frontend** uses public Firebase browser configuration and passes ID tokens; it must never receive server credentials or OAuth client secrets.

Authentication proves who the user is. It does not by itself grant project ownership, membership, or permissions.

## Current implementation versus remaining deployment work

Already present in the repository:

- Firebase Admin initialization accepts an explicit project ID and uses ADC.
- Firebase ID tokens are verified asynchronously and unsafe provider details are
  removed from public errors.
- The backend accepts an async principal provider, extracts the Bearer token, and
  preserves the `401` / `403` / `503` boundary.
- The runtime makes authentication mode explicit, constructs the Firebase
  provider, publishes only public browser configuration, and prevents a deployed
  local-principal fallback.
- The browser supports Firebase email/password account creation, sign-in, session
  restoration, ID-token handoff, and sign-out. Its local UI profile is keyed by
  the verified Firebase UID but is never treated as authorization evidence.
- Persisted anonymous browser sessions are cleared, and the trusted-principal
  adapter rejects anonymous Firebase tokens before backend authorization.
- Firebase-backed WebMCP visits require a real Firebase login, then continue
  directly into the authenticated agent workspace. Local WebMCP mode is unchanged.

Still requiring owner-specific implementation or configuration:

- The runtime service account and deployment identity must be configured for the
  intended environment.
- Each intended Firebase Authentication provider must be enabled in the correct
  project and its security settings reviewed.
- Google and GitHub login are wired through Firebase in the browser and reuse the
  existing ID-token verification and trusted-principal boundary. Each environment
  still needs the canonical identity setup run with that environment's authorized
  domains and OAuth credentials.
- Google and GitHub popup login fails closed with a configuration error when the
  public Firebase `authDomain` is absent.

Therefore, merging this guide does not by itself make deployed authentication live.

## Environment modes

| Environment | Identity behavior | Credential source | Safety rule |
| --- | --- | --- | --- |
| Local development | Explicit local principal may be used | Local ADC when real Firebase is tested | Never point accidental tests at production |
| Shared staging | Real Firebase authentication | Runtime identity through ADC | Missing authentication configuration must fail closed |
| Production | Real Firebase authentication only | Runtime identity through ADC | Never fall back to the local-development principal |

Use separate Firebase projects for environments when practical. At minimum, select the project explicitly and verify it before deployment.

## Firebase project selection

The browser and backend must agree on the Firebase project for an environment.

- Set `FIREBASE_PROJECT_ID` explicitly for deployed authentication.
- Treat the project ID as configuration, not a secret.
- Review authorized domains, OAuth redirect URLs, and provider settings whenever a hostname changes.
- If Firebase Authentication and persistence intentionally use different Google Cloud projects, document and configure each independently.

Firebase Admin validates token signatures and claims such as issuer, audience, and expiry. Tokens from the wrong Firebase project must be rejected.

## Application Default Credentials

Application Default Credentials (ADC) let Archbro authenticate to Google services without embedding a private key in the repository.

### Local development

```bash
gcloud auth application-default login
gcloud config set project YOUR_NON_PRODUCTION_PROJECT_ID
```

Developer credentials remain outside the repository. Use an ignored local `.env` for environment-specific configuration such as:

```dotenv
FIREBASE_PROJECT_ID=your-non-production-project-id
```

Do not put secrets in `.env.example`.

### Deployed Google Cloud runtime

Attach an appropriate runtime service account to the deployed workload. On the current GCE deployment, ADC is supplied by the instance/runtime identity through Google Cloud metadata rather than by a downloaded key file.

Do not deploy service-account JSON, bake credentials into images, place private keys in environment variables, or commit them to Git.

## Runtime identity versus deployment identity

These identities perform different jobs and should remain separate.

### Runtime identity

The runtime identity should receive only permissions needed while serving Archbro requests.

- Do not grant broad `Owner` or `Editor` roles.
- Token verification is not a reason to grant project-administration permissions.
- Persistence access is a separate permission from identity verification.
- Secret access should be limited to specific secrets the runtime actually reads.
- Do not grant deployment or service-account-administration permissions to the runtime.

### Deployment identity

The deployment identity is the human or CI/CD principal that releases Archbro. It may need permission to deploy a revision, attach the approved runtime identity, and configure environment or secret references.

It should not become the application's runtime identity and should not gain ordinary access to user/project data merely because it deploys code.

## Firebase Authentication providers

Enable only the providers required by the intended environment and review their settings before production use.

The canonical setup command is `qa/setup_archbro_identity_platform.ps1`. It requires
an explicit Firebase `-AuthDomain`, enables email/password, disables anonymous login,
and creates or updates the Google and GitHub provider records through the Identity
Platform Admin API. It reads the OAuth credentials from setup-only process
environment variables:

```text
ARCHBRO_FIREBASE_GOOGLE_OAUTH_CLIENT_ID
ARCHBRO_FIREBASE_GOOGLE_OAUTH_CLIENT_SECRET
ARCHBRO_FIREBASE_GITHUB_OAUTH_CLIENT_ID
ARCHBRO_FIREBASE_GITHUB_OAUTH_CLIENT_SECRET
```

Populate those values from an approved secret store immediately before setup. They
must not be added to frontend runtime configuration, `.env.example`, command-line
arguments, logs, or the generated `.archbro-firebase-public.json`. The generated
file contains only public browser configuration and must be translated into the
deployment values: `projectId` to `FIREBASE_PROJECT_ID`, `apiKey` to
`ARCHBRO_FIREBASE_API_KEY`, and `authDomain` to
`ARCHBRO_FIREBASE_AUTH_DOMAIN`.

### Email/password

- Let Firebase handle passwords; Archbro must never receive or store user passwords.
- Configure account recovery and abuse controls appropriate for the environment.

### Google login

- Configure the OAuth consent screen and authorized domains for the correct environment.
- Configure the public Firebase `authDomain` used by the popup flow.
- The browser uses Firebase's Google popup and passes only the resulting Firebase
  ID token to Archbro. The verified Firebase UID remains canonical.
- Browser-facing client IDs are configuration; OAuth client secrets are secrets and must never be shipped to the browser.

### GitHub login

- GitHub as a Firebase Authentication provider is separate from Archbro's GitHub repository/event integration.
- Configure the public Firebase `authDomain` used by the popup flow.
- The browser uses Firebase's GitHub popup and passes only the resulting Firebase
  ID token to Archbro. A GitHub username or OAuth access token is never used as
  `TrustedPrincipal.user_id`.
- Do not request `repo` or other repository permissions for ordinary login.
- Configure the callback URL exactly as Firebase requires.
- Store the OAuth client secret in Firebase/provider configuration or an approved secret manager, never source or frontend code.

## Secret and configuration handling

| Value | Classification | Safe location |
| --- | --- | --- |
| Firebase project ID | Non-secret configuration | Environment configuration |
| Firebase browser config / web API key | Public client configuration, not authorization proof | Frontend runtime configuration |
| OAuth client ID | Usually public configuration | Environment-specific configuration |
| OAuth client secret | Secret | Provider configuration or approved secret manager |
| Service-account private key JSON | Long-lived secret; avoid creating | Do not store in Archbro |
| Firebase ID/refresh token | User credential | Request handling only |
| Decoded claims such as email/name | Sensitive user data | Memory only when required; do not log by default |

A Firebase browser API key is generally public configuration, but restrict it to the intended Firebase APIs. Do not reuse it for unrelated billable APIs.

## Token and log redaction

Never log or include in exception messages:

- `Authorization` headers;
- Bearer, Firebase ID, or refresh tokens;
- OAuth client secrets;
- service-account private keys;
- full decoded Firebase claims.

Prefer category-level diagnostics:

```text
Safe:   Firebase ID token is invalid.
Unsafe: Invalid token <raw token>
```

## Fail-closed production requirements

The local principal is a development convenience only. Shared staging and production must ensure:

1. Firebase authentication is explicitly enabled.
2. The principal provider uses the intended Firebase project.
3. Missing or blank Firebase project configuration fails clearly.
4. Credential/provider initialization failure never selects the local principal.
5. Protected requests cannot continue as `local-demo` after Firebase fails.
6. Authentication headers, credentials, and provider exceptions are redacted from logs.

Expected HTTP behavior:

- missing, expired, revoked, malformed, or invalid credentials -> `401`;
- valid identity without project permission -> `403`;
- Firebase/identity provider unavailable -> `503`.

An identity outage must be visible. It must not become fake success or local-demo authentication.

## Review checklist

- [ ] Firebase project matches the intended environment.
- [ ] Only intended authentication providers are enabled.
- [ ] Authorized domains and OAuth callbacks match deployed hostnames.
- [ ] No service-account key exists in the repository or image.
- [ ] Runtime and deployment identities are separate and least-privileged.
- [ ] OAuth secrets come from provider configuration or an approved secret manager.
- [ ] Deployed runtime cannot silently use `local-demo`.
- [ ] Invalid credentials return `401`; denied access returns `403`; provider outage returns `503`.
- [ ] Logs contain no authorization header, token, secret, or full claims payload.
- [ ] Repository/event integration permissions have not leaked into the authentication identity.

## Official reference topics

- Application Default Credentials behavior
- Application Default Credentials with an attached service account
- Firebase API-key guidance
- Firebase GitHub sign-in configuration
