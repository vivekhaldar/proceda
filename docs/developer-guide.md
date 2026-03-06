# Developer Guide

This guide complements `docs/product-spec.md` and `docs/technical-design.md` with practical instructions for contributors.

## Getting started

```bash
pip install -e .
pytest -q
```

## Working with skills

- A skill path can be either:
  - direct path to `SKILL.md`, or
  - directory containing `SKILL.md`.
- See `skillrunner.skills.loader.resolve_skill_path`.
- Parser/lint behavior is implemented in `skillrunner.skills.parser`.

## Runtime execution model

The core runtime is event-driven (`skillrunner.runtime.Runtime`):

1. Create run directory + event log sink.
2. Emit `run.created`, then `run.started`.
3. Iterate steps and emit `step.started` / `step.completed`.
4. Emit `approval.requested` and `approval.responded` around approval gates.
5. Emit assistant messages as `message.assistant`.
6. Emit final run terminal event (`run.completed`, `run.cancelled`, or `run.failed`).

### Event sink composition

`CompositeEventSink` allows attaching multiple consumers (e.g. log sink + queue sink + UI sink).

## Testing strategy

Current tests focus on:

- parser and linter behavior
- loader path resolution
- runtime happy path and approval cancellation path
- event streaming through `Agent.run_stream`
- CLI command behavior (`lint`, `run`, `replay`)

When adding features:

- Add unit tests at module boundaries.
- Add integration tests for end-to-end run flows.
- Keep tests deterministic; avoid network calls.

## Extending the current implementation

### LLM runtime

`skillrunner.llm.runtime.LLMRuntime` is intentionally minimal and deterministic in this baseline. To extend:

- add provider-specific adapters,
- parse tool call responses into `ToolCall`,
- preserve stable event emission for observability.

### MCP integration

`skillrunner.mcp` currently provides protocol-level placeholders. Future work should:

- support stdio + HTTP transports,
- discover tools and validate against skill requirements,
- emit tool invocation lifecycle events.

### TUI

`skillrunner.tui` is scaffolded for future Textual implementation. Keep runtime decoupled from UI internals and consume runtime events rather than mutating session state directly.
