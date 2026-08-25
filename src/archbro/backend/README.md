# Core Backend / Agent — Jim

Owns Archbro product semantics and decision logic:

- `api/` — product REST/event contract
- `core/` — Project/Goal/Architecture/Task contracts, action execution, persistence port
- `agent/` — orchestration, drift evaluation, prompts
- `llm/` — model-provider abstraction and Gemini/fake providers

Backend code must not import `platform/runtime`. It depends on `ProjectRepositoryPort` instead of concrete database implementations.
