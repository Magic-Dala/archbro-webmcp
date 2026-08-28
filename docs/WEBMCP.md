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

## Core Site Tools

| Tool | Purpose | Boundary |
| --- | --- | --- |
| `archbro_ping` | Verify native WebMCP connectivity without mutation or model use | Read-only |
| `archbro_bootstrap_project` | Save a project goal, host-designed Architecture v1, and initial tasks | Deterministic project/bootstrap APIs |
| `archbro_get_project_brief` | Read fresh project health, tasks, blockers, recent activity, and human attention | Read-only |
| `archbro_get_decision_context` | Read accepted architecture, evidence, execution state, and governance rules | Read-only |
| `archbro_submit_agent_recommendation` | Submit host-agent reasoning; architecture changes become pending human review | Proposal boundary only |
| `archbro_update_task_status` | Start or complete an existing ready task | Deterministic task transition |
| `archbro_focus_pending_review` | Navigate the visible UI to a pending architecture review | UI navigation only |

Connected-MCP bridge tools may be registered only when the product gateway supplies the corresponding bridge methods. They are optional and are not required for the WebMCP Challenge demo path.

## Governance invariants

1. WebMCP recommendations never directly approve architecture changes.
2. A material change creates a `PENDING` proposal; only explicit human acceptance increments the architecture version.
3. Operational incidents alone do not imply architecture drift. The host agent may submit `KEEP_CURRENT` when the accepted boundary still fits.
4. Accepted architecture changes reconcile execution state. Replacement-component tasks are re-scoped to the replacement and become ready when still unfinished; removed-component tasks remain blocked for redefinition.
5. Read tools refresh current backend state before returning decision context so external evidence is not hidden behind stale browser memory.
6. WebMCP does not write persistence directly and does not bypass ArchBro validation or authorization boundaries.

## Competition acceptance path

```text
Natural-language request
  -> host agent discovers ArchBro Site Tools
  -> project bootstrap
  -> external evidence changes project reality
  -> host reads project + decision context
  -> operational-only evidence can KEEP_CURRENT
  -> approved requirement change justifies a PENDING architecture proposal
  -> human accepts in ArchBro
  -> Architecture v2 is applied
  -> tasks are reconciled
  -> host starts the next ready task
```

The local competition-safe mode is available at `/?mode=webmcp`. In this mode the human project-creation flow, built-in architecture generation, built-in agent messaging, and manual task Start/Done controls are disabled so an acceptance run cannot silently fall back to DOM automation. Human architecture Accept/Reject remains enabled.

## Challenge-period history

The original standalone prototype was developed in `archbro-webmcp` beginning during the challenge period. The production implementation and source of truth now live in this repository. The standalone repository is retained only as historical evidence and must not be used as a build-time or runtime dependency.
