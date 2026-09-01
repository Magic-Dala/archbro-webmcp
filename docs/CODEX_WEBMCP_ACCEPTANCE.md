# Codex Native WebMCP Acceptance

Use this runbook for black-box acceptance through Archbro's native browser WebMCP / Site Tools surface. The verifier must not use Playwright, shell commands, source inspection, direct HTTP calls, or unrelated MCP tools as a fallback.

## Target

Open a fresh page at:

```text
https://archbro-dev.magicdala.com/?mode=webmcp
```

For local development, the same flow may be run against `http://127.0.0.1:<port>/?mode=webmcp`.

Always perform a fresh navigation or explicit reload before tool discovery. A server restart cannot replace JavaScript already executing in an older page instance.

The `?mode=webmcp` query parameter enables the acceptance-safe UI. It disables UI paths that could let a verifier silently substitute DOM automation for Site Tools while leaving human architecture Accept/Reject available.

## Phase 1 — native host boundary

1. Discover the Site Tools registered by the current page.
2. With no connected MCP gateway configured, require exactly 14 `archbro_*` tools and no legacy public names.
3. Require `archbro_ping` to be present and invoke it through the host Site Tools surface.
4. Do not inspect `document.modelContext` as a prerequisite for invocation.

`archbro_ping` must report the current server-backed identity, including:

- `ok=true`
- `surface=archbro-webmcp`
- `surface_version=archbro.semantic-webmcp.v4`
- `expected_tool_count=14`
- `connected_mcp_gateway_configured=false`
- `stale_client=false`
- `reload_required=false`
- `asset_match=true`
- `built_in_model_called=false`

If the host exposes no native Site Tools, report a host limitation. Do not switch to another testing mechanism.

## Phase 2 — disposable project bootstrap and reads

Use only discovered `archbro_*` tools.

1. `archbro_bootstrap_project`
2. `archbro_get_agent_context`
3. `archbro_get_architecture_diagram`
4. `archbro_get_architecture_node_context`
5. `archbro_find_architecture_path`
6. `archbro_get_architecture_decision_context`

Create a clearly named disposable project with one hierarchical Architecture v1. Before bootstrap, plan SYSTEM_MAP roots, recursively evaluate every scope as EXPANDED or JUSTIFIED_LEAF, then RECONCILE relationships and tasks. The supplied `planning_trace` must cover every canonical component in preorder and match the submitted hierarchy.

Verify:

- Architecture v1 is committed atomically;
- root and scoped projections are coherent;
- node context uses authored dependency direction;
- directed path lookup follows authored relationships;
- no client-side inferred hierarchy is required.

## Phase 3 — deterministic execution and observation

Continue only through native Site Tools:

1. `archbro_create_task` and require a `TODO` task without built-in model invocation.
2. `archbro_update_task_status` through `TODO -> IN_PROGRESS -> DONE`.
3. `archbro_record_project_observation` and verify the accepted Living Architecture version/topology is unchanged.
4. Submit a `KEEP_CURRENT` architecture recommendation when evidence does not justify drift and verify no proposal is created.

## Phase 4 — Code Architecture boundary

Require discovery of:

- `archbro_publish_code_architecture`
- `archbro_get_code_architecture`

For black-box contract acceptance, use a deterministic safe fixture with an exact 40-character revision, repository-relative source paths, matching line ranges/excerpts, and valid evidence references. Do not inspect the repository through another MCP or filesystem tool during this acceptance.

Verify:

- classification is `IMPLEMENTATION_EVIDENCE`;
- the exact revision is preserved;
- provenance fields are durable;
- `repository_checkout_verified` is not upgraded beyond what the backend actually verified;
- publishing Code Architecture does not mutate accepted Living Architecture or tasks.

## Phase 5 — human review and stale-version boundary

1. Call `archbro_expand_architecture_scope` with the current `expected_architecture_version` and verify the result is a `PENDING` proposal rather than an immediate canonical mutation.
2. Verify there is no agent-accessible WebMCP Accept or Reject architecture tool.
3. Exercise the stale-version guard with an outdated `expected_architecture_version` and require rejection without silent rebasing or canonical mutation.

The verifier must never approve or reject architecture on the human's behalf.

## Connected MCP conditionality

The default no-gateway surface contains 14 tools. When a deployment explicitly configures a connected MCP gateway, exactly three additional tools may appear:

- `archbro_list_connected_mcp_servers`
- `archbro_list_connected_mcp_tools`
- `archbro_call_connected_mcp_tool`

That produces 17 tools total. External evidence must never auto-mutate Living Architecture.

## Cleanup

This strict acceptance surface intentionally exposes no project-delete Site Tool. Leave the disposable project clearly named for manual cleanup rather than using a non-WebMCP fallback.

## Final status

Report `READY` only when native discovery, ping identity, bootstrap/read/path, task lifecycle, observation invariant, Code Architecture boundary, human-review boundary, and stale-version guard all pass using WebMCP only.
