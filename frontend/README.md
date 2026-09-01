# Frontend / Product

Owns the human-facing ArchBro experience:

- Goal / Ask
- Living and Code Architecture views
- architecture health, scoped drill-down, and review context
- Task view
- Proposal Review / Needs You
- Project selection/edit/delete
- Context-aware global Ask

`frontend/web/` is served by `platform/runtime`; it communicates with privileged product state through the backend API/WebMCP contracts rather than direct persistence access.
