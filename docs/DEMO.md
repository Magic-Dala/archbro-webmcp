# Archbro WebMCP Demo Script (<3 minutes)

## 0:00–0:20 — Problem

Show a project with an accepted Living Architecture and active tasks.

Voiceover: "Architecture gets stale when project reality changes faster than the documentation. Archbro gives humans and agents one governed living architecture instead of separate plans."

## 0:20–0:45 — Native WebMCP

Open the production WebMCP acceptance URL and let a WebMCP-capable host discover Archbro's semantic Site Tools. Do not name tools in the user prompt.

Show that the host reads project state through structured Site Tools rather than DOM guessing.

## 0:45–1:15 — Architecture context

Ask the agent to inspect the current architecture and dependency context.

Expected behavior:

- read the accepted Living Architecture;
- drill one backend-authored scope;
- inspect node context or a directed authored path;
- explain current architecture without inventing topology.

## 1:15–1:45 — Execution and evidence

Ask the agent to create or continue one normal implementation task and record one operational observation.

Expected behavior:

- task execution uses deterministic task tools without built-in model invocation;
- the observation becomes durable evidence;
- the observation does not silently mutate Living Architecture.

## 1:45–2:15 — Living vs Code Architecture

Show the separate **Living** and **Code** Architecture views.

Explain that Living Architecture is human-approved design intent, while Code Architecture is revision-pinned implementation evidence. Publishing Code Architecture must not change the accepted Living Architecture version or topology.

## 2:15–2:40 — Human-governed change

Ask the agent to propose one justified structural architecture change.

Expected behavior:

- the recommendation becomes `PENDING`;
- the current accepted architecture remains unchanged;
- there is no agent-accessible WebMCP Accept/Reject tool.

The human may then review and accept or reject the proposal directly in Archbro.

## 2:40–2:55 — Closing

Voiceover: "Archbro lets agents observe project reality and continue execution, while humans keep control of consequential architecture decisions."

## Recording rules

- Keep total runtime below 3 minutes.
- Use the designated public WebMCP deployment for the recording.
- Use natural-language prompts; show tool activity only briefly when useful.
- Do not substitute DOM automation, Playwright, shell commands, or unrelated MCP tools for Archbro's native WebMCP surface.
- Record only after the native WebMCP acceptance flow passes end to end.
