# Skills, Not SDKs: How SKILL.md Replaces the Entire Google ADK

**Thesis:** You don't need to write code in ADK/LangGraph/LangChain/CrewAI to build agents. A well-written Standard Operating Procedure (SKILL.md) plus a thin runtime harness (Proceda) can replace the entire functionality set of a modern agent development kit.

This document maps every capability in [Google ADK 2.0](https://adk.dev/agents/) to its Proceda/SKILL.md equivalent — showing what maps directly, what maps with a different philosophy, and what genuinely requires extension.

---

## Part 1: The Core Philosophical Shift

ADK says: **"Define agents in code. Compose them with classes. Wire behavior with callbacks."**
([ADK: About](https://adk.dev/get-started/about/))

Proceda says: **"Write a procedure. The procedure IS the agent."**

This isn't a superficial difference. It changes everything:

| ADK Mental Model | Proceda Mental Model |
|---|---|
| Agent = Python class with config | Agent = Markdown document with steps |
| Orchestration = Code composition | Orchestration = Step ordering + natural language |
| Behavior control = Callbacks + plugins | Behavior control = Step instructions + approval gates |
| State = Programmatic key-value store | State = Conversation context + variables |
| Planning = LLM or workflow graph | Planning = The SOP itself |
| Testing = Unit tests + eval framework | Testing = SOP-Bench (run procedure, check outputs) |

The key insight: **most "agent framework features" are solutions to problems created by the framework itself.** When you write a procedure in natural language, you don't need a `SequentialAgent` class — you just write Step 1, Step 2, Step 3. You don't need a `before_model_callback` — you just write "Before responding, verify that..." in the step instructions.

### A Note on Orthogonal Concerns

Several capabilities ADK bundles are **not agent framework features** — they're infrastructure or model capabilities that happen to ship with ADK because Google wants to sell Cloud Run and Vertex AI:

- **Cloud deployment** (Cloud Run, GKE, Agent Runtime) — deployment infrastructure, orthogonal to how you define agents
- **Multimodal I/O** (images, audio, video) — a Gemini model capability, not an agent abstraction. Nothing about SKILL.md prevents passing image content to the LLM.
- **Live voice/video streaming** — a Gemini Live API feature, not an agent framework feature

These are excluded from the gap analysis below. Any agent harness (including Proceda) could add these by integrating with the underlying infrastructure/model APIs — they don't require a specific agent SDK.

### The Scorecard (Agent Abstractions Only)

Once you strip out the orthogonal infrastructure, the gap analysis across ADK's ~35 agent-abstraction capabilities looks like this:

| Gap Level | Count | Examples |
|---|---|---|
| **No gap** | 18 | Sequential execution, MCP tools, config, events, audit, replay, multi-model, secret redaction, context management |
| **Low gap** | 12 | Tool auth, access control, guardrails, state templating, artifacts, input validation (different mechanism, same outcome) |
| **Medium gap** | 2 | Parallel LLM chains, sub-skill composition |
| **High gap** | 0 | — |
| **Proceda ahead** | 4 | Human-in-the-loop, tool confirmation, replay, SOP linting/conversion |

The only genuine architectural gap in the agent abstraction layer is **sub-skill composition** (one skill invoking another). Parallel execution is the other medium gap, but it's arguably a runtime scheduling concern, not an agent abstraction concern.

**The punchline:** The only thing an agent SDK gives you over a well-written SOP is sub-agent composition — and that's a thin bridge to build, not a framework to buy.

---

## Part 2: Feature-by-Feature Mapping

### 2.1 Agent Types

#### LlmAgent → A Single SKILL.md

([ADK: LLM Agents](https://adk.dev/agents/llm-agents/))

**ADK (22 lines of Python):**
```python
from google.adk.agents import LlmAgent

agent = LlmAgent(
    name="expense_processor",
    model="gemini-flash-latest",
    description="Processes expense reports",
    instruction="""You are an expense processing agent.
    Review the submitted expense, check policy compliance,
    and approve or flag for review.""",
    tools=[check_policy, lookup_employee, submit_approval],
    output_key="decision",
    output_schema=ExpenseDecision,
    callbacks={
        'before_tool': validate_amounts,
        'after_agent': log_decision
    }
)
```

**Proceda (plain markdown):**
```markdown
---
name: expense-processor
description: Processes expense reports against company policy
required_tools:
  - finance__check_policy
  - hr__lookup_employee
  - finance__submit_approval
output_fields:
  - decision
  - reason
---

### Step 1: Review the expense submission
Read the submitted expense report. Extract the employee name, amount,
category, and date. Look up the employee's department and approval limits
using the HR system.

### Step 2: Check policy compliance
Verify the expense against company policy. Check:
- Amount is within the employee's approval limit
- Category is an approved expense type
- Receipt is attached (if required for this amount)
- Date is within the current reporting period

If any check fails, note the specific violation.

### Step 3: Make decision
[APPROVAL REQUIRED]
Based on the policy check, either approve the expense or flag it for
manual review. Submit the decision through the approval system.
Include the specific reason for your decision.
```

**What's different:** No Python. No class hierarchy. No import statements. The procedure *is* the agent. The model is configured in `proceda.yaml`, not in each agent definition. The output schema is declared as `output_fields` — the LLM extracts structured data via XML tags in the completion summary.

**What's better about the Proceda approach:**
- A non-engineer can read, write, and modify this
- The procedure is auditable — each step is a discrete, reviewable unit
- The approval gate on Step 3 is visible in the document, not hidden in a callback
- No build step, no dependencies to install, no Python environment to manage

---

#### SequentialAgent → Steps in a SKILL.md (Built-in)

([ADK: Sequential Agents](https://adk.dev/agents/workflow-agents/sequential-agents/))

**ADK:**
```python
pipeline = SequentialAgent(
    name="data_pipeline",
    sub_agents=[extract_agent, transform_agent, load_agent]
)
```

**Proceda:** This is literally what SKILL.md *is*. Steps execute sequentially by default.

```markdown
### Step 1: Extract data from source
...
### Step 2: Transform to target schema
...
### Step 3: Load into destination
...
```

**No mapping needed.** Sequential execution is the foundational primitive of an SOP.

---

#### ParallelAgent → Natural Language Within a Step

([ADK: Parallel Agents](https://adk.dev/agents/workflow-agents/parallel-agents/))

**ADK:**
```python
gatherer = ParallelAgent(
    name="data_gatherer",
    sub_agents=[fetch_weather, fetch_news, fetch_traffic]
)
```

**Proceda today:** A single step that instructs the LLM to gather multiple pieces of information. The LLM can make multiple tool calls within one step, and modern LLMs handle this naturally.

```markdown
### Step 1: Gather data from all sources
Fetch the following information simultaneously:
- Current weather conditions from the weather service
- Top news headlines from the news API
- Current traffic conditions from the traffic service

Collect all results before proceeding.
```

**Honest assessment:** This works for tool-level parallelism (LLM issues multiple tool calls), but doesn't provide true concurrent execution of separate LLM reasoning chains. For most SOP use cases, tool-level parallelism is sufficient. True parallel agent execution would require a Proceda extension (e.g., a `[PARALLEL]` marker or nested skill invocation).

**Gap level: Medium.** Works for 80% of cases. The remaining 20% (independent LLM reasoning chains) would need a `parallel_skills` mechanism.

---

#### LoopAgent → Conditional Language + skip_remaining_steps

([ADK: Loop Agents](https://adk.dev/agents/workflow-agents/loop-agents/))

**ADK:**
```python
refiner = LoopAgent(
    name="quality_loop",
    sub_agents=[draft_agent, review_agent],
    max_iterations=5
)
```

**Proceda:** Loop logic is expressed in step instructions. The LLM decides when quality is sufficient.

```markdown
### Step 1: Generate initial draft
Write the first draft of the report based on the provided data.

### Step 2: Review and assess quality
[OPTIONAL]
Review the current draft for completeness, accuracy, and clarity.
Score the quality from 1-10. If the score is 8 or above, call
skip_remaining_steps with the final draft. Otherwise, note specific
improvements needed and continue.

### Step 3: Revise draft
[OPTIONAL]
Incorporate the review feedback and produce an improved draft.
Return to Step 2's review criteria to self-assess.

### Step 4: Final review
Review the draft one final time. If it still needs work after
multiple iterations, flag it for human review rather than continuing
to iterate.
```

**Honest assessment:** This works but has two limitations:
1. The LLM can't literally "loop back" to an earlier step — it moves forward. You simulate loops by having multiple review/revise steps.
2. The `MAX_TOOL_CALL_ITERATIONS=50` per step provides a hard ceiling, but there's no explicit `max_iterations` for the logical loop.

**Gap level: Low-Medium.** Works well for 1-3 iterations. For heavy iteration (10+ rounds), a native loop construct would be cleaner.

---

#### Custom Agents → More Detailed SOPs

([ADK: Custom Agents](https://adk.dev/agents/custom-agents/))

**ADK:**
```python
class CustomAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        # Custom Python logic
        data = await fetch_data(ctx.session.state['source'])
        processed = custom_transform(data)
        yield Event(author=self.name, content=processed)
```

**Proceda philosophy:** If you need "custom logic," you're either:
1. **Writing a tool** (which should be an MCP server), or
2. **Writing a more detailed procedure** (break the "custom logic" into explicit steps)

The ADK `CustomAgent` pattern typically means "I need to do something the framework doesn't support." In Proceda, the procedure is flexible enough to express most logic, and custom compute goes into MCP tools.

**Example:** Instead of a custom agent that fetches data, transforms it, and yields results, you write:

```markdown
### Step 1: Fetch data from source
Use the data_service tool to retrieve records from {source}.

### Step 2: Transform records
For each record, apply the following transformation rules:
- Convert dates to ISO 8601 format
- Normalize currency amounts to USD
- Flag records with missing required fields

### Step 3: Submit processed data
Upload the transformed records to the destination system.
Report the count of successful, failed, and flagged records.
```

**Gap level: None** for procedure-expressible logic. **Medium** for genuinely computational tasks (ML inference, complex data transforms) — but those should be MCP tools regardless of framework.

---

#### Graph-Based Workflows → Conditional Step Instructions

([ADK: Graph Routes](https://adk.dev/workflows/graph-routes/), [ADK: Dynamic Workflows](https://adk.dev/workflows/dynamic/))

**ADK:**
```python
workflow = Workflow(
    name="ticket_router",
    edges=[
        ("START", classifier_agent, router_function),
        (router_function, {
            "bug": bug_handler,
            "support": support_handler,
            "feature": feature_handler
        })
    ]
)
```

**Proceda:** Conditional routing is expressed in step instructions using natural language.

```markdown
### Step 1: Classify the incoming ticket
Read the ticket description and classify it as one of:
- bug: A defect in existing functionality
- support: A user needing help with existing features
- feature: A request for new functionality

### Step 2: Route and handle the ticket
Based on the classification from Step 1:

**If bug:** Look up the affected component in the bug database.
Check for known issues. If it's a known issue, link the ticket
and notify the reporter. If new, create a bug report with
reproduction steps.

**If support:** Search the knowledge base for relevant articles.
Compose a response with step-by-step instructions. If no KB
article exists, escalate to the support team.

**If feature:** Check the product roadmap for similar requests.
Add the request to the feature backlog with priority assessment.
Notify the product team.
```

**Honest assessment:** This works well for simple branching (2-5 paths). For complex DAGs with many nodes and conditional edges, the natural language approach gets verbose and error-prone. However, complex DAGs in agent systems are often a code smell — if your workflow has 15 conditional branches, you probably need multiple simpler SOPs, not one complex graph.

**Gap level: Low** for typical routing. **Medium-High** for genuinely complex graph workflows — but the Proceda philosophy would argue those should be decomposed into simpler skills.

---

### 2.2 Agent Composition & Multi-Agent Patterns

#### Sub-Agent Delegation → Nested Skills / MCP Tool Wrapping Skills

([ADK: Multi-Agents](https://adk.dev/agents/multi-agents/), [ADK: Collaboration](https://adk.dev/workflows/collaboration/))

**ADK:**
```python
researcher = LlmAgent(name="researcher", ...)
writer = LlmAgent(name="writer", ...)
editor = LlmAgent(name="editor", ...)

manager = LlmAgent(
    name="content_manager",
    sub_agents=[researcher, writer, editor]
)
```

**Proceda today:** There's no native sub-skill invocation. But the concept maps to:

1. **Single skill with multiple roles per step:**
```markdown
### Step 1: Research the topic
Act as a research analyst. Gather comprehensive information about
{topic} using the search and database tools. Compile findings
into structured research notes.

### Step 2: Write the draft
Act as a content writer. Using the research from Step 1, write
a clear, engaging article. Follow the style guide in the
knowledge base.

### Step 3: Edit and polish
[APPROVAL REQUIRED]
Act as an editor. Review the draft for clarity, accuracy, grammar,
and adherence to style guidelines. Make all necessary corrections
and produce the final version.
```

2. **Skill-as-MCP-tool (future):** An MCP server that exposes `run_skill` as a tool, enabling one skill to invoke another.

**Gap level: Medium.** The single-skill approach works for most cases (the LLM can adopt different "roles" per step). True multi-agent composition (separate LLM contexts, independent reasoning chains) would need a `run_skill` MCP tool or native sub-skill support.

---

#### AgentTool (Agent as Callable Tool) → Skill-as-MCP-Tool

([ADK: Multi-Agents — AgentTool](https://adk.dev/agents/multi-agents/))

**ADK:**
```python
researcher = LlmAgent(name="researcher", ...)
parent = LlmAgent(
    name="parent",
    tools=[AgentTool(agent=researcher)]
)
```

**Proceda equivalent (future):** An MCP server that wraps skill execution:

```yaml
# proceda.yaml
apps:
  - name: skills
    transport: stdio
    command: ["proceda", "serve", "--skills-dir", "./skills/"]
```

Then in a parent skill:
```markdown
### Step 2: Get research results
Use the skills__run_skill tool to execute the "deep-research" skill
with the topic from Step 1. Wait for completion and use the results.
```

**Gap level: Medium.** Not built today, but architecturally natural — Proceda already has a CLI that could expose skills as MCP tools.

---

#### Multi-Agent Patterns

([ADK: Multi-Agents](https://adk.dev/agents/multi-agents/))

| ADK Pattern | Proceda Equivalent | Gap |
|---|---|---|
| **Manager-Specialist** (hierarchical) | Multi-step skill with role-per-step | Low |
| **Generator-Critic** (review loop) | Steps: generate → review → revise, with `[OPTIONAL]` markers | Low |
| **Iterative Refinement** | Multiple review/revise steps + `skip_remaining_steps` | Low-Medium |
| **Broker/Router** | Conditional step instructions ("if X, do Y") | Low |
| **Data Pipeline** | Sequential steps (the default) | None |
| **Parallel Specialists** | Single step with multiple tool calls | Medium |

---

### 2.3 Tools

#### Function Tools → MCP Tools

([ADK: Function Tools](https://adk.dev/tools-custom/function-tools/))

**ADK:**
```python
def get_weather(city: str, unit: str = "celsius") -> dict:
    """Get weather for a city."""
    return {"temp": 72, "unit": unit}

agent = LlmAgent(tools=[get_weather])
```

**Proceda:** Tools are MCP servers, not inline Python functions. This is more work to set up but more powerful — tools are reusable across skills, language-agnostic, and independently deployable.

```yaml
# proceda.yaml
apps:
  - name: weather
    transport: stdio
    command: ["python", "-m", "weather_mcp_server"]
```

```markdown
---
required_tools:
  - weather__get_weather
---
### Step 1: Check the weather
Use the weather tool to get current conditions for {city}.
```

**Trade-off:** ADK's inline function tools are faster to prototype (just write a Python function). Proceda's MCP approach is more work upfront but produces reusable, composable tools that any skill can use. The MCP approach also means tools can be written in any language and run as separate processes with their own dependencies.

**Gap level: None** functionally. **Higher friction** for quick prototyping — but this is a deliberate design choice favoring reusability over convenience.

---

#### MCP Tools → Native (Identical)

([ADK: MCP Tools](https://adk.dev/tools-custom/mcp-tools/))

Both ADK and Proceda support MCP tools natively. Proceda was MCP-native from day one.

**ADK:**
```python
McpToolset(connection_params=StdioConnectionParams(
    server_params=StdioServerParameters(command="npx", args=[...])
))
```

**Proceda:**
```yaml
apps:
  - name: filesystem
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path"]
```

**Gap level: None.** Proceda's MCP integration is arguably simpler — YAML config vs. Python objects.

---

#### Built-in Tools → Control Tools

([ADK: Integrations — Code Execution](https://adk.dev/integrations/code-execution/), [ADK: Integrations — Google Search](https://adk.dev/integrations/google-search/), [ADK: Integrations — Computer Use](https://adk.dev/integrations/computer-use/))

| ADK Built-in | Proceda Equivalent |
|---|---|
| `LoadMemoryTool` | Not built-in (would be an MCP tool) |
| Code Execution | MCP tool wrapping a code executor |
| Google Search | MCP tool wrapping search API |
| Computer Use | MCP tool (e.g., `agent-browser`) |

**Proceda's built-in control tools:**
- `complete_step` — Signal step completion with summary
- `request_clarification` — Pause and ask human a question
- `skip_remaining_steps` — Early termination with summary

These are fundamentally different from ADK's built-in tools. ADK's built-ins add capabilities (search, code execution). Proceda's control tools manage the *execution flow itself*. External capabilities always come through MCP.

**Gap level: None** — ADK's built-in tools can all be provided as MCP servers. Proceda's philosophy is that tools should be external, not framework-bundled.

---

#### Long-Running / Streaming Tools → MCP Tool Behavior

([ADK: Streaming Tools](https://adk.dev/streaming/streaming-tools/))

**ADK:** Explicit `StreamingTool` class that yields intermediate results.

**Proceda:** MCP tools can take arbitrary time to execute. The runtime waits. Streaming intermediate results isn't natively supported — the tool returns when done.

**Gap level: Medium** for streaming use cases. For long-running-but-blocking tools, no gap. For tools that need to stream intermediate results (e.g., monitoring a stock price), Proceda would need MCP streaming support or a polling pattern.

---

#### Tool Authentication → MCP Server Environment Variables

([ADK: Tool Authentication](https://adk.dev/tools-custom/authentication/))

**ADK:** `ToolContext` passes credentials; integration with Secret Manager.

**Proceda:** Environment variables in `proceda.yaml` with `${VAR}` expansion:
```yaml
apps:
  - name: salesforce
    command: ["node", "salesforce-mcp-server"]
    env:
      SF_TOKEN: ${SALESFORCE_TOKEN}
      SF_INSTANCE: ${SALESFORCE_INSTANCE_URL}
```

**Gap level: Low.** Environment variables are the standard way to pass credentials to MCP servers. For production deployments needing Secret Manager integration, the MCP server itself handles credential retrieval.

---

#### Tool Access Control → Denylist + Allowlist

([ADK: Callbacks — Types of Callbacks](https://adk.dev/callbacks/types-of-callbacks/) — before_tool_callback section)

**ADK:** `before_tool_callback` for per-call authorization.

**Proceda:**
- **Denylist:** `security.tool_denylist` glob patterns in config (e.g., `dangerous_*`, `admin_*`)
- **Allowlist:** `required_tools` in SKILL.md frontmatter — if declared, *only* those tools are available
- **Per-step circuit breaker:** Max 20 tool calls per step (configurable)

```yaml
security:
  tool_denylist:
    - "*__delete_*"
    - "*__drop_*"
    - "admin__*"
```

```markdown
---
required_tools:
  - crm__search_contacts
  - crm__update_contact
  # Only these two tools are available — nothing else
---
```

**Gap level: Low.** Proceda's approach is declarative (denylist/allowlist) rather than programmatic (callbacks). This is actually stronger for SOPs — the tool access policy is visible in the skill document itself, not hidden in code.

---

#### Tool Confirmation → Approval Gates

([ADK: Tool Confirmation](https://adk.dev/tools-custom/confirmation/))

**ADK:** Tool confirmation via callback or UI integration — developer builds the confirmation flow.

**Proceda:** `[APPROVAL REQUIRED]` and `[PRE-APPROVAL REQUIRED]` markers on steps. The runtime handles the entire confirmation flow through the `HumanInterface` protocol.

**Gap level: Proceda is ahead.** Confirmation is a first-class document-level concern, not a code-level concern.

---

### 2.4 Memory & State

#### Session State → Variables + Conversation Context

([ADK: Session State](https://adk.dev/sessions/state/))

**ADK:**
```python
session.state['booking_step'] = 'confirm_payment'
session.state['user:preferred_language'] = 'fr'
session.state['app:api_endpoint'] = 'https://...'
session.state['temp:raw_response'] = {...}
```

Four namespaces: session, user (`user:` prefix), app (`app:` prefix), temp (`temp:` prefix).

**Proceda:**
- **Variables:** Input parameters passed at runtime (`--var key=value`)
- **Conversation context:** The LLM maintains state through message history
- **Step summaries:** Each `complete_step` summary becomes part of the context for subsequent steps
- **Tool results:** Stored in `session.step_tool_results`

```markdown
---
name: order-processor
---
### Step 1: Look up customer
# Variables like {customer_id} are injected from runtime input.
# The LLM remembers everything from prior steps via message context.
```

**Honest assessment:** Proceda doesn't have ADK's four-namespace state system. This is both a limitation and a design choice:

- **Session-scoped state:** Covered by conversation context. The LLM remembers what happened in prior steps.
- **User-scoped state:** Not built-in. Would need a user profile MCP tool.
- **App-scoped state:** Handled by `proceda.yaml` configuration.
- **Temp state:** Implicit — everything within a step is temporary until the step summary is produced.

**Gap level: Low-Medium.** For single-session SOPs (Proceda's primary use case), conversation context is sufficient. For multi-session user state, you'd need an external state store exposed as an MCP tool.

---

#### State Templating in Instructions → Variables in SKILL.md

([ADK: LLM Agents — Instruction Templating](https://adk.dev/agents/llm-agents/))

**ADK:**
```python
agent = LlmAgent(
    instruction="Process order for {customer_name} with budget {budget}"
)
```

**Proceda:**
```markdown
---
name: process-order
---
### Step 1: Process the order
Process the order for {customer_name} with budget {budget}.
```

Variables are injected via `proceda run --var customer_name=Acme --var budget=10000`.

**Gap level: None.** Direct equivalent.

---

#### Sessions & Session Services → RunSession + Event Log

([ADK: Sessions](https://adk.dev/sessions/session/))

**ADK:** `SessionService` interface with multiple backends — `InMemorySessionService`, `VertexAiSessionService`, `DatabaseSessionService`. Sessions persist across invocations.

**Proceda:** `RunSession` captures full execution state. Event logs persist as JSONL in `.proceda/runs/`. Single-session focus — each `proceda run` is one execution.

**Gap level: Low-Medium.** For SOP execution (one run = one session), Proceda covers this. For multi-turn conversational agents that maintain state across many user interactions, you'd need a session persistence layer.

---

#### Long-Term Memory → MCP Tool (Not Built-in)

([ADK: Memory](https://adk.dev/sessions/memory/))

**ADK:**
```python
from google.adk.memory import VertexAiMemoryBankService
memory_service = VertexAiMemoryBankService(...)
runner = Runner(agent=agent, memory_service=memory_service)

# Agent can use LoadMemoryTool to search past conversations
```

**Proceda:** No built-in memory service. Past conversations are stored as JSONL event logs in `.proceda/runs/`, but there's no semantic search over them.

**How you'd bridge this:** An MCP server wrapping a vector store (Chroma, Qdrant, etc.) that indexes past run summaries.

```yaml
apps:
  - name: memory
    command: ["python", "-m", "memory_mcp_server"]
    env:
      CHROMA_PATH: ${HOME}/.proceda/memory
```

```markdown
### Step 1: Recall prior context
Search the memory service for past interactions with {customer_name}.
Use any relevant context to inform your approach.
```

**Gap level: Medium.** Proceda has all the raw data (event logs with full conversation history) but lacks the indexing and retrieval layer. This is a natural extension point.

---

#### Artifacts (Binary Data) → MCP Artifacts

([ADK: Artifacts](https://adk.dev/artifacts/))

**ADK:**
```python
part = Part.from_bytes(image_bytes, "image/png")
await tool_context.save_artifact("chart.png", part)
```

Versioned binary data with `ArtifactService` interface. Backends: `InMemoryArtifactService`, `GcsArtifactService`.

**Proceda:** MCP tool results can include artifacts (`MCPArtifact` with content_type and content). The event log stores artifacts in the run directory.

**Gap level: Low.** Proceda supports artifacts through MCP but doesn't have a standalone artifact service for storing/retrieving binary data across runs. For most SOP use cases (processing documents, generating reports), tool-level artifact handling is sufficient.

---

### 2.5 Callbacks & Hooks

This is where the philosophical difference is sharpest.

([ADK: Callbacks](https://adk.dev/callbacks/), [ADK: Types of Callbacks](https://adk.dev/callbacks/types-of-callbacks/))

#### Before/After Agent Callbacks → Step Structure

**ADK:**
```python
async def before_agent_callback(ctx):
    if ctx.session.state.get('user:role') != 'admin':
        return Content(parts=[Part(text="Not authorized")])
    return None
```

**Proceda:** There are no callbacks. Instead, you write the guard logic into the procedure itself:

```markdown
### Step 1: Verify authorization
[PRE-APPROVAL REQUIRED]
Before proceeding, verify that the requesting user has the
appropriate role and permissions for this operation. If the
user is not authorized, call skip_remaining_steps explaining
why access was denied.
```

**The Proceda argument:** Callbacks are invisible control flow hidden in code. A step that says "Verify authorization" is visible, auditable, and understandable by non-engineers. The `[PRE-APPROVAL REQUIRED]` marker ensures a human sees the authorization check before the procedure continues.

---

#### Before/After Model Callbacks → Step Instructions

([ADK: Callbacks — before_model / after_model](https://adk.dev/callbacks/types-of-callbacks/))

**ADK:**
```python
async def before_model_callback(ctx, llm_request):
    if contains_prompt_injection(llm_request.messages[-1].content):
        return LlmResponse(text="I can't respond to that")
```

**Proceda:** Input validation is a step, not a callback:

```markdown
### Step 1: Validate input
Review the input for completeness and validity. Check that:
- All required fields are present
- No malicious or inappropriate content is included
- The request is within the scope of this procedure

If the input is invalid, call skip_remaining_steps with
an explanation of what's wrong.
```

**Gap level: Low** for content-level guardrails. **Medium** for low-level model interaction control (e.g., injecting few-shot examples, modifying token parameters). But the Proceda philosophy says: if you need to manipulate the raw LLM request, you're over-engineering it. Write better instructions instead.

---

#### Before/After Tool Callbacks → Step Instructions + Denylist

([ADK: Callbacks — before_tool / after_tool](https://adk.dev/callbacks/types-of-callbacks/))

**ADK:**
```python
async def before_tool_callback(ctx, tool_name, args):
    if tool_name == "delete_account" and ctx.session.state.get('role') != 'admin':
        return {"error": "Not authorized to delete accounts"}
```

**Proceda:** Tool-level authorization is handled by:
1. **Denylist:** Block dangerous tools entirely
2. **Allowlist:** Only expose needed tools via `required_tools`
3. **Step instructions:** Tell the LLM when not to use certain tools
4. **Approval gates:** Human reviews before critical actions

```markdown
### Step 3: Execute the change
[APPROVAL REQUIRED]
Apply the approved changes using the admin tools. The human
reviewer will verify the action before it takes effect.
```

**Gap level: Low.** Proceda's declarative approach (denylist + allowlist + approval gates) covers most authorization scenarios. For truly dynamic, per-call authorization logic, you'd need a wrapper MCP server that enforces policies.

---

#### Plugins (Global Callbacks) → proceda.yaml + Convention

([ADK: Plugins](https://adk.dev/plugins/))

**ADK:**
```python
runner = Runner(
    agent=agent,
    plugins=[LoggingPlugin(), SecurityPlugin(), AnalyticsPlugin()]
)
```

Pre-built plugins include: Reflect and Retry, BigQuery Analytics, Context Filter, Global Instruction, Save Files as Artifacts.

**Proceda:** No plugin system. Cross-cutting concerns are handled by:
- **Event sinks:** For logging, analytics, observability
- **Configuration:** Security denylist, secret redaction
- **Convention:** Standard first/last steps in SOPs for setup/teardown

**Gap level: Medium.** The event sink system provides the logging/observability half of plugins. The security/policy half is handled by configuration. But there's no hook for custom code at arbitrary execution points (like "run this function before every LLM call across all skills"). Whether you *need* that depends on whether you believe cross-cutting concerns should be in code or in configuration.

---

#### Callback Design Patterns → SOP Patterns

([ADK: Callback Design Patterns](https://adk.dev/callbacks/design-patterns-and-best-practices/))

ADK documents patterns like caching (before/after model), logging (all callbacks), guardrails (before model + before tool), A/B testing (before model), and fallback/retry (after tool).

In Proceda, these patterns map to:
- **Caching:** MCP tool that checks cache before calling upstream service
- **Logging:** Built-in event log (every event is logged automatically)
- **Guardrails:** Validation steps + approval markers
- **A/B testing:** Model selection in `proceda.yaml` (swap models without code changes)
- **Fallback/retry:** Built-in error recovery (human chooses retry/skip/cancel)

---

### 2.6 Human-in-the-Loop

**This is where Proceda is stronger than ADK.**

([ADK: Human Input in Workflows](https://adk.dev/workflows/human-input/))

#### ADK Approach: Build It Yourself

ADK has no built-in human-in-the-loop primitives. The [workflow docs](https://adk.dev/workflows/human-input/) suggest patterns like:
- Store pending decisions in session state
- Build a separate approval UI
- Poll for decisions
- Resume agent on approval

```python
# ADK: You write all of this yourself
def request_approval(tool_context, action, reason):
    approval_id = store_approval_request(action, reason, ...)
    return {"approval_id": approval_id, "status": "pending"}
    # Then you need: approval UI, polling, session resumption...
```

#### Proceda Approach: First-Class Primitives

Proceda has **four** built-in HITL mechanisms:

1. **`[APPROVAL REQUIRED]`** — Human must approve after step completes
2. **`[PRE-APPROVAL REQUIRED]`** — Human must approve before step begins
3. **`request_clarification`** — LLM pauses to ask human a question
4. **Error recovery** — On failure, human chooses: retry, skip, or cancel

```markdown
### Step 4: Submit the transfer
[PRE-APPROVAL REQUIRED]
Transfer ${amount} from account {source} to account {destination}.

### Step 5: Verify completion
[APPROVAL REQUIRED]
Confirm the transfer completed successfully. Display the
confirmation number and updated balances.
```

The `HumanInterface` protocol has three implementations:
- `TerminalHumanInterface` — Interactive Rich terminal UI
- `AutoApproveHumanInterface` — For testing/CI
- `ScriptedHumanInterface` — Deterministic testing with pre-scripted responses

**Proceda advantage:** Human oversight is a *document-level* concern, visible in the SOP itself. Reviewers can see exactly where humans are in the loop by scanning for `[APPROVAL REQUIRED]` markers. In ADK, approval logic is scattered across callbacks, tools, and custom code.

**Gap level: Proceda is ahead.** ADK's HITL is DIY. Proceda's is built-in and declarative.

---

### 2.7 Guardrails & Safety

([ADK: Safety](https://adk.dev/safety/))

| Guardrail Type | ADK Approach | Proceda Approach |
|---|---|---|
| **Input validation** | `before_model_callback` | Validation step in SOP |
| **Output filtering** | `after_model_callback` | Review step + approval gate |
| **Tool authorization** | `before_tool_callback` | Denylist + allowlist + approval |
| **Content moderation** | LLM-as-safety-layer in callback | Step: "Review output for..." |
| **Prompt injection defense** | `before_model_callback` | System prompt (runtime-level) |
| **Secret redaction** | Custom plugin | Built-in: `logging.redact_secrets` |
| **Rate limiting** | Custom plugin | Per-step tool call limit (default 20) |
| **Audit trail** | Custom logging | Built-in: JSONL event log |

**Proceda advantage:** Guardrails are visible in the procedure document. A compliance officer can read the SKILL.md and see every safety check. In ADK, safety logic is buried in Python callbacks.

**Proceda limitation:** No programmatic guardrails at the LLM request/response level. You're trusting the LLM to follow the instructions in the SOP. For high-stakes applications, you might want a separate safety model check — this could be added as an MCP tool or a runtime-level feature.

**Gap level: Low-Medium.** Different philosophy (declarative vs. programmatic), but both achieve the goal. Proceda's approach is more auditable; ADK's is more programmable.

---

### 2.8 Planning & Reasoning

([ADK: Workflow Agents](https://adk.dev/agents/workflow-agents/), [ADK: Dynamic Workflows](https://adk.dev/workflows/dynamic/))

| Pattern | ADK | Proceda |
|---|---|---|
| **Explicit plan** | Workflow agents (Sequential, Graph) | The SKILL.md IS the plan |
| **LLM-driven planning** | LlmAgent with planning instructions | Step instructions guide reasoning |
| **ReAct pattern** | LlmAgent + tools (implicit) | Every step is a ReAct cycle |
| **Plan-then-execute** | SequentialAgent with planner + executor | Step 1: Plan. Step 2-N: Execute. |

**Key insight:** In ADK, you build a system that *creates* plans at runtime. In Proceda, the plan is written by a human *before* runtime. This is a feature, not a bug — SOPs exist precisely because you want a predetermined, reviewed, approved plan, not an LLM improvising one.

**When ADK's dynamic planning is better:** Novel, open-ended tasks where the steps can't be predetermined.

**When Proceda's static planning is better:** Regulated, repeatable, auditable processes — which is exactly what SOPs are for.

**Gap level: Low.** For SOP use cases, Proceda's approach is superior. For open-ended agent tasks, ADK's dynamic planning is more appropriate.

---

### 2.9 Events & Streaming

#### ADK Event System → Proceda RunEvent System

([ADK: Events](https://adk.dev/events/), [ADK: Runtime Event Loop](https://adk.dev/runtime/event-loop/))

Both systems are event-driven. Proceda's is actually more comprehensive for SOP execution:

**Proceda's 26 event types:**
- Lifecycle: `RUN_CREATED`, `RUN_STARTED`, `RUN_COMPLETED`, `RUN_FAILED`, `RUN_CANCELLED`
- Steps: `STEP_STARTED`, `STEP_COMPLETED`, `STEP_SKIPPED`
- Messages: `MESSAGE_SYSTEM`, `MESSAGE_ASSISTANT`, `MESSAGE_USER`, `MESSAGE_TOOL`, `MESSAGE_REASONING`
- Tools: `TOOL_CALLED`, `TOOL_COMPLETED`, `TOOL_FAILED`
- Human: `APPROVAL_REQUESTED`, `APPROVAL_RESPONDED`, `CLARIFICATION_REQUESTED`, `CLARIFICATION_RESPONDED`, `ERROR_RECOVERY_REQUESTED`, `ERROR_RECOVERY_SELECTED`
- Runtime: `LLM_USAGE`, `STATUS_CHANGED`, `CONTEXT_UPDATED`, `SUMMARY_GENERATED`

**ADK's events:** More focused on LLM interaction events (user message, agent response, tool call, tool result, state changes).

**Proceda advantage:** Richer event taxonomy for SOP execution — step-level tracking, approval tracking, error recovery tracking. Every state transition is an event.

**Gap level: None** for SOP execution events.

---

### 2.10 Context Management

([ADK: Context Management](https://adk.dev/context/), [ADK: Context Caching](https://adk.dev/context/caching/), [ADK: Context Compaction](https://adk.dev/context/compaction/))

| Feature | ADK | Proceda |
|---|---|---|
| **Token budgeting** | Context caching, compaction | `ContextManager` with configurable budget (100k default, 4k reserve) |
| **History trimming** | Context compaction plugin | Automatic: preserve system + critical messages, trim oldest non-critical |
| **Step summarization** | Not built-in (manual) | Built-in: after step completion, messages summarized |
| **Caching** | Gemini context caching | Not built-in (model-level if supported) |

**Gap level: Low.** Both handle context management. Proceda's step-based summarization is actually a better fit for SOPs — each completed step collapses to a summary, keeping context focused.

---

### 2.11 Evaluation & Testing

([ADK: Evaluate](https://adk.dev/evaluate/))

#### ADK Eval Framework → SOP-Bench

**ADK:**
```json
{
  "eval_set_id": "test_set",
  "eval_cases": [{
    "conversation": [{
      "user_content": {"parts": [{"text": "Query"}]},
      "final_response": {"parts": [{"text": "Expected"}]}
    }]
  }]
}
```

Features: trajectory evaluation, response metrics (exact match, semantic similarity), [custom metrics](https://adk.dev/evaluate/custom_metrics/), [environment simulation](https://adk.dev/evaluate/environment_simulation/), [user simulation](https://adk.dev/evaluate/user-sim/), CLI + web UI for building eval datasets.

**Proceda (SOP-Bench):**
- CSV-based test cases with input variables and expected outputs
- Harness runs each case through the skill
- Compares predicted vs. expected outputs (case-insensitive, fuzzy matching)
- Per-domain Task Success Rate (TSR) metrics
- Trace saving for analysis

**Proceda advantage:** SOP-Bench evaluates *procedure execution*, not just LLM responses. It checks whether the right tools were called, the right steps were completed, and the final outputs match expectations.

**ADK advantage:** Richer eval framework with LLM-as-judge metrics, trajectory evaluation, and UI for building eval datasets.

**Gap level: Low-Medium.** Both have evaluation systems. ADK's is more polished; Proceda's is more focused on SOP correctness.

---

### 2.12 Configuration

([ADK: Agent Config](https://adk.dev/agents/config/), [ADK: RunConfig](https://adk.dev/runtime/runconfig/))

| Aspect | ADK | Proceda |
|---|---|---|
| **Agent config** | Python code (class params) | SKILL.md frontmatter |
| **Model config** | Per-agent in code | `proceda.yaml` (global or overridable) |
| **Tool config** | Python code | `proceda.yaml` apps section |
| **Security config** | Callbacks/plugins | `proceda.yaml` security section |
| **Runtime config** | `RunConfig` object | `proceda.yaml` + CLI flags |

**Proceda advantage:** Configuration is declarative YAML, not code. Easier to version-control, review, and modify without touching Python.

**Gap level: None.** Different approach, but Proceda's is arguably better for the SOP use case.

---

### 2.13 Multi-Model Support

([ADK: Models](https://adk.dev/agents/models/), [ADK: LiteLLM](https://adk.dev/agents/models/litellm/), [ADK: Ollama](https://adk.dev/agents/models/ollama/))

**ADK:** Gemini (native), Claude ([ADK: Anthropic](https://adk.dev/agents/models/anthropic/)), GPT and others via [LiteLLM](https://adk.dev/agents/models/litellm/), local models via [Ollama](https://adk.dev/agents/models/ollama/) and [vLLM](https://adk.dev/agents/models/vllm/).

**Proceda:** Any model via LiteLLM. Model configured in `proceda.yaml`.

**Gap level: None.** Both use LiteLLM for multi-model support. Proceda may actually be simpler here — one config line changes the model for all skills.

---

### 2.14 Observability & Logging

([ADK: Observability](https://adk.dev/observability/), [ADK: Logging](https://adk.dev/observability/logging/))

**ADK:** Built-in logging, Cloud Logging integration, Cloud Trace, third-party integrations (AgentOps, Langwatch, MLflow, Weave, etc.).

**Proceda:**
- **Event log:** Every event → JSONL file with timestamps, payloads, token usage
- **Secret redaction:** Built-in regex-based redaction of API keys, passwords, tokens
- **Replay:** `proceda replay` renders any past run to terminal
- **Event sinks:** Extensible protocol — implement `EventSink` for custom destinations

**Gap level: Low.** Proceda's built-in observability is strong for single-machine use. For distributed cloud deployments, you'd need custom event sinks for Cloud Logging/Datadog/etc.

---

### 2.15 A2A (Agent-to-Agent Protocol)

([ADK: A2A](https://adk.dev/a2a/))

**ADK:** Support for the [A2A protocol](https://adk.dev/a2a/intro/) — standardized agent-to-agent communication. Can expose ADK agents as A2A servers and consume A2A agents as tools.

**Proceda:** No A2A support.

**Gap level: Medium.** A2A is an emerging protocol. If it gains adoption, Proceda could support it via MCP bridge (A2A agent exposed as MCP tool). This is an interoperability concern, not an agent abstraction concern.

---

### 2.16 Development Tools

([ADK: Web Interface](https://adk.dev/runtime/web-interface/), [ADK: Command Line](https://adk.dev/runtime/command-line/), [ADK: API Server](https://adk.dev/runtime/api-server/))

| Tool | ADK | Proceda |
|---|---|---|
| **CLI interaction** | `adk run` | `proceda run` |
| **Browser dev UI** | `adk web` (interactive testing, event inspection, eval dataset building) | Not built-in |
| **REST API server** | `adk api_server` | Python SDK (`Agent.run_async()`) |
| **Linting** | Not built-in | `proceda lint` (validates SKILL.md structure) |
| **Replay** | Not built-in | `proceda replay` (renders past runs) |
| **Environment check** | Not built-in | `proceda doctor` (checks deps, config, API keys) |
| **SOP conversion** | Not built-in | `proceda convert` (arbitrary text → SKILL.md) |

**Mixed gap:** ADK has a better dev UI. Proceda has better SOP-specific tooling (lint, convert, replay, doctor).

---

## Part 3: The Full Mapping Table

| ADK Capability | Proceda Equivalent | Gap | Notes |
|---|---|---|---|
| **[LlmAgent](https://adk.dev/agents/llm-agents/)** | SKILL.md | None | The skill IS the agent |
| **[SequentialAgent](https://adk.dev/agents/workflow-agents/sequential-agents/)** | Steps 1, 2, 3... | None | Built-in primitive |
| **[ParallelAgent](https://adk.dev/agents/workflow-agents/parallel-agents/)** | Multi-tool-call step | Medium | No true parallel LLM chains |
| **[LoopAgent](https://adk.dev/agents/workflow-agents/loop-agents/)** | Repeated steps + skip_remaining | Low-Med | Works for 1-3 iterations |
| **[CustomAgent](https://adk.dev/agents/custom-agents/)** | MCP tool + detailed steps | Low | Compute goes in tools |
| **[Graph Workflow](https://adk.dev/workflows/graph-routes/)** | Conditional step instructions | Med | Verbose for complex DAGs |
| **[Multi-Agents](https://adk.dev/agents/multi-agents/)** | Role-per-step / skill-as-tool | Medium | No native sub-skill yet |
| **[Function tools](https://adk.dev/tools-custom/function-tools/)** | MCP tools | None | Different mechanism, same result |
| **[MCP tools](https://adk.dev/tools-custom/mcp-tools/)** | MCP tools | None | Native in both |
| **[Built-in tools](https://adk.dev/integrations/)** | MCP tools | None | Philosophy: tools are external |
| **[Streaming tools](https://adk.dev/streaming/streaming-tools/)** | MCP tools (blocking) | Medium | No streaming mid-tool |
| **[Tool auth](https://adk.dev/tools-custom/authentication/)** | Env vars in config | Low | Standard MCP pattern |
| **[Tool confirmation](https://adk.dev/tools-custom/confirmation/)** | Approval markers | **Ahead** | First-class in Proceda |
| **[Session state](https://adk.dev/sessions/state/)** | Conversation context | Low-Med | No explicit key-value store |
| **[Memory](https://adk.dev/sessions/memory/)** | Event logs (no search) | Medium | Needs memory MCP tool |
| **[Artifacts](https://adk.dev/artifacts/)** | MCP artifacts | Low | Tool-level, not framework-level |
| **[Callbacks](https://adk.dev/callbacks/)** | Step structure + markers | Low | Visible in document |
| **[Plugins](https://adk.dev/plugins/)** | Event sinks + config | Medium | No arbitrary code hooks |
| **[Safety](https://adk.dev/safety/)** | Steps + denylist + approval | Low-Med | More auditable |
| **[Human input](https://adk.dev/workflows/human-input/)** | First-class markers | **Ahead** | Proceda is stronger here |
| **[Events](https://adk.dev/events/)** | 26 RunEvent types | None | Richer for SOP tracking |
| **[Evaluation](https://adk.dev/evaluate/)** | SOP-Bench | Low-Med | Different focus |
| **[Context mgmt](https://adk.dev/context/)** | ContextManager | Low | Step summarization built-in |
| **[Config](https://adk.dev/agents/config/)** | proceda.yaml | None | Declarative YAML |
| **[Multi-model](https://adk.dev/agents/models/)** | LiteLLM | None | Same approach |
| **[Observability](https://adk.dev/observability/)** | Event log + replay | Low | Strong single-machine |
| **[A2A](https://adk.dev/a2a/)** | Not built-in | Medium | MCP bridge possible |
| **[Session services](https://adk.dev/sessions/session/)** | RunSession + event log | Low-Med | Single-session focus |
| **Audit trail** | Custom logging | JSONL event log | None | Built-in |
| **Secret redaction** | Custom plugin | Built-in config | None | Built-in |
| **Replay** | Not in ADK | `proceda replay` | **Ahead** | Proceda-only |
| **SOP linting** | Not in ADK | `proceda lint` | **Ahead** | Proceda-only |
| **SOP conversion** | Not in ADK | `proceda convert` | **Ahead** | Proceda-only |

---

## Part 4: What Proceda Genuinely Can't Do (Yet)

These are real gaps in the agent abstraction layer, not orthogonal infrastructure concerns:

### 4.1 True Parallel Execution
Multiple independent LLM reasoning chains running simultaneously. Workaround: multiple tool calls in one step. Fix: `[PARALLEL]` marker or sub-skill invocation.

### 4.2 Sub-Skill Composition
One skill calling another as a sub-routine. Workaround: role-per-step. Fix: skill-as-MCP-tool bridge.

### 4.3 Long-Term Memory with Semantic Search
Recalling information from past runs. Workaround: manual reference. Fix: memory MCP tool indexing event logs.

---

## Part 5: What Proceda Does Better

### 5.1 Auditability
Every step, every tool call, every approval decision is logged as a structured event. The SKILL.md itself is a human-readable audit document. Compliance officers can review the procedure without reading code.

### 5.2 Human Oversight
First-class approval gates, clarification requests, and error recovery — not bolted on via callbacks. ([Compare ADK's DIY approach](https://adk.dev/workflows/human-input/) to Proceda's declarative markers.)

### 5.3 Accessibility
Non-engineers can write, read, and modify SKILL.md files. The barrier to creating an agent is "can you write a procedure?" not "can you write Python."

### 5.4 Determinism
The execution path is defined by the document. Steps execute in order. Branching is explicit in natural language. No hidden control flow in callbacks or plugins.

### 5.5 Replay & Debugging
`proceda replay` reconstructs any past execution from the event log. Every run is reproducible. ADK has no equivalent.

### 5.6 Minimal Abstraction
No class hierarchies, no design patterns, no framework concepts to learn. Just: write steps, attach tools, run. Compare to ADK's taxonomy: `LlmAgent`, `SequentialAgent`, `ParallelAgent`, `LoopAgent`, `CustomAgent`, `BaseAgent`, `Workflow`, `AgentTool`, `BasePlugin`, `EventActions`, `InvocationContext`...

---

## Part 6: The Strategic Argument

ADK (and frameworks like it) optimizes for **developer power** — maximum flexibility, composability, and programmability. This is valuable when you're building novel, complex agent systems.

Proceda optimizes for **operational clarity** — readable procedures, visible oversight, auditable execution. This is valuable when you're automating real business processes that have compliance, safety, and handoff requirements.

The question isn't "which is more powerful?" (ADK is). The question is: **"For the 80% of agent use cases that are essentially SOPs, do you need that power?"**

Most enterprise agent deployments are:
- Customer service routing
- Document processing
- Compliance checks
- Approval workflows
- Data validation pipelines
- Incident response procedures

These are all SOPs. They don't need [`ParallelAgent`](https://adk.dev/agents/workflow-agents/parallel-agents/) or [`Graph Workflow`](https://adk.dev/workflows/graph-routes/) or [`before_model_callback`](https://adk.dev/callbacks/types-of-callbacks/). They need:
1. A clear procedure
2. Access to tools
3. Human oversight at critical points
4. An audit trail

That's what Proceda provides.

---

## Part 7: Prioritized Roadmap for Full Parity

If the goal is to cover 95% of ADK's agent-abstraction functionality, here's the priority order:

### P0: Sub-Skill Composition
- `proceda serve` command exposing skills as MCP tools
- One skill can invoke another via tool call
- Unlocks: delegation, multi-agent patterns, complex workflows

### P1: Long-Term Memory
- Memory MCP tool that indexes past run event logs
- Semantic search over past executions
- Enables: learning from past runs, user preference recall

### P2: Parallel Step Execution
- `[PARALLEL]` marker for steps that can run concurrently
- Or: parallel sub-skill invocation via composition
- Enables: independent data gathering, parallel processing

### P3: Structured Output Schemas
- Pydantic-style output validation (beyond XML tag extraction)
- Type-checked outputs from skills
- Enables: reliable downstream consumption of skill results

---

*Generated 2026-04-24. Based on analysis of [Google ADK 2.0 docs](https://adk.dev/) ([source](https://github.com/google/adk-docs)) and the Proceda codebase.*
