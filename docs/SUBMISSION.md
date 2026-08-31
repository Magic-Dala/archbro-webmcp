# WebMCP Challenge Submission Draft

## One-line pitch

ArchBro turns project architecture into a shared living workspace where external AI agents can observe real project state through browser-native WebMCP, recommend consequential architecture changes for explicit human approval, and then continue execution from the accepted architecture.

## Problem

Architecture diagrams and implementation plans become stale as code, product requirements, operational incidents, and team execution change. Existing agents usually either guess the UI, maintain a disconnected plan, or mutate project state without a clear human governance boundary.

## What ArchBro enables with WebMCP

ArchBro exposes semantic Site Tools through the imperative `document.modelContext.registerTool()` API. A host agent can create a project, read fresh architecture/task/evidence context, distinguish operational incidents from actual architecture drift, submit a reviewable recommendation, focus the human-review UI, and continue task execution after the human decision.

A proposed architecture change is never auto-approved. The proposal remains `PENDING` until the human accepts or rejects it in ArchBro.

## Challenge-period extension

The original ArchBro product predates the challenge. The browser-native WebMCP extension began during the challenge period. Early adapter history is retained in the standalone `archbro-webmcp` repository; the production source of truth now lives entirely in `Magic-Dala/archbro` and has no runtime dependency on the prototype repository.

## WebMCP leverage

This is not a CRUD wrapper around the website. WebMCP is the interaction boundary that lets the host agent operate on ArchBro's semantic project model without DOM automation while preserving the same validation and human-governance rules as the product.

Core flow:

```text
project reality/evidence
-> host reads ArchBro Site Tools
-> host reasons about architecture drift
-> recommendation becomes PENDING review
-> human Accept / Reject
-> accepted architecture is versioned
-> affected tasks are reconciled
-> host continues execution
```

## Technical highlights

- Imperative browser-native WebMCP via `document.modelContext.registerTool()`.
- Seven high-level semantic Site Tools for connectivity, bootstrap, project context, decision context, recommendation, task execution, and review navigation.
- Host reasoning path reports `built_in_model_called=false`; ArchBro's built-in model is not exposed as a WebMCP tool.
- Fresh backend refresh before project/decision reads prevents stale browser state from hiding new evidence.
- Rich reviewable architecture operations support component replacement/removal/update and relationship replacement.
- Proposal validation happens before persistence so a proposal shown to a human is executable if accepted.
- Explicit human acceptance is required before architecture version changes.
- Accepted architecture changes reconcile affected tasks and produce executable next work.
- PostgreSQL is the persistence backend, locally and in deployment.

## Demonstrated governance behavior

An operational PostgreSQL health-check failure alone was correctly treated as an operational issue and did not justify an architecture change. When the approved release requirement later changed to offline-first clients, Firebase-managed persistence, automatic synchronization, and no custom WebSocket persistence channel, the host agent proposed Architecture v2 for human review. After the human accepted, ArchBro replaced PostgreSQL/custom WebSocket boundaries with Firebase Auth/Cloud Firestore and re-scoped the next persistence task, which the host agent then started.

## Final submission checklist

- [x] Production WebMCP implementation lives in `Magic-Dala/archbro`.
- [x] Standalone adapter is no longer a runtime/build dependency.
- [x] MIT `LICENSE` exists in the submission repository.
- [x] Competition README and WebMCP technical documentation exist.
- [x] Golden governance/execution regression test exists.
- [x] Container deployment configuration exists.
- [x] Public HTTPS live URL: https://archbro-dev.magicdala.com/?mode=webmcp
- [x] The current public host is routed through a dedicated Cloudflare Tunnel; the VM publishes no HTTP/HTTPS application port.
- [x] Direct VM ingress is closed, so the application is reached through the Cloudflare edge boundary.
- [x] Public deployment passed page-level WebMCP registration/execution acceptance and the full governance golden flow through the custom domain.
- [ ] Public deployment passed final ChatGPT-host native Site Tools acceptance on `archbro-dev.magicdala.com`.
- [x] Public GitHub repository verified: https://github.com/Magic-Dala/archbro
- [ ] <3 minute public YouTube demo URL recorded here: **TBD**
- [ ] Devpost form completed with final live/repo/video links.

## Real-host acceptance boundary

The real Codex/ChatGPT host acceptance must distinguish **host tool discovery/invocation** from the page JavaScript registration API.

- Open exactly `https://archbro-dev.magicdala.com/?mode=webmcp` so the competition-safe UI is active.
- If the host discovers `archbro_ping`, Phase 1 must invoke `archbro_ping` directly through the host Site Tools surface.
- `document.modelContext` visibility in page DevTools is **diagnostic only** and must never block a host invocation after Site Tools have already been discovered.
- `data-webmcp-agent-mode` validates only the explicit `?mode=webmcp` UI mode; it is not evidence for or against host Site Tools transport.
- `REAL_HOST_BLOCKED` is valid only when the host cannot discover or invoke `archbro_ping`, permission is denied, invocation fails, or Site Tools are lost after navigation.
- A host that discovers and successfully invokes `archbro_ping` has crossed the real-host boundary and must continue the acceptance flow.

See `docs/CODEX_WEBMCP_ACCEPTANCE.md` for the exact executable sequence.

## Final acceptance prompt sequence

1. Create a collaborative issue-tracking project in ArchBro with React, FastAPI, PostgreSQL, and realtime collaboration. Do not name tools.
2. Review the current ArchBro project and determine whether the accepted architecture still satisfies the latest project requirements and evidence. If an architecture change is justified, submit the recommendation and take me to human review. Do not approve or reject anything for me.
3. After the human accepts: re-read the project, summarize Architecture v2, choose the most relevant ready task, and start it. Do not make another architecture decision.

The final recording must use `https://archbro-dev.magicdala.com/?mode=webmcp`, not localhost or a direct VM/container path.
