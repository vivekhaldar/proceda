# SkillRunner OSS SDK

SkillRunner is a **procedure-first, local-first SDK** for turning an SOP in `SKILL.md` into a runnable agent with human approvals, structured runtime events, and replayable execution logs.

> This repository follows the product and technical design in `docs/` and is optimized for developer workflows in a terminal environment.

## Why SkillRunner

Most agent tooling starts from prompts or graphs. SkillRunner starts from a procedure:

1. Author a `SKILL.md`
2. Run it locally with `skillrunner run`
3. Review approvals and tool activity
4. Replay the run from `events.jsonl`

## Installation

```bash
pip install -e .
```

## Quickstart

### 1) Lint a skill

```bash
python -m skillrunner.cli.main lint ./examples/expense-approval
```

### 2) Run a skill

```bash
python -m skillrunner.cli.main run ./examples/expense-approval
```

### 3) Replay a run

`run` prints an `event_log=...` path. Replay it:

```bash
python -m skillrunner.cli.main replay .skillrunner/runs/<run-dir>/events.jsonl
```

## SKILL.md format

SkillRunner currently expects:

- YAML frontmatter delimited by `---`
- Required frontmatter fields: `id`, `name`, `description`
- Steps as `## Step <N>: <Title>` headings
- Optional in-step markers:
  - `[requires_approval]`
  - `[requires_post_approval]`
  - `[optional]`

### Minimal example

```md
---
id: expense_approval
name: Expense Approval
description: Validate and approve an expense request
required_tools: ledger.submit, slack.notify
---

## Step 1: Validate [requires_approval]
Check required fields.

## Step 2: Submit [requires_post_approval]
Submit approved request.
```

## Python SDK usage

### Synchronous run

```python
from skillrunner import Agent

agent = Agent.from_path("./examples/expense-approval")
result = agent.run()
print(result.status, result.summary)
print(result.event_log_path)
```

### Async event stream

```python
import asyncio
from skillrunner import Agent

async def main() -> None:
    agent = Agent.from_path("./examples/expense-approval")
    async for event in agent.run_stream():
        print(event.type, event.payload)

asyncio.run(main())
```

## CLI commands

```text
skillrunner run <path>
skillrunner dev <path>
skillrunner lint <path>
skillrunner replay <events.jsonl>
skillrunner doctor
```

- `run`: execute skill in interactive terminal mode and persist run events.
- `dev`: TUI placeholder command in current implementation.
- `lint`: validate SKILL structure and lint rules.
- `replay`: print events from an event log.
- `doctor`: print environment/config diagnostics.

## Run artifacts

By default, runs are stored under:

```text
.skillrunner/runs/<timestamp-run-id>/events.jsonl
```

Each event is JSON and includes `id`, `timestamp`, `run_id`, `type`, and `payload`.

## Repository layout

```text
src/skillrunner/
  agent.py        # public Agent API
  runtime.py      # core run loop + event emission
  skills/         # SKILL loader/parser/linter
  session.py      # run/session/approval models
  events.py       # event model and sink protocol
  store/          # run directory + JSONL event log sink
  cli/            # command routing and subcommands
  llm/            # LLM decision layer (current deterministic stub)
  mcp/            # MCP client/orchestrator abstractions
  tui/            # Textual app placeholders for future expansion
```

## Examples

See `examples/`:

- `examples/expense-approval/SKILL.md`
- `examples/customer-escalation/SKILL.md`

## Developer docs

- `docs/product-spec.md`
- `docs/technical-design.md`
- `docs/developer-guide.md` (added practical implementation notes)

## Running tests

```bash
pytest -q
```
