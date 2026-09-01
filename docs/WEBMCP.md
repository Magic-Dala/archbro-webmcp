# ArchBro WebMCP

ArchBro exposes a browser-native imperative WebMCP surface so an external host agent can operate on the same living project state as humans without guessing the DOM or bypassing product governance.

## Architecture

```text
ChatGPT / WebMCP-aware host agent
          |
          v
document.modelContext.registerTool(...)
          |
          v
ArchBro semantic Site Tools
          |
          v
window.ArchBroWebBridge
          |
          v
ArchBro API / ActionExecutor
          |
          +--> project + task state
          +--> architecture proposals
          +--> human review boundary
```

The calling host agent owns reasoning. ArchBro owns state, validation, governance, and deterministic execution. ArchBro's built-in model remains available for direct human UI flows and is not exposed as a WebMCP tool.

## Default semantic Site Tools

ArchBro exposes **14 tools by default**. Connected-MCP gateway tools are a separate optional module and are registered only when the deployment actually configures at least one external MCP server.

| Tool | Purpose | Boundary |
| --- | --- | --- |
| `archbro_ping` | Verify native WebMCP connectivity without mutation or model use | Read-only |
| `archbro_get_agent_context` | Read compact project/agent context and connected sources | Read-only |
| `archbro_get_architecture_diagram` | Read the backend-authored root or one Living Architecture subsystem projection, including PRIMARY/CONTEXT nodes, provenance, and positioned graph | Read-only |
| `archbro_get_architecture_node_context` | Read bounded upstream/downstream canonical dependency context for a stable `node:<component_id>` | Read-only |
| `archbro_find_architecture_path` | Find a deterministic directed path using authored architecture relationships | Read-only |
| `archbro_bootstrap_project` | Atomically save the final hierarchical Architecture v1 only after the host records SYSTEM_MAP roots, one EXPAND_SCOPE pass per root, and RECONCILE in `planning_trace` | Deterministic project/bootstrap APIs; trace must match final topology |
| `archbro_expand_architecture_scope` | Propose one additive child level under an existing canonical component; grandchildren are expanded in a later call | `PENDING` proposal / human approval |
| `archbro_get_architecture_decision_context` | Read accepted Living Architecture, execution state, evidence, pending review, and governance rules | Read-only |
| `archbro_submit_architecture_recommendation` | Submit architecture-specific host reasoning; architecture changes become pending human review | Proposal boundary only |
| `archbro_publish_code_architecture` | Validate and persist revision-pinned implementation evidence as the latest Code Architecture artifact | Derived artifact write; no canonical architecture mutation |
| `archbro_get_code_architecture` | Read the latest persisted Code Architecture implementation-evidence artifact | Read-only |
| `archbro_create_task` | Create normal post-bootstrap execution work within accepted architecture | Deterministic task creation; no model invocation |
| `archbro_update_task_status` | Start or complete an existing ready task | Deterministic task transition; no model invocation |
| `archbro_record_project_observation` | Persist external evidence or a project fact without classifying it as an architecture recommendation | Event write only; no model or canonical architecture mutation |

When an external MCP gateway is configured, ArchBro adds exactly three tools: `archbro_list_connected_mcp_servers`, `archbro_list_connected_mcp_tools`, and `archbro_call_connected_mcp_tool`, for **17 total**. With no configured gateway those three tools are absent rather than returning empty discovery noise.

## Governance invariants

1. WebMCP recommendations never directly approve architecture changes.
2. A material change creates a `PENDING` proposal; only explicit human acceptance increments the architecture version.
3. Operational incidents alone do not imply architecture drift. The host agent may submit `KEEP_CURRENT` when the accepted boundary still fits.
4. Accepted architecture changes reconcile execution state. Replacement-component tasks are re-scoped to the replacement and become ready when still unfinished; removed-component tasks remain blocked for redefinition.
5. Read tools refresh current backend state before returning decision context so external evidence is not hidden behind stale browser memory.
6. WebMCP does not write canonical persistence directly and does not bypass ArchBro validation or authorization boundaries. A Code Architecture publish uses an authorized API to persist a derived evidence artifact only.
7. `update_component` remains metadata-only. Structural decomposition uses `archbro_expand_architecture_scope`, which is additive, one-level-at-a-time, stable-ID preserving, and reviewable before acceptance.
8. The host must use `archbro_get_architecture_diagram` for hierarchical drill-down. It must not infer, crop, or manufacture child topology from a full-tree browser snapshot.
9. Code Architecture is not Living Architecture. Code nodes use `code-node:*`, require exact 40-character Git revision provenance plus source excerpts, and cannot alter accepted `node:<component_id>` topology or version.
10. Before publishing Code Architecture, the host must inspect the connected GitHub repository at the exact revision. File names, folder proximity, or an unpinned branch HEAD are insufficient implementation evidence.
11. Initial WebMCP planning is progressive even though persistence is atomic: the host must first choose stable SYSTEM_MAP root ids, then evaluate/expand every root against that draft, then reconcile authored relationships and initial tasks. `planning_trace` must exactly match the submitted hierarchy; post-bootstrap structural changes still use reviewable `archbro_expand_architecture_scope` proposals.

## Living Architecture vs Code Architecture

```text
Living Architecture                     Code Architecture
human-approved design intent            implementation evidence
node:<component_id>                     code-node:<snapshot component id>
versioned by human acceptance            pinned to exact Git commit SHA
scoped backend drill-down                deterministic evidence graph
may change tasks after acceptance        never changes canonical tasks/state
```

`archbro_publish_code_architecture` validates the complete evidence payload before the route persists anything, so a separate public preview tool is unnecessary. `archbro_get_code_architecture` lets the UI or a later agent session recover the latest artifact. If implementation evidence proves the accepted design is wrong, the host must separately call `archbro_submit_architecture_recommendation` and wait for human acceptance.

## Native WebMCP acceptance path

```text
Natural-language request
  -> host agent discovers ArchBro Site Tools
  -> SYSTEM_MAP roots
  -> EXPAND_SCOPE each root in draft
  -> RECONCILE relationships + tasks
  -> one atomic hierarchical project bootstrap
  -> scoped diagram / dependency reads
  -> optional one-level scope expansion proposal
  -> human accepts structural expansion
  -> external evidence changes project reality
  -> host reads project + decision context
  -> operational-only evidence can KEEP_CURRENT
  -> approved requirement change justifies a PENDING architecture proposal
  -> human accepts in ArchBro
  -> Architecture v2 is applied
  -> tasks are reconciled
  -> host starts the next ready task
```

The acceptance-safe mode is available at `/?mode=webmcp`. In this mode the human project-creation flow, built-in architecture generation, built-in agent messaging, and manual task Start/Done controls are disabled so an acceptance run cannot silently fall back to DOM automation. Human architecture Accept/Reject remains enabled.

For normal production use, Code Architecture evidence should come from source inspected at the pinned revision. A deterministic synthetic evidence fixture is acceptable only for black-box contract acceptance where the verifier is intentionally restricted to ArchBro's native WebMCP surface.
