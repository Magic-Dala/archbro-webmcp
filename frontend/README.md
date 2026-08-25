# Frontend / Product — Shaun

Owns the human-facing Archbro experience:

- Goal / Ask
- Architecture View / health map / drill-down
- Task View
- Proposal Review / Needs You
- Project selection/edit/delete
- Context-aware global Ask

`frontend/web/` is served by `platform/runtime`; it should communicate with product state only through the backend REST/event contract.
