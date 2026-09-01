# Infrastructure

What runs where, why it is set up this way, and how to rebuild it. Nothing here
is a secret: tokens live in `.env` files on the VM (mode 600, root-owned) and in
Cloudflare, never in this repository.

## Overview

```
                     ┌─ archbro.magicdala.com ──── tunnel: archbro-main ──┐
Cloudflare ──────────┤                                                    ├── GCE VM "magicdala"
                     └─ archbro-dev.magicdala.com                         │
                          └─ Access (email allowlist) ─ tunnel: archbro-dev ┘

VM inbound: 80 closed · 443 closed · 22 open (deploys only)
```

Both environments are reached only through Cloudflare Tunnels. The VM publishes
no HTTP port and carries no `http-server` tag, so nothing on the public internet
can open a connection to the application except through Cloudflare.

## Google Cloud — project `magic-dala`

| Resource | Identifier | Purpose |
| --- | --- | --- |
| Compute instance | `magicdala`, zone `us-central1-a`, e2-medium | Runs both stacks |
| Artifact Registry | `us-central1-docker.pkg.dev/magic-dala/archbro` | Deployment images |
| Workload Identity Pool | `github`, provider `github` | Lets GitHub Actions authenticate without a stored key |
| Service account | `archbro-deployer@magic-dala.iam.gserviceaccount.com` | The identity Actions assumes |

**Deployer roles**, one per thing it actually does:

- `artifactregistry.writer` — push images
- `compute.osAdminLogin` — SSH to the VM to run the deploy
- `iam.serviceAccountUser` — required to SSH to a VM that has a service account

**The OIDC provider is restricted** to `assertion.repository_owner == 'Magic-Dala'`,
and the service account can only be impersonated by `Magic-Dala/archbro`. Another
repository presenting a GitHub token cannot use it.

**The instance has `enable-oslogin=TRUE`.** Without it `gcloud compute ssh` falls
back to instance metadata SSH keys, which requires `compute.instances.setMetadata`
— far broader than `compute.osAdminLogin`. This cost a deploy cycle to discover.

**Registry cleanup policy**: keep the last 10 versions, delete untagged after a
day, delete anything after 30 days. Without it the registry grows past the free
tier. The Dockerfile installs dependencies before copying source, so a deploy
pushes roughly 600KB rather than 155MB.

### Layout on the VM

```
/opt/archbro/                  # root-only, mode 750, because the .env files hold secrets
├── main/          .env  docker-compose.yml
├── dev/           .env  docker-compose.yml
├── cloudflared-main/  .env(TUNNEL_TOKEN)  docker-compose.yml
└── cloudflared-dev/   .env(TUNNEL_TOKEN)  docker-compose.yml
```

`.env` files are placed **by hand** and are never written by the deploy workflow.
`archbro-main` fails closed unless it has the complete production/Firebase
contract (`ARCHBRO_ENV=production`, `ARCHBRO_AUTH_MODE=firebase`, a Firebase
project id, and `ARCHBRO_FIREBASE_API_KEY`). `archbro-dev` may remain explicit
`local/local` staging until Firebase is provisioned; mixed or incomplete
configurations are rejected before the app container is recreated.

Each environment is a separate Compose project (`archbro-main`, `archbro-dev`),
so containers, networks, and database volumes never overlap. The stacks join a
shared external network, `archbro-edge`, which is how the tunnel connectors
reach them without anything being published on the host.

## Cloudflare — zone `magicdala.com`

| Resource | Identifier |
| --- | --- |
| Tunnel (main) | `archbro-main` |
| Tunnel (dev) | `archbro-dev` |
| Access application | `ArchBro dev` → `archbro-dev.magicdala.com` |
| Access policy | team email allowlist |
| Login method | One-time PIN |

DNS is two CNAMEs to `<tunnel-id>.cfargotunnel.com`, both proxied. Proxying is
required: Access only applies to traffic that passes through Cloudflare.

Routing lives in Cloudflare rather than a local config file — the tunnels were
created with `config_src=cloudflare` — so it is changed through the API or the
dashboard, not by editing a file on the VM.

**Two tunnels rather than one**, so a connector failure in dev cannot take
production down with it.

### The bypass that has to stay closed

Cloudflare only guards the path through Cloudflare. While a reverse proxy on the
VM still had a route for `archbro-dev.magicdala.com`, this returned 200 and
walked straight past Access:

```
curl --resolve archbro-dev.magicdala.com:443:<vm-ip> https://archbro-dev.magicdala.com/healthz
```

The fix was removing that route and closing 80/443 entirely. **Any future
change that publishes a port on the VM reopens this hole.** Adding Access in
front of a service is only half the job; the direct path has to disappear.

## Deployment

`main` and `dev` deploy on a push to the matching branch. Anything else is
refused by the workflow.

1. Authenticate to Google via Workload Identity Federation — no stored key
2. Build for `linux/amd64` (pinned: the VM is x86_64) and push two tags
3. Upload the compose file and `deploy-stack.sh` **to the home directory**
4. SSH in and run the script, with a short-lived registry token on stdin
5. Verify `/healthz` from inside `archbro-edge`

Uploads go to the home directory rather than `/tmp` because `/tmp` has the
sticky bit: a file left there by one OS Login account cannot be replaced by
another, and the deploy account differs from any human's.

The registry token is piped over stdin so it never appears in the VM's process
list, and the script logs out afterwards. The VM's own service account scopes
cannot reach Artifact Registry, which is why the token is handed over per
deploy rather than held on the machine.

## Rebuilding from nothing

1. Create the GCE instance; set `enable-oslogin=TRUE`; install Docker and the
   Compose plugin; `docker network create archbro-edge`
2. Create the Artifact Registry repository and apply the cleanup policy
3. Create the Workload Identity Pool and OIDC provider, restricted to the
   repository owner; create the deployer service account with the three roles
   above; bind `workloadIdentityUser` to `attribute.repository/Magic-Dala/archbro`
4. Create one tunnel per environment with `config_src=cloudflare`; set each
   one's ingress to `<hostname> -> http://<stack>-app:8080`; run a connector on
   the VM with the tunnel token in its `.env`
5. Point each hostname at its tunnel with a proxied CNAME
6. For dev: create the Access application and policy, and enable One-time PIN
7. Place `.env` in `/opt/archbro/main` and `/opt/archbro/dev` by hand
8. Push to `main` or `dev`

## Firebase

Token verification works on this VM with no service-account key. The container
reaches the GCE metadata server, and verifying an ID token needs only Google's
public certificates — not any IAM permission on the Firebase project. Measured
on this instance, with the instance's default scopes.

This is worth recording because the opposite is true off Google Cloud: in a
container with no metadata server, `firebase_admin` raises
`DefaultCredentialsError` on verification even though `initialize_app` succeeds.
If deployment ever leaves GCE, token verification has to be reworked to fetch
Google's JWKS directly.
