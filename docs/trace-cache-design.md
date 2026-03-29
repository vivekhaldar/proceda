# Trace Harvesting & Step-Level Execution Cache

## 1. Problem

Every SOP execution currently runs the full LLM reasoning loop for every step, even when the same SOP has been executed successfully many times before. Looking at real SOP-Bench traces, many steps are pure data pipelines — the LLM just maps variables to tool arguments, calls a tool, and moves on. This is expensive (tokens) and slow (latency) for no reason after the pattern has been established.

**Example: `dangerous_goods` SOP**

Across multiple successful runs of this 7-step SOP, steps 2-5 always follow the identical pattern:

```
Step 2: Call calculate_sds_label_score(product_id=$var.product_id, sds_label_text=$var.sds_label_text) → capture sds_label_score
Step 3: Call calculate_handling_score(product_id=$var.product_id, handling_and_storage_guidelines=$var.handling_and_storage_guidelines) → capture handling_score
Step 4: Call calculate_transportation_score(product_id=$var.product_id, transportation_requirements=$var.transportation_requirements) → capture transportation_score
Step 5: Call calculate_disposal_score(product_id=$var.product_id, disposal_guidelines=$var.disposal_guidelines) → capture disposal_score
```

Steps 1, 6, and 7 use no tools at all — they're pure reasoning/arithmetic. The LLM is doing rote mapping work that could be cached.

**Goal**: After N successful executions, extract per-step execution recipes from traces and use them to reduce LLM calls on subsequent runs.

## 2. Design Overview

Two optimization levels, applied per-step (not per-run):

| Level | Name | How it works | Token savings | Risk |
|-------|------|-------------|---------------|------|
| 1 | Hint Injection | Inject a text hint from prior traces into the step prompt. LLM still reasons, but converges in 1 iteration instead of 2-4. | ~30-50% | None |
| 2 | Direct Execution | Execute cached tool calls directly, then make ONE validation LLM call. | ~60-80% | Medium (fallback required) |

Level 3 (fully deterministic, zero LLM) is explicitly out of scope — if a step is that deterministic, it should be written as code, not an SOP step.

### Architecture

```
                    ┌─────────────────────────┐
                    │     Runtime.start()      │
                    │                          │
                    │  Loads SkillCache from   │
                    │  .proceda/cache/         │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   Executor.execute()     │
                    │                          │
                    │  For each step:          │
                    │  1. Check cache          │
                    │  2a. Cache hit (L1):     │
                    │      inject hint →       │
                    │      normal LLM loop     │
                    │  2b. Cache hit (L2):     │
                    │      execute recipe →    │
                    │      validation LLM call │
                    │  2c. Cache miss:         │
                    │      normal LLM loop     │
                    └────────────┬─────────────┘
                                 │
                                 ▼ (after successful run)
                    ┌─────────────────────────┐
                    │   Auto-build cache       │
                    │   if enough traces exist  │
                    └─────────────────────────┘
```

### New Module: `src/proceda/cache/`

```
src/proceda/cache/
├── __init__.py          # Exports: SkillCache, CacheStore, TraceAnalyzer
├── models.py            # Data models: StepRecipe, ToolCallRecipe, ArgumentMapping, etc.
├── analyzer.py          # TraceAnalyzer: reads N traces → produces SkillCache
├── store.py             # CacheStore: reads/writes SkillCache to disk as JSON
└── executor.py          # RecipeExecutor: executes a StepRecipe (Level 2 only)
```

### Files to Modify

| File | What changes |
|------|-------------|
| `src/proceda/llm/prompts.py` | `build_step_prompt()` gains `hint: str \| None` parameter |
| `src/proceda/internal/executor.py` | `Executor.__init__()` accepts `SkillCache`; `_execute_step()` consults cache |
| `src/proceda/runtime.py` | `Runtime.start()` loads cache; after run, triggers auto-build |
| `src/proceda/events.py` | Three new `EventType` values for cache observability |
| `src/proceda/config.py` | New `CacheConfig` dataclass, added to `ProcedaConfig` |
| `src/proceda/cli/main.py` | New `cache` subcommand group with `build` and `clear` |

---

## 3. Data Models (`src/proceda/cache/models.py`)

```python
"""ABOUTME: Data models for step-level execution cache.
ABOUTME: Defines recipes that encode tool call patterns extracted from traces."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class ArgumentSourceType(enum.Enum):
    """Where a tool call argument value comes from."""

    VARIABLE = "variable"          # From session.variables[key]
    STEP_RESULT = "step_result"    # From a prior step's tool result
    LITERAL = "literal"            # A fixed constant value


@dataclass
class ArgumentMapping:
    """Describes how to resolve a single tool call argument at runtime.

    Fields:
        source: Where the value comes from (variable, step_result, or literal).
        key: For VARIABLE: the variable name (e.g., "product_id").
             For STEP_RESULT: a dotted path "step[N].field_name"
                 (e.g., "step[2].sds_label_score").
             For LITERAL: unused (set to "").
        literal_value: The fixed value, only used when source == LITERAL.
    """

    source: ArgumentSourceType
    key: str = ""
    literal_value: Any = None


@dataclass
class ToolCallRecipe:
    """Encodes a single tool call to make during a step.

    Fields:
        tool_name: Fully qualified MCP tool name (e.g., "calculate_sds_label_score").
        argument_mappings: Maps argument name → how to resolve its value.
            Example: {"product_id": ArgumentMapping(VARIABLE, "product_id"),
                      "sds_label_text": ArgumentMapping(VARIABLE, "sds_label_text")}
    """

    tool_name: str
    argument_mappings: dict[str, ArgumentMapping] = field(default_factory=dict)


@dataclass
class ResultCapture:
    """Describes a value to extract from a tool call result for use by later steps.

    Fields:
        field_name: The key to extract from the tool result JSON.
            Example: "sds_label_score"
        from_tool_call_index: Which tool call in this step's sequence (0-indexed).
            Usually 0 since most steps make one tool call.
    """

    field_name: str
    from_tool_call_index: int = 0


@dataclass
class StepRecipe:
    """A cached execution recipe for a single step.

    Fields:
        step_index: 1-indexed step number matching the SKILL.md.
        tool_call_recipes: Ordered list of tool calls to make. Empty list
            means this step uses no tools (pure reasoning — not cacheable at L2).
        result_captures: Values to extract from tool results for later steps.
        summary_template: Template string for complete_step summary. May contain
            {placeholders} that get filled from variables and step results.
        confidence: 0.0-1.0 score from trace analysis. Computed as:
            (number of traces with matching pattern) / (total analyzed traces).
            Only recipes with confidence >= min_confidence are used.
    """

    step_index: int
    tool_call_recipes: list[ToolCallRecipe] = field(default_factory=list)
    result_captures: list[ResultCapture] = field(default_factory=list)
    summary_template: str = ""
    confidence: float = 0.0


@dataclass
class SkillCache:
    """The complete cache for a skill — one StepRecipe per step.

    Fields:
        skill_id: Matches Skill.id from parser.
        skill_content_hash: SHA256 of the SKILL.md raw content. Used for
            invalidation — if the hash doesn't match the current SKILL.md,
            the cache is stale and must be discarded.
        step_recipes: Maps step_index → StepRecipe. Steps with no recipe
            (or low confidence) are omitted.
        source_run_ids: Run IDs that were analyzed to produce this cache.
        created_at: ISO timestamp of when the cache was built.
    """

    skill_id: str
    skill_content_hash: str
    step_recipes: dict[int, StepRecipe] = field(default_factory=dict)
    source_run_ids: list[str] = field(default_factory=list)
    created_at: str = ""

    def get_recipe(self, step_index: int) -> StepRecipe | None:
        """Return recipe for a step, or None if not cached."""
        return self.step_recipes.get(step_index)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        # Implementation: recursively convert dataclasses to dicts,
        # converting enums to their .value strings.
        ...

    @staticmethod
    def from_dict(data: dict[str, Any]) -> SkillCache:
        """Deserialize from a JSON dict."""
        ...
```

### Serialization Format (`.proceda/cache/{skill_id}.json`)

```json
{
  "skill_id": "e9ac31854bee661d",
  "skill_content_hash": "a1b2c3d4...",
  "source_run_ids": ["run_50c565a293ea", "run_10e6be757074", "run_abc123def456"],
  "created_at": "2026-03-29T10:00:00+00:00",
  "step_recipes": {
    "2": {
      "step_index": 2,
      "tool_call_recipes": [
        {
          "tool_name": "calculate_sds_label_score",
          "argument_mappings": {
            "product_id": {"source": "variable", "key": "product_id"},
            "sds_label_text": {"source": "variable", "key": "sds_label_text"}
          }
        }
      ],
      "result_captures": [
        {"field_name": "sds_label_score", "from_tool_call_index": 0}
      ],
      "summary_template": "Calculated SDS label score for product {product_id}: {sds_label_score}",
      "confidence": 1.0
    }
  }
}
```

---

## 4. Trace Analyzer (`src/proceda/cache/analyzer.py`)

The analyzer reads N completed traces for the same skill and produces a `SkillCache`.

```python
"""ABOUTME: Extracts execution patterns from historical traces to build step-level cache.
ABOUTME: Compares N traces to identify stable tool call patterns and argument mappings."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from proceda.cache.models import (
    ArgumentMapping,
    ArgumentSourceType,
    SkillCache,
    StepRecipe,
    ResultCapture,
    ToolCallRecipe,
)
from proceda.events import EventType, RunEvent
from proceda.store.event_log import EventLogReader


class TraceAnalyzer:
    """Analyzes multiple run traces to extract cacheable step patterns."""

    def __init__(self, min_traces: int = 3) -> None:
        self._min_traces = min_traces

    def analyze(
        self,
        run_dirs: list[Path],
        skill_raw_content: str,
    ) -> SkillCache | None:
        """Analyze traces from multiple runs and build a SkillCache.

        Args:
            run_dirs: Paths to run directories containing events.jsonl and metadata.json.
                Must all be for the same skill. Must have at least min_traces entries.
            skill_raw_content: The current SKILL.md content, used to compute the hash.

        Returns:
            A SkillCache if enough consistent traces were found, or None if
            fewer than min_traces runs are provided.

        Algorithm:
            1. Load metadata and events from each run directory.
            2. Group events by step for each run.
            3. For each step, extract the tool call pattern (tool names, argument values).
            4. Compare patterns across runs to identify stable mappings.
            5. Build StepRecipe for each step with confidence score.
        """
        ...

    def _load_run(self, run_dir: Path) -> _RunTrace | None:
        """Load a single run trace. Returns None if run was not successful."""
        ...

    def _group_events_by_step(self, events: list[RunEvent]) -> dict[int, list[RunEvent]]:
        """Group events by step_index.

        How: Walk events in order. When a STEP_STARTED event is seen,
        assign all subsequent events to that step_index until the next
        STEP_STARTED or RUN_COMPLETED.
        """
        ...

    def _extract_step_pattern(
        self, step_events: list[RunEvent]
    ) -> _StepPattern:
        """Extract the tool call pattern from one step's events.

        Returns:
            _StepPattern containing:
            - tool_calls: list of (tool_name, arguments_dict) tuples
            - has_clarification: bool (did request_clarification occur?)
            - summary: str (from summary.generated event)

        How: Walk events, collecting TOOL_CALLED payloads (tool_name + arguments)
        and checking for CLARIFICATION_REQUESTED events.
        """
        ...

    def _compare_patterns(
        self,
        patterns: list[_StepPattern],
        variables_per_run: list[dict[str, str]],
        prior_step_results_per_run: list[dict[int, dict[str, str]]],
    ) -> StepRecipe:
        """Compare patterns across N runs to build a StepRecipe.

        This is the core algorithm. For each step:

        1. CHECK CONSISTENCY: Do all runs have the same number of tool calls
           with the same tool names in the same order?
           - If yes: confidence starts at 1.0
           - If no: confidence = (count of matching runs) / (total runs)
             Only proceed if confidence >= some threshold.

        2. DERIVE ARGUMENT MAPPINGS: For each tool call, for each argument:
           a. Check if arg_value == variables[key] for some key, across ALL runs.
              → If yes for the same key in all runs: source = VARIABLE, key = that key.
           b. Check if arg_value matches a prior step's captured result field.
              → If yes in all runs: source = STEP_RESULT, key = "step[N].field_name".
           c. Check if arg_value is identical across all runs.
              → If yes: source = LITERAL, literal_value = that value.
           d. If none of the above match consistently:
              → This argument cannot be mapped. Drop the recipe for this step
                (confidence = 0.0).

        3. DERIVE RESULT CAPTURES: For each tool result, check which fields
           from the result JSON are used as arguments by later steps.
           This requires a forward pass — analyze all steps, then go back
           and mark which fields are referenced.

        4. BUILD SUMMARY TEMPLATE: Take the summary from the first run,
           replace any literal variable values with {variable_name} placeholders,
           and replace any step result values with {step[N].field_name} placeholders.
        """
        ...

    def _infer_argument_source(
        self,
        arg_name: str,
        values_across_runs: list[Any],
        variables_per_run: list[dict[str, str]],
        prior_results_per_run: list[dict[int, dict[str, str]]],
    ) -> ArgumentMapping | None:
        """Infer the source of a single argument by comparing across runs.

        Args:
            arg_name: The argument name (e.g., "product_id").
            values_across_runs: The actual value used in each run
                (e.g., ["P_13057", "P_13174", "P_13401"]).
            variables_per_run: The session.variables dict for each run.
            prior_results_per_run: For each run, a mapping of
                step_index → {field_name: value} for captured results
                from earlier steps.

        Returns:
            An ArgumentMapping if a consistent source was found, or None
            if the argument source is ambiguous or inconsistent.

        Algorithm:
            # Try VARIABLE source
            for var_key in variables_per_run[0]:
                if all(
                    values_across_runs[i] == variables_per_run[i].get(var_key)
                    for i in range(len(values_across_runs))
                ):
                    return ArgumentMapping(
                        source=ArgumentSourceType.VARIABLE,
                        key=var_key,
                    )

            # Try STEP_RESULT source
            for step_idx, fields in prior_results_per_run[0].items():
                for field_name, _ in fields.items():
                    if all(
                        values_across_runs[i]
                        == prior_results_per_run[i].get(step_idx, {}).get(field_name)
                        for i in range(len(values_across_runs))
                    ):
                        return ArgumentMapping(
                            source=ArgumentSourceType.STEP_RESULT,
                            key=f"step[{step_idx}].{field_name}",
                        )

            # Try LITERAL source
            if len(set(str(v) for v in values_across_runs)) == 1:
                return ArgumentMapping(
                    source=ArgumentSourceType.LITERAL,
                    literal_value=values_across_runs[0],
                )

            # Ambiguous — cannot cache this argument
            return None
        """
        ...
```

### Internal Helper Types

These are private to the analyzer module (prefixed with `_`):

```python
@dataclass
class _RunTrace:
    """Parsed data from a single run's trace."""
    run_id: str
    variables: dict[str, str]
    events: list[RunEvent]

@dataclass
class _StepPattern:
    """Extracted pattern from a single step in a single run."""
    tool_calls: list[tuple[str, dict[str, Any]]]  # (tool_name, arguments)
    has_clarification: bool
    summary: str
```

### Concrete Example: Analyzing `dangerous_goods`

Given 3 runs with these tool calls for Step 2:

| Run | product_id variable | sds_label_text variable | Tool args |
|-----|--------------------|-----------------------|-----------|
| Run 1 | P_13057 | Skin sensitizer class 2 | `{product_id: "P_13057", sds_label_text: "Skin sensitizer class 2"}` |
| Run 2 | P_13174 | Acute toxicity | `{product_id: "P_13174", sds_label_text: "Acute toxicity"}` |
| Run 3 | P_13401 | (some value) | `{product_id: "P_13401", sds_label_text: (some value)}` |

For `product_id` argument:
- Run 1: arg value "P_13057" == variables["product_id"] "P_13057" ✓
- Run 2: arg value "P_13174" == variables["product_id"] "P_13174" ✓
- Run 3: same pattern ✓
- **Result**: `ArgumentMapping(VARIABLE, key="product_id")`

For `sds_label_text` argument:
- Run 1: arg value == variables["sds_label_text"] ✓
- (same for all runs)
- **Result**: `ArgumentMapping(VARIABLE, key="sds_label_text")`

**Confidence**: All 3 runs had exactly 1 tool call to `calculate_sds_label_score` with 2 arguments, both consistently mapped → confidence = 1.0

---

## 5. Cache Store (`src/proceda/cache/store.py`)

```python
"""ABOUTME: Persists and loads step-level execution caches to disk.
ABOUTME: Handles cache invalidation based on SKILL.md content hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from proceda.cache.models import SkillCache


# Default directory for cache files, relative to the project root.
CACHE_DIR = ".proceda/cache"


class CacheStore:
    """Reads and writes SkillCache objects to the filesystem.

    Cache files are stored at: {base_dir}/{skill_id}.json

    Invalidation: On load, the stored skill_content_hash is compared
    against the hash of the current SKILL.md content. If they differ,
    the cache is stale and None is returned.
    """

    def __init__(self, base_dir: str = CACHE_DIR) -> None:
        self._base_dir = Path(base_dir)

    def save(self, cache: SkillCache) -> Path:
        """Write a SkillCache to disk.

        Args:
            cache: The SkillCache to persist.

        Returns:
            The Path where the cache was written.

        Steps:
            1. Create the base directory if it doesn't exist.
            2. Serialize cache via cache.to_dict().
            3. Write JSON to {base_dir}/{skill_id}.json with indent=2.
        """
        ...

    def load(self, skill_id: str, current_skill_content: str) -> SkillCache | None:
        """Load a SkillCache from disk, with invalidation check.

        Args:
            skill_id: The skill ID to look up.
            current_skill_content: The current raw content of the SKILL.md file.
                Used to compute a hash and compare against the stored hash.

        Returns:
            The SkillCache if found and valid, or None if:
            - No cache file exists for this skill_id.
            - The stored skill_content_hash doesn't match the current content hash.

        Steps:
            1. Compute SHA256 of current_skill_content.
            2. Check if {base_dir}/{skill_id}.json exists. If not, return None.
            3. Read and parse the JSON file.
            4. Deserialize via SkillCache.from_dict().
            5. Compare cache.skill_content_hash against computed hash.
            6. If mismatch, log a warning and return None (stale cache).
            7. If match, return the cache.
        """
        ...

    def delete(self, skill_id: str) -> bool:
        """Delete the cache file for a skill.

        Returns True if a file was deleted, False if no cache existed.
        """
        ...

    def list_cached_skills(self) -> list[str]:
        """Return skill IDs that have cache files on disk.

        How: Glob for *.json in base_dir, strip the extension.
        """
        ...

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute SHA256 hash of SKILL.md content.

        Returns the first 16 hex characters of the hash.
        """
        return hashlib.sha256(content.encode()).hexdigest()[:16]
```

---

## 6. Recipe Executor (`src/proceda/cache/executor.py`)

This module handles Level 2 execution — running tool calls directly from a recipe without the LLM loop.

```python
"""ABOUTME: Executes cached step recipes by resolving arguments and calling tools directly.
ABOUTME: Used for Level 2 optimization — bypasses the LLM loop for high-confidence steps."""

from __future__ import annotations

import json
import logging
from typing import Any

from proceda.cache.models import ArgumentMapping, ArgumentSourceType, StepRecipe
from proceda.events import EventType, RunEvent
from proceda.internal.executor import EmitFn
from proceda.internal.tool_executor import ToolExecutor
from proceda.session import RunMessage, RunSession, ToolCall

logger = logging.getLogger(__name__)


class RecipeExecutionError(Exception):
    """Raised when a recipe cannot be executed and fallback is needed."""
    pass


class RecipeExecutor:
    """Executes a StepRecipe by resolving arguments and calling tools directly.

    This replaces the LLM loop for Level 2 cached steps. After executing
    all tool calls in the recipe, it does NOT call complete_step — the caller
    (Executor) handles step completion.
    """

    def __init__(
        self,
        session: RunSession,
        tool_executor: ToolExecutor,
        emit: EmitFn,
        step_results: dict[int, dict[str, Any]],
    ) -> None:
        """
        Args:
            session: The current RunSession (for reading variables).
            tool_executor: The ToolExecutor for making MCP tool calls.
            emit: Event emission function.
            step_results: Accumulated results from prior steps. Maps
                step_index → {field_name: value}. This is built up as
                the run progresses by the caller.
        """
        self._session = session
        self._tool_executor = tool_executor
        self._emit = emit
        self._step_results = step_results

    async def execute(self, recipe: StepRecipe) -> dict[str, Any]:
        """Execute all tool calls in a recipe and return captured results.

        Args:
            recipe: The StepRecipe to execute.

        Returns:
            A dict mapping field_name → captured value from tool results.
            Example: {"sds_label_score": 3}

        Raises:
            RecipeExecutionError: If any argument cannot be resolved,
                a tool call fails, or a result field is missing.

        Steps:
            1. For each ToolCallRecipe in recipe.tool_call_recipes:
               a. Resolve each argument via _resolve_argument().
               b. Create a ToolCall with the resolved arguments.
               c. Add the ToolCall as an assistant message to the session
                  (so the conversation history is consistent).
               d. Execute via self._tool_executor.execute().
               e. Add the tool result message to the session.
               f. Parse the result content as JSON.
               g. If the tool call returned an error, raise RecipeExecutionError.
            2. For each ResultCapture in recipe.result_captures:
               a. Get the result JSON from the corresponding tool call.
               b. Extract the field_name from the JSON.
               c. If missing, raise RecipeExecutionError.
            3. Return the captured values dict.
        """
        ...

    def _resolve_argument(self, mapping: ArgumentMapping) -> Any:
        """Resolve an argument mapping to a concrete value.

        Args:
            mapping: The ArgumentMapping to resolve.

        Returns:
            The resolved value.

        Raises:
            RecipeExecutionError: If the variable or step result is not found.

        Logic:
            if mapping.source == VARIABLE:
                value = self._session.variables.get(mapping.key)
                if value is None:
                    raise RecipeExecutionError(
                        f"Variable '{mapping.key}' not found in session variables. "
                        f"Available: {list(self._session.variables.keys())}"
                    )
                return value

            elif mapping.source == STEP_RESULT:
                # Parse "step[N].field_name" format
                # e.g., "step[2].sds_label_score" → step_index=2, field="sds_label_score"
                step_idx, field = _parse_step_result_key(mapping.key)
                step_data = self._step_results.get(step_idx)
                if step_data is None:
                    raise RecipeExecutionError(
                        f"No results captured for step {step_idx}. "
                        f"Available steps: {list(self._step_results.keys())}"
                    )
                value = step_data.get(field)
                if value is None:
                    raise RecipeExecutionError(
                        f"Field '{field}' not found in step {step_idx} results. "
                        f"Available: {list(step_data.keys())}"
                    )
                return value

            elif mapping.source == LITERAL:
                return mapping.literal_value

            else:
                raise RecipeExecutionError(f"Unknown source type: {mapping.source}")
        """
        ...


def _parse_step_result_key(key: str) -> tuple[int, str]:
    """Parse a step result key like 'step[2].sds_label_score' into (2, 'sds_label_score').

    Raises ValueError if the format is invalid.
    """
    # Expected format: "step[N].field_name"
    # Use a regex: r"step\[(\d+)\]\.(.+)"
    ...
```

---

## 7. Changes to Existing Files

### 7.1. `src/proceda/config.py` — Add CacheConfig

Add after `LoggingConfig`:

```python
@dataclass
class CacheConfig:
    """Step-level execution cache settings."""

    enabled: bool = False                # Must be explicitly opted in
    min_traces: int = 3                  # Minimum successful runs before building cache
    min_confidence: float = 0.8          # Minimum recipe confidence to use
    optimization_level: int = 1          # 1 = hint injection, 2 = direct execution
```

Add to `ProcedaConfig`:

```python
@dataclass
class ProcedaConfig:
    # ... existing fields ...
    cache: CacheConfig = field(default_factory=CacheConfig)
```

Add parsing in `ProcedaConfig.from_dict()`:

```python
if "cache" in data:
    c = data["cache"]
    config.cache = CacheConfig(
        enabled=c.get("enabled", False),
        min_traces=c.get("min_traces", 3),
        min_confidence=c.get("min_confidence", 0.8),
        optimization_level=c.get("optimization_level", 1),
    )
```

### 7.2. `src/proceda/events.py` — Add Cache Event Types

Add three new values to `EventType`:

```python
class EventType(enum.Enum):
    # ... existing values ...

    # Cache
    CACHE_HIT = "cache.hit"
    CACHE_MISS = "cache.miss"
    CACHE_FALLBACK = "cache.fallback"
```

Event payloads:

```python
# CACHE_HIT: emitted when a step uses a cached recipe
{"step_index": 2, "optimization_level": 1, "confidence": 1.0}

# CACHE_MISS: emitted when a step has no cache or confidence too low
{"step_index": 1, "reason": "no_recipe"}  # or "low_confidence"

# CACHE_FALLBACK: emitted when Level 2 execution fails and falls back to LLM
{"step_index": 3, "error": "Tool returned unexpected error: ..."}
```

### 7.3. `src/proceda/llm/prompts.py` — Add Hint Parameter

Change `build_step_prompt()` signature and body:

**Before:**

```python
def build_step_prompt(
    step: SkillStep,
    is_last_step: bool = False,
    output_fields: list[str] | None = None,
) -> str:
```

**After:**

```python
def build_step_prompt(
    step: SkillStep,
    is_last_step: bool = False,
    output_fields: list[str] | None = None,
    hint: str | None = None,
) -> str:
    """Build a user-facing prompt for starting a specific step."""
    markers_text = ""
    if step.markers:
        markers_text = " (" + ", ".join(m.value for m in step.markers) + ")"

    prompt = (
        f"Now execute Step {step.index}: {step.title}{markers_text}\n\n"
        f"{step.content}\n\n"
        f"When complete, call `complete_step` with a summary of what you did."
    )

    if is_last_step and output_fields:
        tags = "\n".join(f"  <{f}>YOUR_VALUE</{f}>" for f in output_fields)
        prompt += (
            "\n\nIMPORTANT: This is the final step. Your complete_step summary "
            "MUST include these output fields as XML tags:\n" + tags
        )

    if hint:
        prompt += f"\n\n{hint}"

    return prompt
```

### 7.4. `src/proceda/internal/executor.py` — Consult Cache

**Changes to `__init__`:**

Add parameter and field:

```python
def __init__(
    self,
    skill: Skill,
    session: RunSession,
    llm: LLMRuntime,
    tool_executor: ToolExecutor | None,
    human: HumanInterface,
    emit: EmitFn,
    context_manager: ContextManager | None = None,
    tool_schemas: list[dict[str, Any]] | None = None,
    max_text_responses_before_prompt: int = 3,
    max_tool_calls_per_step: int = 20,
    skill_cache: SkillCache | None = None,            # NEW
    cache_config: CacheConfig | None = None,           # NEW
) -> None:
    # ... existing assignments ...
    self._skill_cache = skill_cache                     # NEW
    self._cache_config = cache_config                   # NEW
    self._step_results: dict[int, dict[str, Any]] = {}  # NEW: accumulated per-step results
```

**Changes to `_execute_step()`:**

Insert cache consultation at the top of the method, before the existing LLM loop. The changes are:

```python
async def _execute_step(self, step_index: int) -> None:
    """Execute a single step via the LLM loop."""
    step = self._skill.get_step(step_index)
    session = self._session

    # ── NEW: Cache consultation ──────────────────────────────────
    recipe = None
    hint = None
    cache_cfg = self._cache_config

    if self._skill_cache and cache_cfg and cache_cfg.enabled:
        recipe = self._skill_cache.get_recipe(step_index)

        if recipe and recipe.confidence >= cache_cfg.min_confidence:
            # Level 2: Direct execution with fallback
            if cache_cfg.optimization_level >= 2 and recipe.tool_call_recipes:
                await self._emit(RunEvent.create(
                    session.id, EventType.CACHE_HIT,
                    {"step_index": step_index, "optimization_level": 2,
                     "confidence": recipe.confidence},
                ))
                try:
                    from proceda.cache.executor import RecipeExecutor, RecipeExecutionError
                    recipe_exec = RecipeExecutor(
                        session, self._tool_executor, self._emit, self._step_results
                    )
                    captured = await recipe_exec.execute(recipe)
                    self._step_results[step_index] = captured
                    # Synthesize complete_step summary from template
                    summary = recipe.summary_template.format(
                        **session.variables, **{
                            f"step_{k}_{f}": v
                            for k, fields in self._step_results.items()
                            for f, v in fields.items()
                        }
                    )
                    session.add_message(
                        RunMessage.create("tool", summary, tool_call_id="cache_complete")
                    )
                    await self._emit(RunEvent.create(
                        session.id, EventType.SUMMARY_GENERATED,
                        {"step_index": step_index, "summary": summary},
                    ))
                    return
                except RecipeExecutionError as e:
                    logger.warning("Recipe failed for step %d: %s. Falling back to LLM.", step_index, e)
                    await self._emit(RunEvent.create(
                        session.id, EventType.CACHE_FALLBACK,
                        {"step_index": step_index, "error": str(e)},
                    ))
                    # Fall through to normal LLM execution below

            # Level 1: Hint injection (also used as fallback from Level 2)
            elif cache_cfg.optimization_level >= 1:
                hint = _build_hint_from_recipe(recipe)
                await self._emit(RunEvent.create(
                    session.id, EventType.CACHE_HIT,
                    {"step_index": step_index, "optimization_level": 1,
                     "confidence": recipe.confidence},
                ))
        else:
            reason = "no_recipe" if not recipe else "low_confidence"
            await self._emit(RunEvent.create(
                session.id, EventType.CACHE_MISS,
                {"step_index": step_index, "reason": reason},
            ))
    # ── END NEW ──────────────────────────────────────────────────

    is_last_step = step_index == self._skill.step_count
    step_prompt = build_step_prompt(
        step,
        is_last_step=is_last_step,
        output_fields=self._skill.output_fields,
        hint=hint,                                      # CHANGED: pass hint
    )
    session.add_message(RunMessage.create("user", step_prompt, is_critical=True))

    # ... rest of the existing LLM loop is UNCHANGED ...
```

**New helper function** (at module level in executor.py):

```python
def _build_hint_from_recipe(recipe: StepRecipe) -> str:
    """Build a natural language hint from a StepRecipe for Level 1 injection.

    Example output:
        "In previous successful executions, this step followed this pattern:
        1. Called `calculate_sds_label_score` with arguments: product_id (from variables),
           sds_label_text (from variables)
        Follow this pattern if applicable to the current inputs."
    """
    if not recipe.tool_call_recipes:
        return ""

    lines = ["In previous successful executions, this step followed this pattern:"]
    for i, tc in enumerate(recipe.tool_call_recipes, 1):
        arg_descriptions = []
        for arg_name, mapping in tc.argument_mappings.items():
            if mapping.source == ArgumentSourceType.VARIABLE:
                arg_descriptions.append(f"{arg_name} (from variable '{mapping.key}')")
            elif mapping.source == ArgumentSourceType.STEP_RESULT:
                arg_descriptions.append(f"{arg_name} (from {mapping.key})")
            elif mapping.source == ArgumentSourceType.LITERAL:
                arg_descriptions.append(f"{arg_name} = {mapping.literal_value!r}")
        args_text = ", ".join(arg_descriptions)
        lines.append(f"{i}. Called `{tc.tool_name}` with: {args_text}")

    lines.append("Follow this pattern if applicable to the current inputs.")
    return "\n".join(lines)
```

**Changes to `execute()` — capture step results for Level 2:**

After `session.complete_current_step()` in the main loop, add:

```python
# Capture tool results for use by later cached steps
if step_index not in self._step_results and session.step_tool_results:
    # Extract field-value pairs from the last tool result
    last_result = session.step_tool_results[-1]
    content = last_result.get("content", "")
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            self._step_results[step_index] = parsed
    except (json.JSONDecodeError, TypeError):
        pass
```

### 7.5. `src/proceda/runtime.py` — Wire Cache Loading and Auto-Build

**Changes to `start()`:**

After creating the `Executor`, load the cache:

```python
# Create executor
skill_cache = None
if self._config.cache.enabled:
    from proceda.cache.store import CacheStore
    cache_store = CacheStore(str(Path(self._config.logging.run_dir).parent / "cache"))
    skill_cache = cache_store.load(skill.id, skill.raw_content)
    if skill_cache:
        logger.info("Loaded execution cache for skill %s (%d step recipes)",
                     skill.name, len(skill_cache.step_recipes))

executor = Executor(
    skill=skill,
    session=session,
    llm=llm,
    tool_executor=tool_executor,
    human=self._human,
    emit=composite.handle,
    tool_schemas=tool_schemas,
    skill_cache=skill_cache,                # NEW
    cache_config=self._config.cache,        # NEW
)
```

**Changes to `_run()` closure — auto-build after successful run:**

```python
async def _run() -> None:
    try:
        await executor.execute()
    finally:
        if self._orchestrator:
            await self._orchestrator.disconnect_all()

        summary = generate_run_summary(session, skill)
        log_writer.write_summary(summary)
        await log_writer.close()

        # NEW: Auto-build cache after successful run if enabled
        if (
            self._config.cache.enabled
            and session.status == RunStatus.COMPLETED
            and skill_cache is None  # Only build if no cache exists yet
        ):
            await self._try_auto_build_cache(skill, session)

        await handle._event_queue.put(None)
```

**New method on Runtime:**

```python
async def _try_auto_build_cache(self, skill: Skill, session: RunSession) -> None:
    """Attempt to build a cache if enough successful traces exist."""
    from proceda.cache.analyzer import TraceAnalyzer
    from proceda.cache.store import CacheStore

    try:
        dir_manager = RunDirectoryManager(self._config.logging.run_dir)
        run_dirs = dir_manager.list_runs()

        # Filter to completed runs of this skill
        matching_dirs = []
        for run_dir in run_dirs:
            reader = EventLogReader(run_dir)
            metadata = reader.read_metadata()
            if (
                metadata.get("skill_id") == skill.id
                and reader.exists
            ):
                # Check if run completed successfully by looking for run.completed event
                events = reader.read_events()
                if any(e.type == EventType.RUN_COMPLETED for e in events):
                    matching_dirs.append(run_dir)

        if len(matching_dirs) >= self._config.cache.min_traces:
            analyzer = TraceAnalyzer(min_traces=self._config.cache.min_traces)
            cache = analyzer.analyze(
                matching_dirs[:self._config.cache.min_traces],
                skill.raw_content,
            )
            if cache:
                store = CacheStore(
                    str(Path(self._config.logging.run_dir).parent / "cache")
                )
                path = store.save(cache)
                logger.info("Auto-built execution cache for skill %s at %s",
                           skill.name, path)
    except Exception:
        logger.debug("Failed to auto-build cache", exc_info=True)
```

### 7.6. `src/proceda/cli/main.py` — Add Cache CLI Commands

Add a new command group:

```python
cache_app = typer.Typer(help="Manage step-level execution cache.")
app.add_typer(cache_app, name="cache")


@cache_app.command("build")
def cache_build(
    skill_path: str = typer.Argument(..., help="Path to SKILL.md or directory containing it"),
    min_traces: int = typer.Option(3, help="Minimum successful traces required"),
) -> None:
    """Analyze traces and build an execution cache for a skill."""
    from proceda.cache.analyzer import TraceAnalyzer
    from proceda.cache.store import CacheStore
    from proceda.skills.loader import load_skill
    from proceda.store.event_log import EventLogReader, RunDirectoryManager

    skill = load_skill(skill_path)
    config = ProcedaConfig.load()

    dir_manager = RunDirectoryManager(config.logging.run_dir)
    run_dirs = dir_manager.list_runs()

    # Filter to completed runs of this skill
    matching = []
    for d in run_dirs:
        reader = EventLogReader(d)
        meta = reader.read_metadata()
        if meta.get("skill_id") == skill.id and reader.exists:
            events = reader.read_events()
            if any(e.type.value == "run.completed" for e in events):
                matching.append(d)

    if len(matching) < min_traces:
        typer.echo(f"Only {len(matching)} completed runs found (need {min_traces}). Run the skill more times first.")
        raise typer.Exit(1)

    analyzer = TraceAnalyzer(min_traces=min_traces)
    cache = analyzer.analyze(matching[:min_traces], skill.raw_content)

    if cache is None:
        typer.echo("Could not build cache — patterns were not consistent enough.")
        raise typer.Exit(1)

    store = CacheStore()
    path = store.save(cache)
    typer.echo(f"Cache built: {path}")
    typer.echo(f"  Steps cached: {list(cache.step_recipes.keys())}")
    for idx, recipe in sorted(cache.step_recipes.items()):
        tools = [tc.tool_name for tc in recipe.tool_call_recipes]
        typer.echo(f"  Step {idx}: {tools or '(no tools)'} confidence={recipe.confidence:.1%}")


@cache_app.command("clear")
def cache_clear(
    skill_path: str = typer.Argument(..., help="Path to SKILL.md or directory containing it"),
) -> None:
    """Delete the execution cache for a skill."""
    from proceda.cache.store import CacheStore
    from proceda.skills.loader import load_skill

    skill = load_skill(skill_path)
    store = CacheStore()
    if store.delete(skill.id):
        typer.echo(f"Cache cleared for skill '{skill.name}'")
    else:
        typer.echo(f"No cache found for skill '{skill.name}'")


@cache_app.command("show")
def cache_show(
    skill_path: str = typer.Argument(..., help="Path to SKILL.md or directory containing it"),
) -> None:
    """Show the current cache for a skill."""
    import json as json_mod
    from proceda.cache.store import CacheStore
    from proceda.skills.loader import load_skill

    skill = load_skill(skill_path)
    store = CacheStore()
    cache = store.load(skill.id, skill.raw_content)

    if cache is None:
        typer.echo(f"No valid cache for skill '{skill.name}'")
        raise typer.Exit(1)

    typer.echo(f"Cache for: {skill.name} (id: {cache.skill_id})")
    typer.echo(f"Built from: {len(cache.source_run_ids)} runs")
    typer.echo(f"Created: {cache.created_at}")
    typer.echo(f"Steps cached: {len(cache.step_recipes)}")
    typer.echo()

    for idx, recipe in sorted(cache.step_recipes.items()):
        typer.echo(f"Step {idx} (confidence: {recipe.confidence:.1%}):")
        for tc in recipe.tool_call_recipes:
            args_desc = {}
            for name, mapping in tc.argument_mappings.items():
                args_desc[name] = f"{mapping.source.value}:{mapping.key or mapping.literal_value}"
            typer.echo(f"  → {tc.tool_name}({args_desc})")
        if recipe.result_captures:
            fields = [rc.field_name for rc in recipe.result_captures]
            typer.echo(f"  captures: {fields}")
```

---

## 8. `proceda.yaml` Configuration

```yaml
llm:
  model: openrouter/google/gemini-3-flash
  api_key_env: OPENROUTER_API_KEY    # Retrieved via: pass soprun/OPENROUTER_API_KEY
  temperature: 0.0
  max_tokens: 4096

cache:
  enabled: true              # Default: false
  min_traces: 3              # Build cache after this many successful runs
  min_confidence: 0.8        # Only use recipes with >= 80% confidence
  optimization_level: 1      # 1 = hints only, 2 = direct execution
```

**Model choice**: Use Gemini 3 Flash via OpenRouter (`openrouter/google/gemini-3-flash`).
The API key is stored in `pass` and should be exported before running:

```bash
export OPENROUTER_API_KEY=$(pass soprun/OPENROUTER_API_KEY)
```

---

## 9. Task Breakdown

### Phase 1: Foundation (data models + storage)

**Task 1.1: Create `src/proceda/cache/__init__.py`**
- File: `src/proceda/cache/__init__.py`
- Content: ABOUTME comment + exports of `SkillCache`, `CacheStore`, `TraceAnalyzer`
- Tests: None (just an init file)
- Dependencies: None

**Task 1.2: Create `src/proceda/cache/models.py`**
- File: `src/proceda/cache/models.py`
- Implement all dataclasses: `ArgumentSourceType`, `ArgumentMapping`, `ToolCallRecipe`, `ResultCapture`, `StepRecipe`, `SkillCache`
- Implement `SkillCache.to_dict()` and `SkillCache.from_dict()`
- Tests: `tests/test_cache/test_models.py`
  - Test round-trip serialization: create a SkillCache, call to_dict(), call from_dict(), assert equal
  - Test with all ArgumentSourceType variants
  - Test `get_recipe()` returns None for missing steps
- Dependencies: None

**Task 1.3: Create `src/proceda/cache/store.py`**
- File: `src/proceda/cache/store.py`
- Implement `CacheStore` with `save()`, `load()`, `delete()`, `list_cached_skills()`, `compute_content_hash()`
- Tests: `tests/test_cache/test_store.py`
  - Test save then load returns the same cache
  - Test load with stale hash returns None
  - Test load with no file returns None
  - Test delete removes the file
  - Test list_cached_skills returns correct IDs
  - Use `tmp_path` fixture for isolation
- Dependencies: Task 1.2

**Task 1.4: Add `CacheConfig` to `src/proceda/config.py`**
- Add `CacheConfig` dataclass (4 fields: `enabled`, `min_traces`, `min_confidence`, `optimization_level`)
- Add `cache: CacheConfig` field to `ProcedaConfig`
- Add parsing in `from_dict()`
- Tests: `tests/test_config.py` (add to existing file)
  - Test default values
  - Test parsing from dict with cache section
  - Test parsing from dict without cache section (defaults)
- Dependencies: None

**Task 1.5: Add cache event types to `src/proceda/events.py`**
- Add `CACHE_HIT`, `CACHE_MISS`, `CACHE_FALLBACK` to `EventType`
- Tests: No separate test needed — existing event tests + usage in later tasks
- Dependencies: None

### Phase 2: Trace Analysis

**Task 2.1: Create `src/proceda/cache/analyzer.py`**
- File: `src/proceda/cache/analyzer.py`
- Implement `TraceAnalyzer` with all methods
- This is the most complex task — implement in this order:
  1. `_load_run()` — read events and metadata from a run directory
  2. `_group_events_by_step()` — split event list by step
  3. `_extract_step_pattern()` — get tool calls from a step's events
  4. `_infer_argument_source()` — the core algorithm (compare values across runs)
  5. `_compare_patterns()` — combine per-run patterns into a StepRecipe
  6. `analyze()` — orchestrate the full pipeline
- Tests: `tests/test_cache/test_analyzer.py`
  - **Create synthetic test traces** (don't depend on real benchmark data):
    - Write helper `_create_trace(run_id, variables, tool_calls_per_step)` that generates a list of `RunEvent` objects mimicking a real run
    - Write these to temporary directories using `EventLogWriter`
  - Test 1: Three identical traces with 1 tool call per step, all args from variables → expect confidence 1.0, all VARIABLE mappings
  - Test 2: Three traces where step 3 uses a result from step 2 → expect STEP_RESULT mapping
  - Test 3: Two traces with different tool call sequences for a step → expect low confidence
  - Test 4: Trace with a clarification step → expect that step is not cached
  - Test 5: Fewer than min_traces provided → returns None
- Dependencies: Tasks 1.2, 1.3, 1.5

### Phase 3: Level 1 — Hint Injection

**Task 3.1: Modify `build_step_prompt()` in `src/proceda/llm/prompts.py`**
- Add `hint: str | None = None` parameter
- Append hint to the end of the prompt if provided
- Tests: `tests/test_llm/test_prompts.py` (add to existing file)
  - Test that hint=None produces unchanged output
  - Test that hint="some text" is appended to the prompt
- Dependencies: None

**Task 3.2: Add `_build_hint_from_recipe()` to `src/proceda/internal/executor.py`**
- Module-level function that converts a StepRecipe to a human-readable hint string
- Tests: `tests/test_internal/test_executor.py` or `tests/test_cache/test_hints.py`
  - Test with a recipe that has VARIABLE mappings
  - Test with a recipe that has STEP_RESULT mappings
  - Test with a recipe that has no tool calls → returns empty string
- Dependencies: Task 1.2

**Task 3.3: Wire cache into `Executor.__init__()` and `_execute_step()`**
- Add `skill_cache` and `cache_config` parameters to `__init__()`
- Add cache consultation block at top of `_execute_step()` (Level 1 only for now)
- Emit CACHE_HIT and CACHE_MISS events
- Tests: `tests/test_internal/test_executor.py` (add to existing file)
  - Test: executor with no cache behaves identically to before (regression test)
  - Test: executor with Level 1 cache and a matching recipe emits CACHE_HIT and passes hint to build_step_prompt
  - Test: executor with cache but low confidence emits CACHE_MISS
  - Use `CollectorEventSink` to assert on events
- Dependencies: Tasks 1.2, 1.5, 3.1, 3.2

**Task 3.4: Wire cache loading into `Runtime.start()`**
- Load `SkillCache` via `CacheStore` when `cache.enabled` is True
- Pass to `Executor` constructor
- Tests: `tests/test_runtime.py` (add to existing file)
  - Test: runtime with cache disabled does not attempt to load
  - Test: runtime with cache enabled loads from store (mock or use tmp_path)
- Dependencies: Tasks 1.3, 1.4, 3.3

### Phase 4: Level 2 — Direct Execution

**Task 4.1: Create `src/proceda/cache/executor.py`**
- Implement `RecipeExecutor` and `_parse_step_result_key()`
- Tests: `tests/test_cache/test_executor.py`
  - Test `_parse_step_result_key()` with valid and invalid inputs
  - Test `_resolve_argument()` for each source type
  - Test `_resolve_argument()` raises RecipeExecutionError for missing variable
  - Test `execute()` with a mock ToolExecutor (this is the one place mocks are appropriate — we're testing the recipe resolution logic, not the tool calls themselves)
- Dependencies: Tasks 1.2

**Task 4.2: Add Level 2 path to `_execute_step()` in executor.py**
- Add the Level 2 code block (direct execution with fallback) above the Level 1 block
- Add `_step_results` accumulation after each step completes
- Tests: `tests/test_internal/test_executor.py`
  - Test: Level 2 with valid recipe executes tools directly and returns without entering LLM loop
  - Test: Level 2 with recipe that fails (RecipeExecutionError) falls back to LLM and emits CACHE_FALLBACK
  - Test: step_results are accumulated across steps
- Dependencies: Tasks 3.3, 4.1

### Phase 5: Auto-Build and CLI

**Task 5.1: Add auto-build to `Runtime._run()`**
- Implement `_try_auto_build_cache()` method
- Call it after successful runs when no cache exists
- Tests: `tests/test_runtime.py`
  - Test: auto-build is triggered after a successful run when cache is enabled and no cache exists
  - Test: auto-build is NOT triggered when cache already exists
  - Test: auto-build is NOT triggered when run failed
  - Test: auto-build failure is caught and logged (doesn't crash the run)
- Dependencies: Tasks 2.1, 3.4

**Task 5.2: Add CLI commands**
- Add `cache build`, `cache clear`, `cache show` to `src/proceda/cli/main.py`
- Tests: `tests/test_cli/test_cache_cli.py`
  - Test each command's happy path (may need synthetic run data)
  - Test error cases (not enough traces, no cache exists)
- Dependencies: Tasks 1.3, 2.1

### Phase 6: Integration Testing

**Task 6.1: End-to-end test with real traces**
- `tests/test_cache/test_integration.py`
- Create a simple 3-step skill with a mock MCP tool server
- Run it 3 times to generate traces
- Build cache
- Run again with Level 1 → verify hint appears in step prompts
- Run again with Level 2 → verify tool calls are made directly from cache
- Compare token usage between cached and uncached runs
- Dependencies: All previous tasks

---

## 10. Verification Plan

### Automated Tests
Each task above specifies its tests. Run the full suite:
```bash
make test
```

### SOP-Bench Accuracy Regression Testing

**This is the critical verification**: caching must not degrade correctness.
Use the SOP-Bench ground truth labels to prove that Level 1 and Level 2
produce the same (or better) accuracy as the baseline uncached runs.

**Ground truth location**: `~/repos/3p/sop-bench/src/amazon_sop_bench/benchmarks/data/{domain}/test_set_with_outputs.csv`
Each CSV has input columns and output columns (the labeled expected answers).
The `metadata.json` in each domain directory lists which columns are inputs vs outputs.

**Proceda benchmark harness**: `benchmarks/sop_bench/harness.py`
- `run_evaluation(domain, data_dir, ...)` runs the agent on each task, extracts outputs, and compares against expected using `compare_decisions()` (case-insensitive fuzzy matching).
- Reports metrics: **TSR** (Task Success Rate), **ECR** (Execution Completion Rate), **C-TSR** (Conditional TSR).
- Results go to `benchmarks/sop_bench/results/{domain}_results.json` with per-task breakdown.

**Model**: Gemini 3 Flash via OpenRouter. Export the key before running:
```bash
export OPENROUTER_API_KEY=$(pass soprun/OPENROUTER_API_KEY)
```

#### Step-by-step verification procedure

**Step 1: Establish baseline (no cache)**

Run the full benchmark with caching disabled to get baseline metrics:

```bash
# Ensure cache is disabled in config
# Run against all domains (or a representative subset: dangerous_goods, content_flagging, order_fulfillment)
cd ~/repos/gh/proceda
python -m benchmarks.sop_bench.harness --domain dangerous_goods \
    --data-dir ~/repos/3p/sop-bench/src/amazon_sop_bench/benchmarks/data

# Save baseline results
cp benchmarks/sop_bench/results/dangerous_goods_results.json \
   benchmarks/sop_bench/results/dangerous_goods_results_baseline.json
```

Record baseline TSR, ECR, and C-TSR for each domain.

**Step 2: Build cache from baseline traces**

```bash
proceda cache build benchmarks/sop_bench/domains/dangerous_goods/
proceda cache show benchmarks/sop_bench/domains/dangerous_goods/
```

Verify:
- Steps 2-5 should have confidence 1.0 with VARIABLE mappings
- Steps 1, 6, 7 should have no recipes or confidence 0.0 (no tool calls)

**Step 3: Run with Level 1 (hint injection)**

```bash
# Enable cache with optimization_level: 1 in config
python -m benchmarks.sop_bench.harness --domain dangerous_goods \
    --data-dir ~/repos/3p/sop-bench/src/amazon_sop_bench/benchmarks/data

cp benchmarks/sop_bench/results/dangerous_goods_results.json \
   benchmarks/sop_bench/results/dangerous_goods_results_level1.json
```

**Acceptance criteria**:
- TSR must be >= baseline TSR (no accuracy regression)
- ECR must be >= baseline ECR
- Token usage per run should be lower (check event logs for cumulative token counts)
- Event logs should contain CACHE_HIT events on cached steps

**Step 4: Run with Level 2 (direct execution)**

```bash
# Enable cache with optimization_level: 2 in config
python -m benchmarks.sop_bench.harness --domain dangerous_goods \
    --data-dir ~/repos/3p/sop-bench/src/amazon_sop_bench/benchmarks/data

cp benchmarks/sop_bench/results/dangerous_goods_results.json \
   benchmarks/sop_bench/results/dangerous_goods_results_level2.json
```

**Acceptance criteria**:
- TSR must be >= baseline TSR (no accuracy regression)
- ECR must be >= baseline ECR
- Token usage should be significantly lower than Level 1
- CACHE_FALLBACK events should be rare (< 5% of cached steps)

**Step 5: Compare results across all three runs**

Write a comparison script or manually diff the per-task results:

```bash
python3 -c "
import json

for label, path in [
    ('Baseline', 'benchmarks/sop_bench/results/dangerous_goods_results_baseline.json'),
    ('Level 1',  'benchmarks/sop_bench/results/dangerous_goods_results_level1.json'),
    ('Level 2',  'benchmarks/sop_bench/results/dangerous_goods_results_level2.json'),
]:
    with open(path) as f:
        data = json.load(f)
    m = data['metrics']
    print(f'{label:10s}  TSR={m[\"tsr\"]:.3f}  ECR={m[\"ecr\"]:.3f}  C-TSR={m[\"c_tsr\"]:.3f}')
"
```

Also diff per-task correctness to find any regressions:
- For each task_id, compare `is_correct` across baseline/L1/L2
- Any task that was correct in baseline but wrong in L1/L2 is a regression
- Investigate regressions by comparing traces (the JSONL files in `results/traces/`)

**Step 6: Repeat for additional domains**

Run the same baseline → L1 → L2 comparison on at least 3 domains:
- `dangerous_goods` (simple, tool-heavy — best case for caching)
- `content_flagging` (more complex reasoning)
- `order_fulfillment` (multi-tool steps)

**Hard gate**: If ANY domain shows TSR regression > 2% (absolute), the caching
level that caused it must not ship until the root cause is identified and fixed.

### Manual Functional Tests

1. **Cache invalidation**:
   - Build cache, edit SKILL.md (change a step's text), run again
   - Verify CACHE_MISS events (stale hash detected)

2. **Fallback robustness**:
   - Build cache, modify MCP tool to return an error for one call
   - Run with Level 2
   - Verify CACHE_FALLBACK event and that the run still completes via LLM fallback

3. **Cache CLI**:
   - `proceda cache build` / `proceda cache show` / `proceda cache clear`
   - Verify each produces expected output
