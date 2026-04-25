# SOP Skills as an Agent Development Kit

This report answers a deliberately strong thesis:

> For a large class of useful agents, you should not have to write code in ADK, LangGraph, LangChain, or any other agent SDK. You should be able to write the operating procedure as an SOP, attach tools, and let a small harness execute it with human oversight.

The comparison target is Google's Agent Development Kit, especially the ADK 2.0 beta documentation. ADK is a serious modern agent framework: it has LLM agents, workflow agents, graph workflows, dynamic workflows, collaborative subagents, tools, MCP, OpenAPI, runtime APIs, event streams, sessions, state, memory, artifacts, callbacks, plugins, deployment paths, evaluation, observability, security guidance, A2A, streaming, and grounding.

The Proceda claim is not that those problems disappear. The claim is that most of them should move to one of three simpler places:

1. The SOP itself, as `SKILL.md`.
2. The harness, as a small reusable runtime with events, approvals, tool access, logging, and replay.
3. External capabilities, as MCP tools or infrastructure adapters.

That shift matters because a procedure is already the artifact an operations team understands. ADK starts with code objects and then adds no-code conveniences. Proceda starts with the procedure and makes code the escape hatch.

## Source Basis

This report was grounded in the current official ADK docs, especially:

- ADK 2.0 overview: https://adk.dev/2.0/
- Graph-based workflows: https://adk.dev/workflows/
- Graph routes: https://adk.dev/workflows/graph-routes/
- Workflow data handling: https://adk.dev/workflows/data-handling/
- Workflow human input: https://adk.dev/workflows/human-input/
- Collaborative agent teams: https://adk.dev/workflows/collaboration/
- Dynamic workflows: https://adk.dev/workflows/dynamic/
- LLM agents: https://adk.dev/agents/llm-agents/
- Workflow agents: https://adk.dev/agents/workflow-agents/
- Agent Config: https://adk.dev/agents/config/
- Function tools: https://adk.dev/tools-custom/function-tools/
- MCP tools: https://adk.dev/tools-custom/mcp-tools/
- Runtime: https://adk.dev/runtime/
- Events: https://adk.dev/events/
- Sessions, state, and memory: https://adk.dev/sessions/
- Callbacks: https://adk.dev/callbacks/
- Artifacts: https://adk.dev/artifacts/
- Evaluation: https://adk.dev/evaluate/
- Safety and security: https://adk.dev/safety/
- Skills for ADK agents: https://adk.dev/skills/

Proceda references are from this repository's `docs/architecture.md`, `docs/product-spec.md`, `docs/skill-format.md`, `docs/configuration.md`, and the runtime implementation in `src/proceda/`.

## Executive Summary

ADK's capability surface can be reconstructed from five primitive needs:

| Need | ADK answer | Proceda answer |
|---|---|---|
| Define agent behavior | `LlmAgent`, Agent Config YAML, instructions, tools | `SKILL.md` frontmatter plus ordered SOP steps |
| Control execution flow | workflow agents, graphs, dynamic workflows, callbacks | step order, markers, control tools, natural-language branch criteria, future optional flow markers |
| Attach capabilities | function tools, toolsets, MCP, OpenAPI, built-ins | MCP apps only; wrap code, APIs, built-ins, and remote agents as MCP tools |
| Keep humans in control | `RequestInput`, action confirmations, auth flows | `[PRE-APPROVAL REQUIRED]`, `[APPROVAL REQUIRED]`, `request_clarification`, `HumanInterface` |
| Observe and persist runs | Runner events, sessions, state, artifact/memory services, evals | `RunEvent`, `RunSession`, JSONL event logs, replay, collector sinks, future state/artifact/memory services |

The conceptual replacement is:

```text
ADK code object graph
  Agent(...)
  SequentialAgent(...)
  ParallelAgent(...)
  Workflow(edges=[...])
  @node(...)
  callbacks=[...]
  Toolset(...)

becomes

SKILL.md
  YAML metadata
  Step 1, Step 2, Step 3
  explicit decision criteria
  approval markers
  output fields
  required MCP tools

+ Proceda harness
  parser
  executor
  control tools
  human interface
  MCP orchestrator
  event log
  replay
```

The honest answer is that Proceda already replaces a lot of ADK for SOP-shaped work, but not all of ADK for arbitrary agent systems. The biggest remaining gaps are true parallel sub-runs, durable resume/checkpointing, first-class graph branches, native artifact storage, long-term memory, and hosted runtime surfaces such as web/API/ambient agents. Those are harness extensions, not reasons to abandon the SOP-first thesis.

## The Key Distinction

ADK treats "agent development" as a software construction problem. You instantiate classes, wire callbacks, select tool abstractions, choose session services, define state, and maybe wrap it in YAML or visual builders later.

Proceda treats agent development as procedure execution. The author writes:

```markdown
---
name: reimbursement-review
description: Review and approve reimbursement requests
required_tools:
  - finance__lookup_policy
  - finance__submit_reimbursement
output_fields:
  - decision
  - reason
---

### Step 1: Extract request facts
Read the reimbursement request. Identify the employee, amount, category,
date, receipt status, and stated business purpose.

### Step 2: Check policy
Call `finance__lookup_policy` for the category and amount. Compare the request
to policy. If information is missing, call `request_clarification`.

### Step 3: Submit decision
[APPROVAL REQUIRED]
If the request is compliant, submit it. If it is not compliant, reject it with
the specific policy reason. Include `<decision>...</decision>` and
`<reason>...</reason>` in the final summary.
```

That single document replaces a surprising amount of framework code:

- `LlmAgent.name`, `description`, and `instruction`
- `tools=[...]`
- an output schema or output key
- sequential workflow composition
- a HITL approval callback
- a terminal interaction loop
- an audit log expectation

The procedure remains readable by the same people who own the process. That is the central product advantage.

## Replacement Model

Every ADK capability falls into one of four replacement buckets.

| Bucket | Meaning | Examples |
|---|---|---|
| Native in SKILL.md | The procedure document expresses the concept directly | sequential flow, instructions, roles, decision criteria, approvals |
| Native in Proceda harness | The runtime already implements the behavior | step execution, LLM loop, control tools, MCP, events, logs, replay |
| Externalized to MCP or infrastructure | The capability should be a tool/service, not an agent framework feature | databases, search, OpenAPI, code execution, memory stores, artifact stores |
| Harness extension | Proceda needs a new primitive or adapter | parallel sub-runs, durable resume, web/API server, streaming UI, graph visualization |

This matters because "replace ADK" does not mean "copy every ADK class into Proceda." It means preserve the user capability while removing unnecessary agent-framework ceremony.

## Capability Map

| ADK capability | What it does | Proceda replacement | Status |
|---|---|---|---|
| `LlmAgent` | LLM-powered agent with name, model, instruction, tools | One `SKILL.md` executed by `Agent` / `Runtime` | Native |
| Agent identity | `name`, `description`, model | Skill frontmatter plus `proceda.yaml` model config | Native |
| Instructions | system prompt or instruction field | Step bodies plus full skill definition in the system prompt | Native |
| Agent Config YAML | no-code-ish YAML agent definition | `SKILL.md` is the canonical no-code definition | Native, simpler |
| ADK Skills | load task instructions/resources as tools | Proceda makes the skill the agent, not a helper tool | Proceda stronger for SOPs |
| `SequentialAgent` | deterministic ordered subagent execution | numbered `### Step N` execution | Native |
| `LoopAgent` | repeat subagents until stop condition or max iterations | repeated review/revise steps, step instructions, `skip_remaining_steps`; future loop marker | Partial |
| `ParallelAgent` | concurrent subagent execution | parallel work inside an MCP tool; future parallel skill branches | Gap |
| Graph workflow `Workflow(edges=...)` | deterministic node graph with routes | linear SOP with explicit branch criteria and early exits; future branch markers | Partial |
| Graph routes | route values to nodes, fan-out, joins, nested workflows | router step + conditional steps + `skip_remaining_steps`; future graph compiler | Partial |
| Dynamic workflows | code-level loops, conditionals, `ctx.run_node`, checkpointing | put deterministic code in MCP tools; use SOP for the human-readable flow | Partial |
| Collaborative agent teams | coordinator delegates to subagents in chat/task/single-turn modes | role-per-step, sub-skill invocation via MCP, future native sub-skills | Partial |
| Agent as tool | call an agent from another agent | expose `proceda run` / skill runner as an MCP tool | Future, natural |
| Function tools | Python/TS/Go/Java functions as tools | MCP servers wrapping functions | Native via MCP, more setup |
| Toolsets | groups of dynamically provided tools | one MCP app/server per capability domain | Native |
| MCP tools | connect MCP servers | `MCPOrchestrator` over stdio and HTTP | Native |
| OpenAPI tools | turn API specs into tools | OpenAPI-to-MCP server generation | Externalized |
| Built-in Google tools | search, code execution, computer use, grounding, DB tools | MCP tools or model-provider tools wrapped behind MCP | Externalized |
| Tool auth | OAuth/OIDC/API-key flows and credential exchange | environment variables, MCP-server auth, wrapper tools, future auth events | Partial |
| Action confirmations | per-tool confirmation before execution | step-level approvals, pre-approvals, human interface | Native, different granularity |
| Long-running tools | pause/resume or emit updates | blocking MCP tools today; async job MCP pattern; future tool progress events | Partial |
| Streaming tools | continuous tool output | not native; use polling MCP or future streaming MCP/events | Gap |
| Planner | model planning or ReAct planner | SOP is the plan; model thinking config can remain model-level | Native conceptually |
| Code execution | execute generated code | MCP code executor or sandbox tool | Externalized |
| Generate config | temperature, max tokens, safety settings | `proceda.yaml` LLM config; future per-skill/per-step config | Partial |
| Input/output schema | structured inputs and outputs | variables, `output_fields`, tool schemas, final XML tags | Partial, practical |
| `output_key` state passing | save response into session state | conversation context and summaries; future explicit skill state | Partial |
| Session | conversation/run object | `RunSession` | Native |
| Session service | in-memory, Vertex AI, database | event log plus future snapshot/resume service | Partial |
| State | session scratchpad | variables, message history, step summaries; future explicit state | Partial |
| Memory | long-term cross-session recall | memory MCP tool indexing run logs/vector DB | Externalized |
| Artifacts | versioned files/binary data | MCP artifact metadata and event-log artifact writer; storage not yet wired through | Partial |
| Context objects | invocation, tool, callback context | `RunSession`, messages, events, tool results | Native enough |
| Context caching | reuse context with Gemini | model/provider adapter concern | Gap/optional |
| Context compression | summarize old context | `ContextManager` trimming today; future summarizer compaction | Partial |
| Callbacks | before/after agent/model/tool hooks | explicit SOP steps, event sinks, wrapper MCP tools | Partial |
| Plugins | packaged callback/event extensions | event sinks, human interfaces, MCP apps; future plugin registry | Partial |
| Events | runtime stream of messages, tool calls, state changes | `RunEvent` types and event sinks | Native |
| Runtime event loop | Runner orchestrates agents and commits events | `Runtime` + `Executor` + `RunSession` | Native |
| CLI run | run in terminal | `proceda run` | Native |
| Web UI | dev web interface | not OSS v1; TUI/terminal-first, hosted UI later | Gap by choice |
| API server | expose agent over HTTP | future adapter around `Runtime.start()` / event stream | Gap |
| Ambient agents | triggers from Pub/Sub/Eventarc/etc. | external trigger service invokes `proceda run` | Externalized/future |
| Resume agents | resume stopped workflow/long-running function | event log exists; durable resume not yet implemented | Gap |
| Runtime config | streaming, modalities, max calls, metadata | `proceda.yaml`, max loop guards, event metadata; future richer config | Partial |
| Deployment | Cloud Run, GKE, Agent Runtime | containerize Proceda or use hosted service | Externalized |
| Observability | logging, tracing, partner integrations | JSONL event log, `proceda replay`, custom event sinks | Native locally |
| Evaluation | eval sets, criteria, user/env simulation | SOP-Bench style tests, `CollectorEventSink`, `ScriptedHumanInterface`, future eval CLI | Partial |
| Safety/security | identity, guardrails, sandboxing, evals | required-tools allowlist, denylist, approvals, redaction, wrapper tools | Partial |
| A2A | expose/consume remote agents | A2A-to-MCP bridge or skill-as-MCP bridge | Future |
| Grounding | search/RAG/enterprise data | MCP retrieval/search tools or model-native grounding | Externalized |
| Live streaming audio/video | Gemini Live API integration | runtime/UI adapter; not core SOP execution | Gap, orthogonal |
| Multimodal inputs | text/audio/image/video parts | model/tool adapter concern; future message support | Partial/future |

## ADK 2.0 Through a Proceda Lens

ADK 2.0 adds three headline features: graph-based workflows, collaborative agents, and dynamic workflows. All three are responses to the same underlying problem: prompt-only agents get hard to control as tasks become longer, branchier, and more stateful.

Proceda agrees with that diagnosis. The disagreement is about the primary abstraction.

ADK says:

```python
root_agent = Workflow(
    name="routing_workflow",
    edges=[
        ("START", process_message, router),
        (router, {
            "BUG": handle_bug,
            "CUSTOMER_SUPPORT": handle_support,
            "LOGISTICS": handle_logistics,
        }),
    ],
)
```

Proceda says:

```markdown
### Step 1: Classify the request
Classify the user's request as BUG, CUSTOMER_SUPPORT, LOGISTICS, or MULTIPLE.
If more than one category applies, list all categories and handle them in this order:
BUG, CUSTOMER_SUPPORT, LOGISTICS.

### Step 2: Handle bug work if needed
[OPTIONAL]
If the classification includes BUG, complete the bug handling procedure.
Otherwise call `complete_step` with "No bug handling needed."

### Step 3: Handle customer support work if needed
[OPTIONAL]
If the classification includes CUSTOMER_SUPPORT, complete the support procedure.
Otherwise call `complete_step` with "No support handling needed."

### Step 4: Handle logistics work if needed
[OPTIONAL]
If the classification includes LOGISTICS, complete the logistics procedure.
Otherwise call `complete_step` with "No logistics handling needed."
```

This is less formally elegant than a graph, but it is more auditable. A process owner can read it. A compliance reviewer can find the branch conditions. The question is not whether graphs are useful. The question is whether graph syntax should be the thing most teams write first.

For SOPs, the answer is usually no.

## Agent Definition

### ADK

ADK's central primitive is the agent object. An `LlmAgent` has a name, model, description, instruction, tools, optional generation config, optional schemas, optional planner, optional code executor, callbacks, and relationships to other agents. Agent Config YAML offers a no-code variant, but it still mirrors the code object model.

### Proceda

Proceda's central primitive is the skill:

- `Skill` is parsed from `SKILL.md`.
- `SkillStep` carries index, title, content, and markers.
- `Agent.from_path()` loads the skill.
- `Runtime` executes it.
- The system prompt includes the full skill definition and enforces step order.

The model is not normally part of the skill. It lives in `proceda.yaml`, because model choice is an execution environment concern. The same SOP should be runnable against different models without editing the SOP.

### Replacement Rule

For every ADK `LlmAgent`, ask:

1. Is this a durable role in the procedure?
2. Is it a subtask that needs separate context?
3. Is it just a function or API call?

Then map it:

| ADK pattern | Proceda pattern |
|---|---|
| One `LlmAgent` with tools | one `SKILL.md` |
| Several role agents in a sequence | several steps with role-specific instructions |
| Agent exists only to call an API | MCP tool |
| Agent exists to format output | final step with `output_fields` |
| Agent needs separate reusable procedure | sub-skill via MCP or future native sub-skill |

The common case becomes simpler. The uncommon case remains possible through tools or future composition.

## Orchestration

### Sequential Workflows

ADK has `SequentialAgent`. Proceda has numbered steps. This is the cleanest replacement:

```markdown
### Step 1: Extract
...

### Step 2: Validate
...

### Step 3: Submit
...
```

The harness already enforces step order. `Executor.execute()` advances only after the model calls `complete_step`, and the prompt explicitly says not to proceed until the current step is complete.

This is better than an SDK abstraction for SOPs because the step list is the process.

### Loop Workflows

ADK has `LoopAgent`, max iterations, and stop conditions such as tool-based escalation.

Proceda can express bounded loops in two ways.

First, with explicit repeated steps:

```markdown
### Step 1: Draft
Write the initial answer.

### Step 2: Review
Assess the draft against the acceptance criteria. If it passes, call
`skip_remaining_steps` with the final answer.

### Step 3: Revise
Fix the issues found in review.

### Step 4: Final review
If the draft still fails, explain the remaining gaps. Otherwise finalize.
```

Second, with a tool that performs deterministic looping:

```markdown
### Step 2: Validate until stable
Call `validator__run_until_pass` with the draft and the acceptance criteria.
Use the returned final draft and validation log in the next step.
```

The first is appropriate when the loop is editorial or judgment-heavy. The second is appropriate when the loop is computational.

Current gap: Proceda does not have a native `[LOOP max=5]` marker. It can be added without changing the thesis:

```markdown
### Step 2: Refine until accepted
[LOOP max=5 until="quality_score >= 8"]
Review and revise the draft.
```

The important point is that this is optional syntax on top of SOP authoring, not a return to code-first agent construction.

### Parallel Workflows

ADK has `ParallelAgent`, graph fan-out, joins, and dynamic `asyncio.gather`.

Proceda today does not run multiple LLM branches concurrently. If a model returns multiple tool calls, the executor processes them one by one. That is a real gap for workloads such as parallel research, many independent API calls, or multi-agent debate.

The replacement options are:

| Use case | Proceda strategy |
|---|---|
| Parallel API calls | one MCP tool does the concurrency internally |
| Parallel deterministic data gathering | MCP tool with fan-out/fan-in |
| Parallel LLM research branches | future `run_skill_parallel` / sub-skill branch execution |
| Parallel human reviews | external review system as MCP tool |

For SOP work, the lack of parallelism is often acceptable. Many procedures are intentionally sequential because they encode dependencies and approvals. For research or high-throughput workloads, Proceda needs a harness extension.

### Graph Routes

ADK graph workflows let nodes emit route values, and edges map routes to target nodes. They also support `JoinNode` and nested workflows.

Proceda's SOP equivalent is explicit conditional language:

```markdown
### Step 2: Decide path
Classify the case as LOW_RISK, MEDIUM_RISK, or HIGH_RISK.

### Step 3: Low-risk handling
[OPTIONAL]
If the case is LOW_RISK, complete auto-approval. Otherwise mark this step not applicable.

### Step 4: Medium-risk handling
[OPTIONAL]
If the case is MEDIUM_RISK, request manager review. Otherwise mark this step not applicable.

### Step 5: High-risk handling
[OPTIONAL]
[PRE-APPROVAL REQUIRED]
If the case is HIGH_RISK, escalate to compliance before any external action.
```

This works when branch count is small and branch criteria are meaningful to humans. It becomes weak when there are many routes, complex joins, or deeply nested DAGs. In those cases, Proceda should use one of two approaches:

- put the graph logic in a deterministic MCP tool and keep the skill at the process level
- add a small declarative branch extension to the skill format

For example:

```markdown
### Step 2: Route case
[ROUTES LOW_RISK -> 3, MEDIUM_RISK -> 4, HIGH_RISK -> 5]
Classify the case.
```

That future marker would still be an SOP annotation, not a Python graph.

### Dynamic Workflows

ADK dynamic workflows allow code-level control flow with decorators, `ctx.run_node`, deterministic execution IDs, checkpointing, resume behavior, and parallel node execution.

Proceda should not try to encode arbitrary Python control flow in Markdown. The replacement is architectural:

- business procedure stays in `SKILL.md`
- arbitrary deterministic computation goes into MCP tools
- durable execution/checkpointing becomes a harness/service feature

Example:

```markdown
### Step 2: Process all orders
Call `orders__process_batch` with the input batch ID. The tool must:
- process each order idempotently
- checkpoint each order by order ID
- return successful, failed, and skipped order IDs

If any order failed, call `request_clarification` with the failure summary.
```

This is a better boundary. The SOP does not pretend to be a programming language. It states the operational requirement and delegates machinery to a tool designed for machinery.

## Collaborative Agents

ADK 2.0 collaborative agent teams introduce coordinator agents and subagent modes:

- `chat`: full user interaction, manual return
- `task`: clarification-only interaction, automatic return
- `single_turn`: no user interaction, automatic return, parallel-capable

Proceda can replace this in three tiers.

### Tier 1: Roles as Steps

Most "multi-agent" systems are really multi-role procedures:

```markdown
### Step 1: Act as intake analyst
Extract facts and missing information.

### Step 2: Act as policy reviewer
Compare the facts to policy.

### Step 3: Act as approver
[APPROVAL REQUIRED]
Make the final recommendation.
```

One LLM context is enough. The role switch is explicit. The audit trail is clear.

### Tier 2: Tools as Specialists

If a specialist is deterministic or data-backed, make it a tool:

```markdown
### Step 2: Get risk score
Call `risk__score_case`. Use the returned score and explanation as the only
source of truth for risk classification.
```

### Tier 3: Skills as Subagents

If the specialist is itself a reusable procedure, expose it as a skill tool:

```yaml
apps:
  - name: skills
    transport: stdio
    command: ["proceda-mcp-skills", "--skills-dir", "./skills"]
```

Then:

```markdown
### Step 3: Run KYB review
Call `skills__run_skill` with `skill_name="kyb-review"` and the extracted company facts.
Use the returned decision and event-log path in the final summary.
```

This would replace ADK `AgentTool`, collaborative subagents, and a large portion of hierarchical multi-agent composition. It is not implemented as a native primitive today, but it is directly aligned with the current architecture because Proceda already has an embeddable `Agent` and event-driven `Runtime`.

## Tools and Integrations

ADK supports native function tools, long-running function tools, `AgentTool`, MCP toolsets, OpenAPI tools, built-in tools, Google Cloud toolsets, third-party MCP tools, and many observability/memory/database plugins.

Proceda's answer should be intentionally narrower:

> All external capability is a tool boundary. The standard tool boundary is MCP.

That gives one consistent rule:

| ADK tool kind | Proceda replacement |
|---|---|
| Python function tool | wrap it in an MCP server |
| Java/Go/TS function tool | wrap it in an MCP server |
| OpenAPI toolset | generate or configure an MCP server from the OpenAPI spec |
| Google Search | MCP search tool or model-native provider exposed behind MCP |
| Code execution | MCP sandbox/code executor |
| Computer use | MCP browser/computer tool |
| Database tools | MCP database server |
| RAG/search/vector DB | MCP retrieval server |
| AgentTool | skill-as-MCP-tool |
| Long-running tool | MCP job tool with polling or callback |
| Streaming tool | future streaming MCP/event integration |

This is more work than importing a Python function into ADK, but it buys important properties:

- tools are language-agnostic
- tools are reusable across skills
- tools can have their own dependencies and process isolation
- tools can be permissioned per skill with `required_tools`
- tools can be deployed independently

Proceda already has the key pieces:

- `MCPOrchestrator` connects stdio and HTTP apps
- MCP tools are converted to OpenAI-compatible schemas
- `required_tools` acts as a skill-level allowlist
- `security.tool_denylist` blocks globally dangerous tools
- `ToolExecutor` emits `tool.called`, `tool.completed`, and `tool.failed`

The most important missing pieces are tool progress, streaming output, richer auth events, and artifact persistence.

## Human Oversight

This is where Proceda is stronger than ADK for SOP workflows.

ADK supports human input through `RequestInput` workflow nodes and action confirmations for tools. Those are useful, but they live in code or framework configuration.

Proceda puts oversight in the procedure:

```markdown
### Step 3: Delete stale records
[PRE-APPROVAL REQUIRED]
Before deleting records, summarize exactly which records will be deleted and why.

### Step 4: Confirm deletion result
[APPROVAL REQUIRED]
After deletion, present the tool result for human review.
```

The harness enforces this:

- pre-approval pauses before step execution
- post-approval pauses after step completion
- rejection cancels the run
- skip can skip a step
- decisions are recorded as events and `ApprovalRecord`s

Clarification is also first-class:

- the LLM must call `request_clarification`
- `RunSession` moves to `AWAITING_INPUT`
- the `HumanInterface` supplies the answer
- clarification events are logged

For regulated workflows, this is a better authoring model. A compliance reviewer does not need to inspect Python callbacks to find human gates.

## State, Data, and Schemas

ADK has a rich state model:

- `Session`
- `session.state`
- state prefixes/scopes
- `output_key`
- `input_schema`
- `output_schema`
- `Event.output`, `Event.message`, and `Event.state`
- dynamic workflow node inputs/outputs

Proceda currently has a simpler model:

- `RunSession.messages`
- `RunSession.variables`
- `RunSession.step_tool_results`
- step summaries
- final `output_fields`
- event log payloads

For SOPs, this is often enough. A step can refer to prior step results because the full message context is preserved and trimmed by the context manager. Structured final outputs use `output_fields`, where the final `complete_step` or `skip_remaining_steps` summary must include literal XML tags.

Example:

```yaml
output_fields:
  - account_status
  - resolution
  - explanation
```

The final step must emit:

```xml
<account_status>active</account_status>
<resolution>approved</resolution>
<explanation>All checks passed.</explanation>
```

This is deliberately pragmatic. ADK's schema support is more formal, but the ADK docs also note important limitations around combining `output_schema` and tools on some models. Proceda's XML-tag approach is less elegant but robust for benchmark-style extraction and audit logs.

Current gap: Proceda lacks an explicit mutable key-value state API. A reasonable extension would add:

```markdown
state_fields:
  - case_type
  - risk_score
  - final_decision
```

and control tools:

```text
set_state(key, value)
get_state(key)
```

But this should be added carefully. Too much state machinery can recreate the framework complexity Proceda is trying to avoid.

## Memory

ADK has `MemoryService` for long-term knowledge across sessions, including Vertex AI memory-bank-style integrations.

Proceda should not put vector search into the core runtime. It should expose memory as an MCP capability:

```yaml
apps:
  - name: memory
    transport: http
    url: http://localhost:8088/mcp
```

Then a skill can say:

```markdown
### Step 1: Retrieve relevant history
Call `memory__search` for prior runs involving this customer and issue type.
Use only results with confidence above 0.8. Cite the run IDs used.
```

The event log already contains high-quality raw material for memory:

- skill name
- steps
- tool calls
- summaries
- final outputs
- approvals
- timestamps

The missing piece is indexing and retrieval. That belongs in a memory MCP server, not in the SOP executor.

## Artifacts

ADK has artifact services with versioning, namespaces, binary data, and implementations such as in-memory and GCS-backed services.

Proceda has partial support:

- `MCPArtifact` exists in `mcp/models.py`
- `ToolExecutor` carries artifact metadata in tool results
- `EventLogWriter.write_artifact()` exists
- the execution pipeline does not yet persist MCP artifact content into the run directory

The replacement path is straightforward:

1. Let MCP tools return artifacts with content, type, and name.
2. Persist them under `.proceda/runs/<run>/artifacts/`.
3. Emit `artifact.created` or extend `tool.completed` payloads with artifact paths.
4. Allow skills to reference artifacts in later steps.

For example:

```markdown
### Step 2: Generate report
Call `reports__render_pdf`. Save the returned PDF artifact.

### Step 3: Review report
[APPROVAL REQUIRED]
Present the report artifact path and the extracted totals for approval.
```

Artifacts are a harness feature, not an agent-definition feature. Proceda can add them without changing the skill thesis.

## Context Management

ADK has explicit context objects, context caching, context compression, invocation context, tool context, callback context, and state visibility rules.

Proceda keeps context simpler:

- messages are stored in `RunSession`
- critical messages preserve step prompts and clarification answers
- `ContextManager` trims message context for token budgets
- events capture runtime transitions

For most SOPs, this is better. Procedure execution wants continuity, not many context types.

The important future extensions are:

- summarizer-backed compaction instead of trimming only
- per-step context inclusion controls
- artifact and state references in prompts
- model-provider context caching where available

Those can be harness optimizations. They should not leak heavily into skill authoring.

## Events, Logs, Replay, and Runtime

ADK's `Runner` emits events and coordinates session services, callbacks, tools, state updates, streaming chunks, and final responses.

Proceda has a smaller but very direct runtime:

```text
Runtime
  creates RunSession
  connects MCP apps
  creates CompositeEventSink
  starts Executor

Executor
  builds system prompt
  runs one step at a time
  calls LLM
  handles control tools
  handles MCP tools
  handles approvals and clarifications
  emits RunEvents

EventLogWriter
  writes JSONL
  writes metadata
  writes summary
```

Proceda's event model is already well aligned with an SDK/harness story. It emits lifecycle, step, message, tool, human interaction, LLM usage, status, context, and summary events.

This gives Proceda a capability ADK does not foreground as strongly: `proceda replay`. A run can be reconstructed from local JSONL without calling the model or tools again.

The runtime gaps are mostly adapters:

- HTTP API server around `Runtime.start()`
- web UI or TUI around the event stream
- durable resume from snapshots
- external event sinks for OpenTelemetry, BigQuery, or partner systems
- ambient trigger runner

These are important, but they do not require code-first agent authoring.

## Callbacks and Plugins

ADK callbacks are powerful. They can intercept before/after agent execution, before/after model calls, before/after tools, and can implement guardrails, logging, caching, request modification, step skipping, auth, summarization control, and artifact handling. ADK plugins package these behaviors.

Proceda should be careful here. Callbacks are also a place where hidden control flow accumulates.

Replacement strategy:

| ADK callback use | Proceda replacement |
|---|---|
| Guardrail before action | explicit validation step + `[PRE-APPROVAL REQUIRED]` |
| Tool argument policy | MCP wrapper tool validates arguments |
| Logging/monitoring | event sink |
| Caching | MCP tool or model adapter |
| Request modification | skill instruction or wrapper tool |
| Conditional skip | step instruction + `skip_remaining_steps` |
| Auth | MCP tool auth flow |
| Artifact handling | artifact persistence in harness |

Proceda should still support extension hooks, but they should be framed as infrastructure hooks, not business logic hooks. Business policy belongs in the skill when it needs to be auditable.

## Runtime Surfaces

ADK provides several ways to run agents:

- CLI
- web interface
- API server
- ambient triggers
- deployed Agent Runtime
- Cloud Run
- GKE
- direct SDK runner

Proceda currently focuses on:

- Python SDK: `Agent.from_path()`, `.run()`, `.run_async()`, `.run_stream()`
- CLI: `proceda run`, `lint`, `convert`, `replay`, `doctor`
- terminal human interface
- JSONL event logs

The replacement plan is not to put all surfaces in the core. It is:

```text
Core runtime:
  Agent / Runtime / Executor / RunEvent / HumanInterface

Adapters:
  CLI
  TUI
  HTTP API
  web UI
  trigger worker
  hosted control plane
```

This is the right split. The agent definition should not change depending on whether the procedure is run from a terminal, web UI, queue trigger, or hosted service.

## Evaluation

ADK has a broad evaluation system:

- eval files and eval sets
- trajectory/tool-use scoring
- response matching
- rubric-based response quality
- hallucination and safety criteria
- user simulation
- environment simulation
- custom metrics
- CLI, pytest, and web UI evaluation flows

Proceda has the foundation, but not the packaged eval product:

- `CollectorEventSink` captures events in tests
- `ScriptedHumanInterface` supports deterministic human responses
- event logs preserve trajectory
- SOP-Bench docs and tests show domain-level task success evaluation
- `output_fields` make final answer extraction deterministic enough for benchmark scoring

The replacement path is clear:

```text
proceda eval ./skills/refund-review --cases cases.jsonl
  -> run each case
  -> collect RunEvents
  -> extract output_fields
  -> compare expected tool trajectory / final outputs
  -> produce report
```

ADK's evaluation system is broader today. Proceda's advantage can be sharper: evaluate whether a written SOP was followed, whether approvals occurred, whether required tools were called, and whether final fields match expected operational outcomes.

## Safety and Security

ADK's safety docs cover identity, authorization, guardrails, sandboxed code execution, evaluation, network controls, and other deployment risks.

Proceda's current controls are narrower but well matched to local SOP execution:

- `required_tools` in a skill acts as an allowlist
- `security.tool_denylist` blocks globally dangerous tools
- `[PRE-APPROVAL REQUIRED]` pauses before risky steps
- `[APPROVAL REQUIRED]` pauses after sensitive steps
- `logging.redact_secrets` redacts secret-like keys
- MCP servers isolate tool implementation
- terminal UX makes approvals visible

For production, the missing pieces are:

- identity-aware human approvals
- per-tool and per-argument policy checks
- sandbox profiles for code/computer tools
- signed or trusted skills
- remote skill allowlists
- centralized audit export
- stronger secret management

Again, these are harness and deployment features. They do not require agent behavior to be authored as code.

## Streaming and Multimodal

ADK has substantial Gemini Live API support: bidirectional streaming, audio, video, image input, voice configuration, VAD, transcription, streaming tools, session resumption, and modality configuration.

Proceda should treat these as I/O adapters and model-provider features. A skill should not care whether the user's clarification arrives as typed text or transcribed audio:

```markdown
### Step 2: Ask for missing account detail
If the account number is missing, call `request_clarification`.
```

The human interface can be terminal, web, voice, or API. The skill is unchanged.

Today Proceda is text-first. Adding live voice/video would require:

- multimodal message parts in `RunMessage`
- streaming event payloads
- a live `HumanInterface`
- model adapter support
- UI transport such as WebSocket

This is real work, but it is not central to the procedure-first thesis.

## A2A and Remote Agents

ADK supports Agent2Agent for exposing and consuming remote agents. This is useful when an agent is owned by another team, runs in another service boundary, or needs a formal remote protocol.

Proceda has two natural replacements:

1. Remote A2A agent as an MCP tool:

   ```markdown
   ### Step 3: Ask remote catalog agent
   Call `catalog_agent__query` with the SKU and customer region.
   ```

2. Proceda skill exposed as MCP or A2A:

   ```text
   proceda serve --skills-dir ./skills --protocol mcp
   proceda serve --skills-dir ./skills --protocol a2a
   ```

The first path lets Proceda consume ADK/A2A agents without changing skill syntax. The second lets other systems call Proceda procedures. Both are adapter work.

## Grounding and RAG

ADK supports Google Search grounding, Vertex AI Search, Agentic RAG, RAG Engine, vector databases, database tools, and enterprise retrieval integrations.

Proceda's replacement is MCP retrieval:

```markdown
### Step 1: Gather authoritative sources
Call `search__query` for current public sources.
Call `kb__retrieve` for internal policy documents.
Use only cited retrieved material for factual claims.
```

Grounding is not an agent framework abstraction. It is access to trusted external data plus instructions about how to use it. MCP is the right boundary.

## Deployment and Infrastructure

ADK bundles extensive deployment guidance for Agent Runtime, Cloud Run, GKE, API servers, web UI, and Google Cloud integration.

Proceda should not compete by adding cloud-specific logic to skills. A skill should be portable:

```text
local CLI
hosted web app
queue worker
cron job
internal service
```

all executing the same `SKILL.md`.

The deployment replacement is:

- package the skill and `proceda.yaml`
- provide required MCP servers
- run `proceda run` or embed `Runtime`
- export JSONL events and artifacts
- use the hosted product for team governance when needed

Cloud deployment is necessary for production, but it is orthogonal to agent authoring.

## Concrete Authoring Patterns

### 1. Sequential Pipeline

ADK:

```python
pipeline = SequentialAgent(sub_agents=[extractor, validator, submitter])
```

Proceda:

```markdown
### Step 1: Extract
...

### Step 2: Validate
...

### Step 3: Submit
...
```

### 2. Router

ADK:

```python
Event(route="HIGH_RISK")
```

Proceda:

```markdown
### Step 1: Classify risk
Classify as LOW_RISK, MEDIUM_RISK, or HIGH_RISK.

### Step 2: Low-risk path
[OPTIONAL]
Run only if risk is LOW_RISK.

### Step 3: High-risk path
[OPTIONAL]
[PRE-APPROVAL REQUIRED]
Run only if risk is HIGH_RISK.
```

### 3. HITL Approval

ADK:

```python
yield RequestInput(message="Approve this request?")
```

Proceda:

```markdown
### Step 4: Execute irreversible action
[PRE-APPROVAL REQUIRED]
Summarize the action and wait for approval before proceeding.
```

### 4. Function Tool

ADK:

```python
def check_policy(amount: float) -> dict:
    ...

agent = Agent(tools=[check_policy])
```

Proceda:

```yaml
apps:
  - name: policy
    transport: stdio
    command: ["python", "-m", "policy_mcp"]
```

```markdown
required_tools:
  - policy__check_policy
```

### 5. Long-Running Job

ADK:

```python
LongRunningFunctionTool(...)
```

Proceda:

```markdown
### Step 2: Start job
Call `jobs__start_export`. Record the returned job ID.

### Step 3: Wait for completion
Call `jobs__poll_export` until it returns COMPLETE or FAILED. If it fails,
call `request_clarification` with the failure reason.
```

### 6. Memory

ADK:

```python
memory_service.search_memory(...)
```

Proceda:

```markdown
### Step 1: Search prior runs
Call `memory__search_runs` with the customer ID and issue type. Use only
results with cited run IDs.
```

### 7. Agent Team

ADK:

```python
root = Agent(sub_agents=[weather_agent, flight_agent])
```

Proceda:

```markdown
### Step 1: Weather specialist pass
Act as the weather specialist. Use weather tools only.

### Step 2: Flight specialist pass
Act as the flight specialist. Use flight tools only.

### Step 3: Coordinator pass
Combine both findings and produce the itinerary recommendation.
```

### 8. Evaluation

ADK:

```text
adk eval
```

Proceda:

```text
uv run pytest tests/test_runtime/test_executor.py
proceda run ./skills/case --var case_id=...
proceda replay <run_id>
```

Future:

```text
proceda eval ./skills/case --cases ./eval/cases.jsonl
```

## Where Proceda Is Already Better

### Human-in-the-Loop Visibility

Approval markers are in the document. A process owner can search for `[APPROVAL REQUIRED]`. ADK can implement equivalent behavior, but the oversight point is usually hidden in code, graph nodes, or tool configuration.

### SOP Linting and Conversion

Proceda has skill parsing, linting, and conversion workflows around SOP documents. ADK has Agent Config and Skills, but its center of gravity is still agent construction. Proceda's authoring loop is closer to how procedures already exist in organizations.

### Replay as a Product Primitive

Proceda's event log and `proceda replay` make every run inspectable after the fact. This is a strong foundation for audit, debugging, and benchmark scoring.

### MCP-First Boundary

ADK supports MCP, but also has many internal tool abstractions. Proceda can keep one tool boundary and make the rest of the system simpler.

## Where ADK Is Still Broader

Proceda should be honest about gaps.

| Gap | Why it matters | Proceda path |
|---|---|---|
| Parallel LLM/subagent execution | faster fan-out, debate, research, branch isolation | sub-skill runner with parallel branches |
| Durable resume/checkpointing | long workflows, failures, human delays | snapshot store + resume command |
| Native graph branches/joins | complex process graphs | optional route/join markers or graph compiler |
| Dynamic workflow checkpointing | arbitrary code workflows | durable MCP job tools or workflow adapter |
| Explicit state service | reliable cross-step data passing | small typed state API |
| Artifact persistence | generated files and binary outputs | wire MCP artifacts to run directory |
| Long-term memory | personalization and cross-run recall | memory MCP server over event logs/vector DB |
| Tool auth events | OAuth/OIDC user credential flows | auth-aware MCP protocol/human interface |
| Streaming tools | progress updates and live monitoring | streaming MCP/event support |
| Web/API/ambient runtime | production integrations | adapters over `Runtime.start()` |
| Eval product | systematic benchmark workflow | `proceda eval` |
| Observability export | enterprise telemetry | event sinks for OTel/BigQuery/etc. |
| Multimodal/live | voice/video agents | model and UI adapters |
| A2A | remote agent interoperability | A2A-MCP bridge and skill server |

The pattern is important: most gaps are harness/adaptor gaps, not failures of SOP authoring.

## Detailed Engineering Gap Notes

This section folds in the sharper engineering notes from the older ADK mapping document. The goal is to make the consolidated artifact useful not only as positioning, but also as an implementation checklist for closing practical parity gaps.

### Execution and Concurrency

Proceda's executor is intentionally sequential today:

- `Executor.execute()` walks `session.current_step` from Step 1 through the final step.
- Each step runs an LLM loop until the model calls `complete_step` or `skip_remaining_steps`.
- Control tools are injected on every LLM call: `complete_step`, `request_clarification`, and `skip_remaining_steps`.
- App tools are MCP tools exposed by `MCPOrchestrator`.
- When an LLM response contains multiple tool calls, `Executor._execute_step()` iterates through `response.tool_calls` and awaits each call in order.

That means Proceda has no current equivalent to:

- ADK `ParallelAgent`
- graph fan-out from multiple `START` edges
- graph `JoinNode`
- dynamic workflow `asyncio.gather`
- ADK 2.0 `single_turn` subagents running in parallel

This is the highest-leverage implementation gap. It has two levels:

| Level | Current behavior | Upgrade |
|---|---|---|
| Parallel tool calls | Multiple tool calls are processed sequentially | detect independent app tool calls and run them with `asyncio.gather` |
| Parallel LLM branches | One active LLM loop per run | add child run handles / sub-skill branches with isolated contexts |

The first upgrade is relatively contained. The executor already receives a list of tool calls in one model response; independent app calls could be grouped and awaited concurrently while preserving event emission. The second upgrade is more substantial because it needs child sessions, branch IDs, merged results, and clearer event semantics.

### Runtime Guard Rails

Proceda already has useful operational guard rails:

- hard LLM loop cap: `MAX_TOOL_CALL_ITERATIONS = 50`
- text-only nudge behavior for non-progress responses
- empty-response recovery with temperature escalation
- per-step app tool call limit, defaulting to 20
- human error recovery after the tool-call limit is exceeded: retry, skip, or cancel
- deterministic tool access denial through `required_tools` and `security.tool_denylist`

These guard rails are different from ADK callbacks. They protect the execution loop from runaway behavior, but they do not let developers run arbitrary deterministic policy code before every model or tool call.

The missing enforcement hooks are:

| Hook | ADK equivalent | Proceda status | Practical fix |
|---|---|---|---|
| before model | `before_model_callback` | no hook | event-sink-only is insufficient; add optional model policy hook if needed |
| after model | `after_model_callback` | no hook | can be approximated with review steps; add only for deterministic filters |
| before tool | `before_tool_callback` | denylist/allowlist only | add policy hook or require wrapper MCP tools |
| after tool | `after_tool_callback` | event sink observes result | event sink is enough for logging; hook needed for mutation/retry |

The SOP-first rule should be: business policy that must be audited belongs in the skill; deterministic enforcement that must happen before the model or tool sees data belongs in the harness or MCP wrapper.

### Tool Access, Auth, and Confirmation

Proceda's tool model is narrower and cleaner than ADK's:

- ADK has function tools, long-running tools, OpenAPI tools, built-ins, toolsets, MCP tools, and agent tools.
- Proceda treats MCP as the tool boundary.

Current Proceda strengths:

- `proceda.yaml` supports MCP app configuration over stdio and HTTP.
- environment variable expansion supports `${VAR}` and `${VAR:-default}`.
- `required_tools` in `SKILL.md` becomes a skill-level allowlist.
- `security.tool_denylist` blocks tools globally with glob patterns.
- tool events are emitted for call, completion, and failure.

Current gaps:

| Capability | ADK | Proceda today | Engineering note |
|---|---|---|---|
| per-call auth | tool auth schemes and `ToolContext` | MCP server owns auth | keep auth in MCP where possible |
| OAuth/OIDC user flows | framework can pause for credentials | no auth-specific runtime event | add auth request/response events if needed |
| per-tool confirmation | `require_confirmation` / `request_confirmation` | step-level approval markers | step-level is better for SOP audit, weaker for conditional per-call approval |
| conditional confirmation | e.g. confirm only amount > threshold | write condition in step or MCP wrapper | deterministic threshold checks should live in wrapper tool or future policy hook |
| remote confirmation | REST confirmation flow | custom `HumanInterface` can approximate | formalize remote human interface adapter |
| streaming tool output | streaming tools / Live API | blocking MCP result | add tool progress events or polling job pattern |

For SOPs, step-level approval is usually the right unit. If a tool call is risky enough to require confirmation, the procedure should usually have a named step describing the action. For low-level dynamic policies, use an MCP wrapper first and a Proceda hook only if wrapper tools become too cumbersome.

### State and Data Passing

ADK has a richer state model than Proceda:

- session state
- user-scoped state with `user:` prefixes
- app-scoped state with `app:` prefixes
- temporary state with `temp:` prefixes
- `output_key` for passing agent output to later agents
- `input_schema` and `output_schema`
- workflow `Event.output`, `Event.message`, and `Event.state`

Proceda's current state model is simpler:

- `RunSession.variables`
- `RunSession.messages`
- `RunSession.step_tool_results`
- step summaries emitted through `complete_step`
- final `output_fields`
- event log payloads

This is enough for many single-run SOPs because the message history and step summaries carry the working context forward. It is weaker for workflows that need precise key-value data across steps.

Important engineering nuance: variables are currently provided to the model in the system prompt. They are not a deterministic template-rendering pass over the skill body. If Proceda wants direct parity with ADK instruction templating, it should add a small pre-rendering layer with clear escaping and missing-variable behavior.

Recommended state additions:

```yaml
state_fields:
  - case_type
  - risk_score
  - final_decision
```

Potential control tools:

```text
set_state(key, value)
get_state(key)
```

This should stay small. If Proceda grows ADK's full state namespace system, it risks recreating the framework surface it is trying to avoid.

### Context Compaction

Proceda has token-budget trimming today:

- `ContextManager.trim_messages()` preserves system, critical, and recent messages.
- It adds a truncation notice when older messages are dropped.
- `ContextManager.summarize_completed_step()` exists but is not currently wired into the executor.

ADK has broader context machinery, including caching and compression options. Proceda does not need the full surface immediately, but it should wire completed-step summarization because SOPs naturally produce step boundaries.

Suggested implementation:

1. After a step completes, gather messages associated with that step.
2. Preserve the `complete_step` summary and important tool result metadata.
3. Replace non-critical intra-step chatter with a compact system summary.
4. Keep approval, clarification, and final output messages as critical.

This would improve long SOP reliability without adding new authoring concepts.

### Memory

Proceda's event logs are a strong raw memory substrate:

- full step lifecycle
- tool calls and results
- approvals and decisions
- final summaries
- timestamps and run metadata

The missing layer is semantic indexing and retrieval. The older mapping doc's recommendation still holds: implement memory as an MCP server, not as a core runtime feature.

Minimum useful memory toolset:

| Tool | Purpose |
|---|---|
| `memory__index_run` | index a completed event log |
| `memory__search_runs` | retrieve prior runs by semantic query and filters |
| `memory__get_run_summary` | load a cited prior run summary |
| `memory__get_user_profile` | retrieve durable user/customer facts |
| `memory__update_user_profile` | write durable user/customer facts with provenance |

The skill should decide when memory is relevant:

```markdown
### Step 1: Recall prior context
Call `memory__search_runs` for prior cases involving {customer_id}. Use only
results that cite run IDs and have relevance above 0.8.
```

### Artifacts

The old mapping doc correctly identified this as "plumbing exists; wiring does not."

Current pieces:

- `MCPArtifact` has `content_type`, `content`, and optional `name`.
- `MCPToolResult` can include artifacts.
- `ToolExecutor` includes artifact metadata in the tool result passed back to the session.
- `EventLogWriter.write_artifact()` can write artifact files.
- The runtime does not currently persist artifact content during tool execution.

Minimum artifact parity:

1. Persist artifacts returned by MCP tools into `.proceda/runs/<run>/artifacts/`.
2. Emit artifact metadata with path, content type, source tool call, and version.
3. Include artifact paths in tool result messages so later steps can refer to them.
4. Add replay rendering for artifact creation.

ADK's artifact services are broader, especially around versioning and user/session namespacing. Proceda can cover SOP needs first with run-scoped artifacts, then add user-scoped artifacts later if memory/profile use cases require them.

### Sub-Skill Composition

Sub-skill composition is the most important abstraction gap if Proceda wants to replace multi-agent systems without becoming graph-first.

The practical design is a skill runner exposed as MCP:

```yaml
apps:
  - name: skills
    transport: stdio
    command: ["proceda", "serve", "--protocol", "mcp", "--skills-dir", "./skills"]
```

Tool shape:

```text
skills__run_skill(skill_name, variables, input, mode)
```

Useful modes:

| Mode | Behavior |
|---|---|
| `inline` | return final summary and output fields |
| `linked` | return final summary plus child run ID/path |
| `isolated` | child skill gets separate context and tool policy |
| `parallel` | multiple child skills can run concurrently and join |

This single capability would cover a large portion of ADK `AgentTool`, subagents, collaborative task agents, and hierarchical agent patterns while keeping `SKILL.md` as the unit of reuse.

### Graph and Dynamic Workflow Boundary

The older mapping doc called graph/dynamic workflows a high gap. The consolidated report softens this to "partial" because many graph use cases can be written as conditional SOP steps. Both are true depending on the workload.

Engineering rule:

- If the branch logic is policy logic a human should understand, keep it in the skill.
- If the branch logic is algorithmic, high-cardinality, or join-heavy, put it in an MCP tool or add a declarative route marker.

Potential route marker:

```markdown
### Step 2: Route case
[ROUTES LOW_RISK -> 3, MEDIUM_RISK -> 4, HIGH_RISK -> 5]
Classify the case and state the route.
```

Potential join marker:

```markdown
### Step 6: Join investigation branches
[JOIN steps=3,4,5]
Combine the branch outputs into one recommendation.
```

These should be optional procedural annotations, not a full graph DSL.

### Human Interface and Testing

Proceda has a stronger test story for human-in-the-loop SOPs than a naive prompt runner:

- `TerminalHumanInterface` for real interactive runs
- `AutoApproveHumanInterface` for tests and simple automation
- `ScriptedHumanInterface` for deterministic approval and clarification sequences
- `CollectorEventSink` for asserting emitted events

This maps well to ADK's eval/user-simulation direction, but Proceda should package it more explicitly:

```text
proceda eval ./skills/refund-review --cases cases.jsonl
```

Case file fields should include:

- variables
- input prompt
- scripted approvals
- scripted clarifications
- expected output fields
- expected required tool calls
- forbidden tool calls
- expected approval points

This would make Proceda's evaluation story SOP-native rather than generic conversation-eval-native.

### Developer Surfaces

ADK is stronger today on interactive developer surfaces:

- `adk web`
- `adk api_server`
- deployed Agent Runtime
- web eval tooling

Proceda is stronger on SOP-specific terminal tooling:

- `proceda run`
- `proceda lint`
- `proceda convert`
- `proceda replay`
- `proceda doctor`

The engineering takeaway is not "copy ADK Web." It is:

1. Keep CLI/TUI as the OSS center.
2. Add an HTTP adapter over `Runtime.start()` and the event stream.
3. Let hosted Proceda own collaboration, auth, and long-term run management.
4. Keep `SKILL.md` unchanged across all runtime surfaces.

### Prioritized Engineering Checklist

If the goal is practical ADK parity for SOP workflows, the order should be:

| Priority | Work | Why |
|---|---|---|
| P0 | skill-as-MCP-tool / `proceda serve` | unlocks subagents, composition, and reuse |
| P1 | concurrent independent tool calls | easy performance win; closes part of parallel gap |
| P2 | artifact persistence | required for document/report workflows |
| P3 | completed-step context compaction | improves long SOP reliability |
| P4 | explicit small state API | closes `output_key` / session state gap |
| P5 | memory MCP server over event logs | closes long-term memory without bloating core |
| P6 | optional before-tool policy hook | deterministic enforcement for high-stakes tools |
| P7 | `proceda eval` | turns event logs and output fields into a benchmark loop |
| P8 | API server / trigger adapter | covers production integration surfaces |

This list intentionally prioritizes capabilities that preserve the SOP-first model. Native graph editing, broad plugin APIs, and model-provider-specific streaming should come later unless a concrete customer workflow demands them.

## Minimal Roadmap to Match ADK's Practical Surface

### Phase 1: Make Current Proceda Feel Complete

- Document the ADK replacement patterns in examples.
- Add artifact persistence from MCP tool results.
- Add a small explicit state mechanism.
- Add per-step final-output validation for `output_fields`.
- Add event sink examples for telemetry export.
- Add more skill examples for branch, loop, approval, and memory patterns.

### Phase 2: Add Composition

- `proceda serve --protocol mcp` to expose skills as tools.
- `skills__run_skill` MCP bridge.
- sub-skill call metadata in event logs.
- optional isolated child contexts.
- parallel sub-skill execution for independent branches.

### Phase 3: Add Durability

- periodic `RunSession` snapshots.
- `proceda resume <run_id>`.
- idempotent tool-call records.
- durable pending approval/clarification states.
- job-style long-running MCP tools.

### Phase 4: Add Evaluation

- `proceda eval`.
- case files with variables, scripted human responses, expected output fields, and expected tool trajectories.
- evaluator plugins as Python callables or MCP tools.
- HTML/Markdown eval reports.

### Phase 5: Add Runtime Adapters

- HTTP API server.
- TUI or web event viewer.
- trigger runner for queues/webhooks.
- A2A bridge.
- streaming event transport.

This roadmap would cover the practical capability set of ADK while preserving the central product idea: author behavior as SOPs, not as agent graphs.

## The Strong Form of the Thesis

The strong claim is not:

> Proceda already has every feature ADK has.

That is false.

The strong claim is:

> ADK's feature set decomposes into procedure authoring, tool access, human control, runtime events, persistence, and deployment adapters. For SOP-shaped agents, procedure authoring should be Markdown-first, tools should be MCP, and the runtime should be a small harness. Most of the remaining ADK surface is either infrastructure or an escape hatch for workflows that should be tools.

This is defensible.

ADK is excellent when the developer is building a software-defined agent system. Proceda is better when the team is operationalizing a procedure. The more the work resembles a checklist, runbook, review process, compliance workflow, intake flow, investigation, or approval chain, the more wasteful code-first agent frameworks become.

The product opportunity is to make `SKILL.md` feel as capable as an agent SDK without making it look like one.

## Final Positioning

Proceda should position itself as:

> The SOP-native agent runtime. Write the procedure. Attach MCP tools. Run it with approvals, events, logs, and replay.

Against ADK:

> ADK asks engineers to build agents out of code objects. Proceda asks teams to write the procedure they already understand, then executes it as an agent.

Against LangGraph/LangChain:

> Graphs and chains are implementation details. SOPs are the durable product artifact.

Against no-code workflow tools:

> Proceda remains developer-grade: local-first, Python-native, MCP-native, evented, testable, and embeddable.

The practical north star is simple:

```text
If you can write the SOP clearly, Proceda should be able to run it.
```
