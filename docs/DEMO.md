# WebMCP Challenge Demo Script (<3 minutes)

## 0:00–0:20 — Problem

Show Architecture v1: React + FastAPI + PostgreSQL + custom WebSocket.

Voiceover: "Architecture docs go stale because project reality changes faster than humans can maintain them. ArchBro gives humans and agents one living architecture."

## 0:20–0:45 — Native WebMCP

In ChatGPT Work with a WebMCP-capable model, ask naturally what needs attention. Do not name a tool.

Show that ArchBro Site Tools are discovered and used. Emphasize structured Site Tools instead of DOM guessing.

## 0:45–1:15 — Reasoning from reality

First show an operational PostgreSQL health incident. The agent should keep Architecture v1 because an operational failure alone does not justify a boundary change.

Then introduce the approved release constraint: offline-first clients, managed Firebase persistence, automatic synchronization, and no custom realtime persistence channel.

## 1:15–1:50 — Governed architecture change

Ask the agent to review the project and decide whether Architecture v1 still fits.

Expected result:

- `archbro_get_project_brief`
- `archbro_get_decision_context`
- host-agent reasoning
- `archbro_submit_agent_recommendation`
- proposal remains `PENDING`
- `archbro_focus_pending_review`

Show the ArchBro `Needs You` view.

## 1:50–2:15 — Human control

Human clicks `Accept proposed change`.

Show Architecture v2:

- React owns offline caching, synchronization, and snapshot listeners through Firebase SDK.
- Firebase Auth + Cloud Firestore replace PostgreSQL.
- FastAPI remains for privileged operations/integrations via Firebase Admin SDK.
- Custom WebSocket component is removed.

## 2:15–2:40 — Execution continues

Ask the agent to continue from the accepted architecture.

Expected result: the re-scoped Firestore task is ready and transitions from `TODO` to `IN_PROGRESS` through `archbro_update_task_status`.

## 2:40–2:55 — Closing

Voiceover: "ArchBro lets agents observe and reason about project reality, humans govern consequential decisions, and execution stays aligned with the architecture they actually accepted."

## Recording rules

- Keep total runtime below 3 minutes.
- Use the public HTTPS deployment, not localhost, for the final recording.
- Use natural-language prompts; do not expose internal tool names unless briefly showing the Site Tool activity.
- Do not make Connected MCP the main story.
- Record only after the public WebMCP golden acceptance passes end to end.
