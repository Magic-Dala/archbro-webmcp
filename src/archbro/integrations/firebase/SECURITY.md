# Firebase Authentication security and IAM guide

Owner: Ayushi (Authentication / Trusted Identity / security guidance)

This document is the deployment contract for Archbro's Firebase Authentication
boundary. It explains the security outcome that runtime and deployment wiring must
provide. It does not create a new IAM framework, grant roles, or modify Max-owned
runtime and deployment code.

## Security boundary

```text
Browser obtains Firebase ID token
    -> backend extracts the Bearer token
    -> Firebase integration verifies the token
    -> integration returns TrustedPrincipal
    -> backend authorizes the principal for one project
```

- Ayushi owns Firebase provider configuration, ID-token verification, and creation
  of a trusted provider-neutral identity.
- Jim owns HTTP authentication error mapping, project authorization, and access
  enforcement.
- Max owns runtime composition, Cloud Run identity, deployed configuration, secret
  injection, and production fail-closed wiring.
- Shaun owns browser Firebase configuration and login UI. The browser must never
  receive a server credential or OAuth client secret.

Authentication proves who the user is. It must not decide project ownership,
membership, or permissions.

## Current implementation versus remaining deployment work

Already present in the repository:

- Firebase Admin initialization accepts an explicit project ID and uses ADC.
- Firebase ID tokens are verified asynchronously and unsafe provider details are
  removed from public errors.
- Jim's backend accepts an async principal provider, extracts the Bearer token, and
  owns the `401` / `403` / `503` boundary.
- Jim's backend deliberately uses a local principal when no provider is supplied.

Still requiring owner-specific implementation or configuration:

- Max must make runtime authentication mode explicit, construct the Firebase
  provider, validate deployed configuration, and prevent deployed local fallback.
- Max must define and attach the runtime service account and deployment identity.
- Ayushi must configure the intended Firebase Authentication providers.
- Shaun must configure the browser for the matching Firebase project and implement
  the login UI/token handoff.

Therefore, merging this guide does not by itself make deployed authentication live.

## Environment modes

| Environment | Identity behavior | Credential source | Required safety rule |
| --- | --- | --- | --- |
| Local development | An explicitly selected local demo principal may be used | Developer's local Application Default Credentials when real Firebase is tested | Never point accidental tests at production |
| Shared demo/staging | Real Firebase authentication | Attached runtime service account through Application Default Credentials | Missing auth configuration must stop startup or make protected requests unavailable |
| Production | Real Firebase authentication only | Attached runtime service account through Application Default Credentials | Never fall back to the local-development principal |

Use separate Firebase projects for development/demo and production when practical.
At minimum, make the selected project explicit and verify it before deployment.

## Firebase and GCP project selection

The browser and backend must agree on the Firebase project for an environment.
For example, a token issued by `archbro-demo` must be verified as an
`archbro-demo` token, not as an `archbro-production` token.

- Set `FIREBASE_PROJECT_ID` to the intended Firebase project identifier.
- Treat a Firebase project ID as configuration, not as a secret.
- Prefer an explicit `FIREBASE_PROJECT_ID` for deployed authentication instead of
  relying on an engineer's current `gcloud` project or an unrelated Firestore
  fallback.
- Review authorized domains, OAuth redirect URLs, and provider settings whenever
  an environment or public hostname changes.
- If Firebase Authentication and Firestore intentionally use different GCP
  projects, document that choice and configure each project separately.

Firebase Admin verifies token signatures and claims such as issuer, audience, and
expiry. A token issued for the wrong Firebase project must be rejected.

## Application Default Credentials

Application Default Credentials (ADC) is Google's standard way for code to obtain
credentials without embedding a private key in the repository.

### Local Mac development

Install and initialize the Google Cloud CLI, then authenticate your own developer
account:

```bash
gcloud auth application-default login
gcloud config set project YOUR_NON_PRODUCTION_PROJECT_ID
```

The first command stores developer credentials in the Google Cloud CLI's local
configuration outside this repository. Do not copy that file into Archbro.

Use a local, ignored `.env` only for environment-specific configuration such as:

```dotenv
FIREBASE_PROJECT_ID=your-non-production-project-id
```

Do not put secrets into `.env.example`. It is a public template and should contain
only variable names, safe defaults, and blank placeholders.

### Cloud Run or another deployed Google Cloud runtime

Attach a dedicated runtime service account to the service. Google Cloud then makes
short-lived credentials available through ADC automatically.

Do not deploy a service-account JSON key, bake one into an image, place one in an
environment variable, or commit one to Git. In normal Google Cloud deployment,
`GOOGLE_APPLICATION_CREDENTIALS` should not point to a downloaded key file.

## Runtime identity versus deployment identity

These identities perform different jobs and should remain separate.

### Runtime service account

The runtime service account is the identity of the running Archbro application. It
should have only the permissions needed while handling requests.

- Do not grant broad `Owner` or `Editor` access.
- Token verification must not be used as a reason to grant project administration
  permissions.
- Firestore access, if the same process needs it, is a separate Max-owned
  persistence permission.
- Grant secret access only to specific secrets the runtime actually reads.
- Do not grant deployment or service-account-administration permissions.

Max must confirm the final service account and exact IAM roles after the Cloud Run
and Firestore topology is selected. This guide intentionally does not guess a broad
role set in advance.

### Deployment identity

The deployment identity is the human or CI/CD service that releases Archbro. It may
need permission to deploy a revision, attach the approved runtime service account,
and configure environment or secret references.

It should not become the application's runtime identity and should not receive
ordinary access to Archbro user or project data merely because it deploys code.

## Firebase Authentication provider administration

Enable providers only in the intended Firebase project and review each provider's
settings before the demo or production deployment.

### Email/password

- Enable the Email/Password provider in Firebase Authentication.
- Use Firebase's password handling; Archbro must never receive or store the user's
  password.
- Configure account recovery and abuse controls appropriate for the environment.

### Google login

- Enable the Google provider in Firebase Authentication.
- Configure the OAuth consent screen and authorized domains for the correct
  environment.
- A browser-facing client ID is configuration; an OAuth client secret is a secret
  and must never be shipped to the browser or committed.

### GitHub login

- This section covers GitHub as a Firebase Authentication provider only.
- Configure the GitHub OAuth application's callback URL exactly as Firebase
  specifies.
- Store the GitHub OAuth client secret in Firebase's provider configuration or an
  approved secret-management system, never in source control or frontend code.
- GitHub App installation, webhooks, repository events, normalization, and pipeline
  delivery are outside this authentication scope and belong to Max for the MVP.

Provider changes are control-plane administration. Limit who can make them, review
changes, and avoid using a shared personal account for production administration.

## Secret and configuration handling

| Value | Classification | Safe location |
| --- | --- | --- |
| Firebase project ID | Non-secret configuration | Environment configuration |
| Firebase browser configuration and web API key | Public client configuration, not proof of authorization | Shaun-owned frontend environment/configuration |
| OAuth client ID | Usually public configuration | Environment-specific configuration |
| OAuth client secret | Secret | Firebase provider configuration or approved secret manager |
| Service-account private key JSON | Long-lived secret; avoid creating it | Do not store in Archbro; prefer attached runtime identity |
| Firebase ID token or refresh token | User credential | Request handling only; never configuration or logs |
| Decoded token claims such as email/name | Sensitive user data | Keep in memory only when required; do not log by default |

If a deployed component must read a secret directly, Max should inject a reference
from the deployment platform and grant access only to the runtime identity that
needs it. Secret values must not appear in deployment manifests, build arguments,
container layers, screenshots, tests, or example files.

A web API key used only for Firebase services generally does not need to be secret,
but it should be restricted to the intended Firebase APIs. Do not reuse a public
Firebase key for Gemini or another billable Google Cloud API.

## Token and log redaction

Never log or include in an exception message:

- the `Authorization` header;
- a Bearer token, Firebase ID token, or refresh token;
- OAuth client secrets;
- service-account private keys;
- full decoded Firebase claims.

Safe diagnostics describe the category rather than the credential. For example:

```text
Safe:   Firebase ID token is invalid.
Unsafe: Invalid token <raw Firebase ID token>
```

User IDs are stable identifiers and should be logged only when operationally
necessary. Do not treat a Firebase UID as a secret, but do treat it as user data.

## Fail-closed production requirements

The current backend supports an intentional local-development path when no
`PrincipalProvider` is injected. That convenience must not become a deployed
fallback.

For shared demo/staging and production, Max-owned runtime wiring must ensure:

1. Firebase authentication is explicitly enabled.
2. A Firebase principal provider is constructed with the intended project ID.
3. Missing or blank Firebase project configuration fails clearly.
4. Provider construction or required credential failure does not select the local
   principal.
5. Protected requests cannot continue as `local-demo` after Firebase fails.
6. Authorization headers and identity-provider exceptions are redacted from logs.

The agreed HTTP behavior remains Jim-owned:

- missing, expired, revoked, malformed, or invalid credentials -> `401`;
- valid identity without project permission -> `403`;
- Firebase or the identity provider unavailable -> `503`.

An outage must produce a visible failure. It must not authenticate the caller as a
demo user and must not return fake success.

## Required owner coordination

Before calling deployed authentication complete:

- **Ayushi:** verify Firebase provider configuration, token verification, trusted
  UID mapping, safe errors, and this security checklist.
- **Jim:** confirm the local-principal path remains development-only and preserve
  the `401` / `403` / `503` authentication-versus-authorization boundary.
- **Max:** confirm the runtime service account, exact least-privilege roles, secret
  references, environment-mode selection, startup validation, and production
  fail-closed behavior.
- **Shaun:** confirm the frontend uses the same environment's Firebase project,
  sends the ID token as a Bearer token, and contains no OAuth client secret or
  server credential.

## Review checklist

- [ ] The Firebase project matches the intended environment.
- [ ] Email/password, Google, and GitHub providers are enabled only where intended.
- [ ] Authorized domains and OAuth callbacks match the deployed hostname.
- [ ] No service-account key file exists in the repository or container image.
- [ ] The runtime and deployment identities are separate and least-privileged.
- [ ] OAuth secrets come from Firebase provider configuration or an approved secret
      manager.
- [ ] Deployed runtime cannot silently use `local-demo`.
- [ ] Invalid credentials fail with `401`; denied project access fails with `403`;
      provider outage fails with `503`.
- [ ] Logs and exceptions contain no Authorization header, token, secret, or full
      claims payload.
- [ ] GitHub webhook/event permissions have not been added to the authentication
      identity by accident.

## Official references

- [How Application Default Credentials works](https://cloud.google.com/docs/authentication/application-default-credentials)
- [ADC with an attached service account](https://cloud.google.com/docs/authentication/set-up-adc-attached-service-account)
- [Cloud Run service identity](https://cloud.google.com/run/docs/securing/service-identity)
- [Firebase API-key guidance](https://firebase.google.com/docs/projects/api-keys)
- [Firebase GitHub sign-in configuration](https://firebase.google.com/docs/auth/web/github-auth)
