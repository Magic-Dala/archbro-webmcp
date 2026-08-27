# Core Backend / Agent — Jim

Owns Archbro product semantics and decision logic:

- `api/` — product REST/event contract
- `core/` — Project/Goal/Architecture/Task contracts, action execution, persistence port
- `agent/` — orchestration, drift evaluation, prompts
- `llm/` — model-provider abstraction and Gemini/fake providers

Backend code must not import `platform/runtime`. It depends on `ProjectRepositoryPort` instead of concrete database implementations.

## M4 Drift Evaluation Boundary

For every normal post-architecture Agent run, the model returns a structured `DriftEvaluation` before mutations are applied.

Classifications:

- `ALIGNED`
- `IMPLEMENTATION_ISSUE`
- `ARCHITECTURE_DRIFT`
- `INSUFFICIENT_EVIDENCE`

Flow:

`ProjectEvent → Provider → DriftEvaluation + proposed AgentAction[] → DriftPolicy → ActionExecutor`

`DriftPolicy` is deterministic. It rejects architecture proposals unless the evaluation is `ARCHITECTURE_DRIFT`, and proposal `affected_components` must match the evaluated architecture boundary.

Explicit human task Start/Done transitions remain deterministic and do not require model evaluation.
