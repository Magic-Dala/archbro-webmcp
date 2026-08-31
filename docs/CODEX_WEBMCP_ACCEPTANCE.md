# Codex Real WebMCP Acceptance

Use this runbook only for real Codex/ChatGPT Site Tools acceptance. It intentionally does **not** use the Playwright `document.modelContext` shim from the page-level integration probes.

## Target

Open exactly:

```text
https://archbro-dev.magicdala.com/?mode=webmcp
```

The `?mode=webmcp` query parameter enables ArchBro's competition-safe UI. `data-webmcp-agent-mode="true"` proves only that this UI mode is active; it does not prove or disprove host Site Tools transport.

## Phase 1 — real host boundary

1. Ask the Codex/ChatGPT host to list the Site Tools it discovered for the current page.
2. Require `archbro_ping` to be present.
3. Immediately invoke `archbro_ping` through the host Site Tools surface.
4. Do **not** inspect `document.modelContext` as a prerequisite for step 3.

`document.modelContext` is the page-side registration API. Its visibility from page DevTools is diagnostic only once the host has already discovered tools. A host that can discover and invoke `archbro_ping` has crossed the real-host boundary.

Phase 1 passes when `archbro_ping` returns successfully.

Use `REAL_HOST_BLOCKED` only when one of these concrete failures occurs:

- `archbro_ping` is not discovered;
- the host exposes no invocation path for the discovered tool;
- Site Tools permission is denied;
- `archbro_ping` invocation throws or returns a transport/tool failure;
- Site Tools disappear after navigation or reload.

Do **not** use `REAL_HOST_BLOCKED` merely because page JavaScript reports `document.modelContext` or `registerTool` as unavailable.

## Phase 2 — deterministic project bootstrap

After Phase 1 passes, invoke the host-discovered ArchBro tools in this order:

1. `archbro_bootstrap_project`
2. `archbro_get_agent_context`
3. `archbro_get_project_brief`
4. `archbro_get_decision_context`

Create only a throwaway acceptance project. Record its project id for cleanup.

## Phase 3 — execution and governance

Continue with:

1. `archbro_update_task_status` on one valid ready task.
2. `archbro_submit_agent_recommendation` with `KEEP_CURRENT` for operational-only evidence and verify no architecture proposal is created.
3. Submit a material architecture-change recommendation and verify it creates a `PENDING` proposal rather than self-approving.
4. `archbro_focus_pending_review` and verify human Accept/Reject remains the approval boundary.
5. After explicit human acceptance, re-read project and decision context and verify the accepted architecture/version and reconciled task state.

The host agent must never approve or reject architecture on the human's behalf.

## Phase 4 — connected MCP surface

If the corresponding tools are discovered, invoke:

1. `archbro_list_connected_mcp_servers`
2. `archbro_list_connected_mcp_tools`
3. `archbro_call_connected_mcp_tool` only with an allowlisted safe read operation suitable for acceptance.

Connected MCP evidence is external evidence and must not automatically mutate ArchBro canonical architecture.

## Cleanup

Delete the throwaway acceptance project. Do not modify source, Git state, or unrelated existing projects.

## Final status

Report `REAL_HOST_PASS` only after Phase 1 host invocation succeeds and the required downstream acceptance phases complete.

If blocked, report the **first concrete host invocation failure**, not page-side API visibility.
