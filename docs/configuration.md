# Configuration Reference

Proceda is configured via `proceda.yaml`. The file is searched in order:

1. `./proceda.yaml` (or `proceda.yml`)
2. `~/.config/proceda/proceda.yaml`

If no config file is found, defaults are used.

## Environment variable expansion

All string values support `${VAR}` and `${VAR:-default}` syntax:

```yaml
apps:
  - name: my-app
    env:
      API_KEY: ${MY_API_KEY}
      REGION: ${AWS_REGION:-us-east-1}
```

## Full reference

```yaml
llm:
  model: anthropic/claude-sonnet-4-20250514  # Any LiteLLM-supported model
  api_key_env: ANTHROPIC_API_KEY             # Env var containing the API key
  temperature: 0.7
  max_tokens: 4096
  max_retries: 3                             # Retry count for transient LLM errors

apps:                                        # MCP tool servers
  - name: my-tools                           # Used as tool name prefix (my-tools__tool_name)
    description: What this server provides
    transport: stdio                         # "stdio" or "http"
    command: ["path/to/server"]              # For stdio: command + args to launch
    # url: http://localhost:8080             # For http: server URL
    env:                                     # Environment variables for the server process
      API_KEY: ${MY_API_KEY}

dev:
  show_reasoning: true                       # Show LLM reasoning in dev/TUI mode
  log_mcp: true                              # Log MCP tool calls

security:
  tool_denylist:                             # Tools the agent is never allowed to call
    - dangerous_tool

logging:
  run_dir: .proceda/runs                     # Where run event logs are stored
  redact_secrets: true                       # Redact API keys in event logs
```

## Sections

### `llm`

Controls the LLM provider. Proceda uses [LiteLLM](https://docs.litellm.ai/) under the hood, so any LiteLLM model string works (e.g., `openai/gpt-4o`, `anthropic/claude-sonnet-4-20250514`, `ollama_chat/llama3`).

### `apps`

Each entry defines an MCP tool server. The `name` becomes the tool prefix — a server named `fs` exposing a tool `read_file` creates the tool `fs__read_file`.

**Stdio transport** launches the server as a subprocess. The `command` array is the argv.

**HTTP transport** connects to a running server at the given `url`.

### `security`

The `tool_denylist` blocks specific tools from being called, regardless of what the LLM requests. Useful for preventing destructive operations.

### `logging`

Run logs are JSONL event streams stored in `run_dir`. Each run gets a timestamped subdirectory. Use `proceda replay` to inspect them.
## Optional decision-model cascades

Decision models can handle bounded checks inside the existing natural-language
execution loop. No changes to a skill's Markdown are required. The feature is
disabled by default.

Backends are attempted in the configured order. Each may set its own `threshold`
to override the shared decision threshold. An uncertain or unavailable stage
continues to the next configured backend; exhausted completion checks return to
the ordinary LLM loop. A local-only backend list therefore has no hidden hosted
decision call.

```yaml
llm:
  model: openai/zai-org/GLM-5.3-Flash
  api_base: https://api.tokenfactory.nebius.com/v1
  api_key_env: NEBIUS_API_KEY
  temperature: 0
  timeout: 90
  reasoning_effort: low  # Optional, provider/model-specific; forwarded when set

decisions:
  enabled: true
  fast_completion: true
  verify_proposals: false
  threshold: 0.97
  max_repairs: 1
  backends:
    - name: local
      kind: ollaya
      model: winnow:e4b
      base_url: http://127.0.0.1:11435
      api_key_env: OLLAYA_API_KEY
      max_input_chars: 18000
      timeout: 30
      input_cost_per_million: 0
    - name: jev
      kind: typesafe
      model: jev-1.13.0
      base_url: https://api.typesafe.ai
      api_key_env: TYPESAFE_API_KEY
      max_input_chars: 60000
      timeout: 20
      input_cost_per_million: 0.042
```

Backends are tried in order. High-probability positive and negative judgments
are distinct from uncertainty. The threshold is a routing setting, not a promised
accuracy rate; evaluate it against your task distribution. The character guard
is conservative input admission, not tokenization. Local responses with reported
truncation are rejected. For local-only operation, configure only local backends;
there is no implicit hosted provider.

`fast_completion` checks actual app-tool receipts after a fully executed batch.
An accepted check replaces the next LLM turn with the existing completion
transition. Final steps, failed tool calls, and mixed control/action batches stay
on the ordinary path. Existing approval gates still apply.

`verify_proposals` enables deterministic tool-schema checks and decision checks
before tool dispatch. Confidently rejected proposals receive at most `max_repairs`
generative repair attempts. Set optional `decisions.fallback_llm` using the same
fields as `llm` to use a different repair model. Discarded proposals never execute
tools, and their tokens still count. An uncertain initial verifier falls back to
the original LLM proposal; an uncertain repair after a confirmed rejection fails
explicitly. This is an advisory optimization, not a compliance authorization gate.

Full sanitized provider-call audits are written under each run's
`provider_calls/`, or `llm.audit_dir` when explicitly configured. They include
every attempt, request, response, model, timing, and reported usage. Decision
calls are in its `decisions/` subdirectory. Credentials are read from environment
variables at execution time, never embedded in configuration. LLM token counters
remain generative-token counters; decision usage is separately recorded in
decision events and audits. Failed requests without reported usage have unknown
usage, not measured zero cost.

See [the design](decision-model-cascades-design.md) for tradeoffs and evaluation
requirements. Listed models and prices are examples; choose and evaluate a
profile for your deployment.
