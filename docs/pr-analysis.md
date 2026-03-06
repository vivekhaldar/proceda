# PR Implementation Analysis

## Overview

Two alternative implementations of the SkillRunner OSS SDK spec (from `docs/product-spec.md` and `docs/technical-design.md`) were submitted as PRs:

| Dimension | Claude PR | Codex PR |
|---|---|---|
| **Branch** | `claude/implement-docs-features-DZ5Ct` | `codex/implement-documentation-from-docs/-folder` |
| **Size** | ~5,900 lines, 80 files | ~1,400 lines, 48 files |
| **Tests** | 172 test cases | ~9 test cases |
| **External deps** | Typer, LiteLLM, Rich, Textual, httpx, pytest-asyncio | Zero (core), pytest only |
| **CLI framework** | Typer | argparse |
| **LLM integration** | LiteLLM wrapper with response parsing | Deterministic stub |
| **TUI** | Working Textual app (244 lines) | Scaffolded placeholders |
| **MCP clients** | Stdio + HTTP implementations | Protocol stubs |
| **Example skills** | 3 | 2 |

---

## Claude PR — Strengths

1. **Feature completeness.** Near-production v1. The executor (~400 lines) implements the full LLM loop: tool calling, control tools (`complete_step`, `request_clarification`), approval checkpoints, and guard rails (MAX_TOOL_CALL_ITERATIONS=50, MAX_TEXT_ONLY_ITERATIONS=5 with "you seem stuck" hints).

2. **Real MCP clients.** `StdioMCPClient` spawns child processes and speaks JSON-RPC 2.0 over stdin/stdout. `HTTPMCPClient` uses httpx. The orchestrator enforces tool policies (denylist via fnmatch, required_tools allowlist).

3. **Working TUI.** Textual app with 2-pane layout, status bar, keyboard bindings, and asyncio.Future-based approval request/response pattern.

4. **Rich human interface.** Three implementations: `TerminalHumanInterface` (Rich), `AutoApproveHumanInterface`, and `ScriptedHumanInterface`. Also models `ErrorRecoveryRequest`/`ErrorRecoveryDecision`.

5. **Robust configuration.** YAML config files with search paths, environment variable expansion (`${VAR:-default}`), and structured config models.

6. **Comprehensive testing.** 172 tests across 14 test files with pytest-asyncio and integration test markers.

7. **Context management.** Token budget trimming preserving system messages and recent messages.

8. **Detailed event types.** 30+ event types covering the full spec.

## Claude PR — Weaknesses

1. **Heavyweight dependency tree.** Typer, Rich, LiteLLM, Textual, httpx — installation friction for contributors.

2. **Single monolithic commit.** ~5,900 lines in one commit. Hard to review or bisect.

3. **Complexity risk in the executor.** 400-line executor with nested loops and flags, no explicit state machine.

4. **Token trimming is crude.** 4-chars-per-token heuristic will be inaccurate.

5. **Config file discovery is implicit.** Multiple search paths can cause confusion.

6. **TUI tightly coupled to runtime.** If the TUI crashes, the runtime's approval flow hangs.

---

## Codex PR — Strengths

1. **Zero external dependencies.** Maximum portability, trivial installation, no supply-chain risk, clear integration points.

2. **Clean, minimal abstractions.** Every module does one thing. Easy to read and understand completely.

3. **Protocol-first design.** `HumanInterface`, `EventSink`, `LLMRuntime` are all Protocols — pluggable without inheritance.

4. **Correct scaffolding.** Package layout exactly matches the spec. Filling in implementations won't require restructuring.

5. **Deterministic by default.** Runs are fully reproducible without API keys or network access. Excellent for CI and testing.

6. **Developer documentation.** Second commit adds developer guide and runnable examples.

7. **`@dataclass(slots=True)` everywhere.** Memory-efficient, prevents accidental attribute assignment.

## Codex PR — Weaknesses

1. **Not functional as an agent runtime.** LLM and MCP are stubs. Cannot run a skill against a real LLM.

2. **Very thin test coverage.** ~9 tests, happy paths only.

3. **No guard rails.** No iteration limits, stuck-detection, or context trimming.

4. **argparse instead of Typer.** Spec explicitly calls for Typer.

5. **No error recovery flow.** Missing `error.recovery_requested`/`error.recovery_selected`.

6. **Marker syntax differs from spec.** Uses `[requires_approval]` instead of `[APPROVAL REQUIRED]`.

7. **No config file support.** Environment variables only.

---

## Recommendation

**Pick the Claude PR** as the base implementation. It is a working product, not a skeleton.

### Ideas to port from the Codex PR

1. **Zero-dep core architecture.** Restructure so core has no required deps; use optional extras (`skillrunner[cli]`, `skillrunner[tui]`, `skillrunner[llm]`).

2. **`@dataclass(slots=True, frozen=True)`** for immutable models.

3. **Deterministic LLM stub as first-class testing primitive.** A `StubLLMRuntime` for reproducible runs without API keys.

4. **Developer guide and contributor docs.** `docs/developer-guide.md` explaining architecture and extension points.

5. **`discover_skills()` registry function.** Recursive SKILL.md finder for workspace-level operations.

6. **Cleaner event sink naming.** `CompositeEventSink` with `add_sink()` and `EventSink` Protocol.
