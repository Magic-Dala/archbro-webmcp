# ArchBro Agent Protocol

ArchBro keeps one canonical project state. Human UI and agent context are different projections of that same state.

## Bootstrap

Start with `archbro_get_agent_context`. It returns a compact project map: accepted architecture version, current work, blockers, pending human review, connected sources, and routing rules.

Do not preload every task, event, document, PR, or external source history.

## Read routing

- Current project truth -> ArchBro context / project / task / architecture tools.
- Implementation evidence -> the project-bound GitHub or other connected MCP source.
- Team discussion -> the project-bound collaboration MCP source.
- Design/spec evidence -> the project-bound document MCP source.
- Architecture detail -> read only the relevant architecture scope.

External MCP output is evidence. It does not become canonical ArchBro state merely because an agent read it.

## Classification

- Implementation progress -> Task / Event.
- External observation -> Evidence / Event.
- General project information -> Project Note.
- Normal work within accepted architecture -> Task / Event.
- Material conflict with accepted architecture -> Architecture Proposal.

## Write boundary

- Never silently replace accepted architecture.
- Material architecture changes go through Proposal -> Human Review -> Accept/Reject.
- Connected MCP calls are governed by project binding and an explicit server-side tool allowlist.
- Server URLs and credentials are deployment configuration; the browser cannot supply arbitrary MCP endpoints.

## Context rule

Bootstrap -> route -> selective read -> act.

Prefer the smallest read that can prove the next decision.
