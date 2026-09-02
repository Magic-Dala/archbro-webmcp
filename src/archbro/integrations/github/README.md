# GitHub boundary

Owned by Max. Verify provider input, convert GitHub-specific payloads into
normalized external events, and hand them to the platform pipeline. GitHub
handlers must not directly mutate Archbro Project, Living Architecture, or Task
state.

`adapter.py` holds the pull-based commit adapter. It reads through the connected
MCP gateway rather than calling the GitHub API directly, so credentials and
transport stay deployment configuration.

Replay keys qualify the repository by its **numeric GitHub id**, never
`owner/name`. The name is case-insensitive to GitHub and changes on rename or
transfer, so using it would let one commit arrive under two identities and
silently defeat deduplication. The readable name is carried in the payload.

Connecting a repository takes a **baseline**, not a backfill: the first pass
records where watching starts and delivers nothing. Replaying history would
evaluate commits against an architecture that did not exist when they were
made, and would spend a model call on each one.

## Scope: operator-configured, not user-connected

This pipeline watches the repositories listed in `ARCHBRO_GITHUB_CONNECTORS_JSON`,
placed on the host by hand, and reads them with the deployment credential named
by the MCP server's `auth_token_env`. **It is not driven by the per-user GitHub
OAuth connection in the app**, which lives in `backend/mcp/provider_gateway.py`,
is bound to a `TrustedPrincipal`, and is held in the app process's memory.

The two are kept apart deliberately, and a test enforces it: the connector
worker runs in its own container and cannot reach a session held in another
process, so reaching for the per-user gateway here would either fail
confusingly or read one person's repositories on another's behalf.

The consequence is a real limit. People cannot connect their own repositories to
this pipeline today; an operator chooses what is watched. Making it
user-connected requires the owner-scoped credential boundary to land first —
persisting or relaying provider sessions across processes, which is a backend
security design, not an adapter change.
