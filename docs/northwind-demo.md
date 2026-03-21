# Northwind Ops Demo

A complete, self-contained demo of Proceda running five realistic business SOPs against a real company database. The Northwind database simulates a small food distribution company with customers, orders, products, employees, and suppliers — the kind of data a real ops team works with daily.

## What's in the demo

```
examples/northwind-ops/
├── seed_db.py                      # Downloads the Northwind SQLite database
├── northwind_server.py             # MCP server exposing 4 database tools
├── proceda.yaml                    # Proceda config for the demo
├── run.py                          # Interactive runner (single skill)
├── run_all.py                      # Batch runner with auto-approval and trace capture
├── order-fulfillment/SKILL.md      # Process a pending order
├── inventory-restock/SKILL.md      # Find and restock low-inventory products
├── customer-onboarding/SKILL.md    # Add a new customer with duplicate checks
├── monthly-sales-report/SKILL.md   # Read-only sales analytics
├── returns-processing/SKILL.md     # Handle a product return with refund
└── traces/                         # Execution traces from batch runs
```

## Quick start

### Prerequisites

- Python 3.11+ (via `uv`)
- An Anthropic API key in the `ANTHROPIC_API_KEY` environment variable
- Proceda installed (`uv sync` from the repo root)

### Run a single skill interactively

```bash
# From the repo root:
proceda run examples/northwind-ops/order-fulfillment/ \
  --config examples/northwind-ops/proceda.yaml
```

The database is downloaded automatically on first run (~1 MB from GitHub). After that, the cached `northwind.db` file is reused.

### Run a single skill via the SDK

```bash
uv run python examples/northwind-ops/run.py order-fulfillment
```

This uses `TerminalHumanInterface` so you'll be prompted at approval gates.

### Run all 5 skills in batch (auto-approved)

```bash
ANTHROPIC_API_KEY=your-key-here \
  uv run python examples/northwind-ops/run_all.py
```

This runs all five skills sequentially with `AutoApproveHumanInterface`, captures JSONL event traces and JSON summaries to `examples/northwind-ops/traces/`, and prints a final report.

You can also run specific skills:

```bash
uv run python examples/northwind-ops/run_all.py monthly-sales-report returns-processing
```

## The database

The demo uses the [Northwind SQLite database](https://github.com/jpwhite3/northwind-SQLite3), a classic sample dataset representing a food distribution company. Key tables:

| Table | Description | Row count |
|-------|-------------|-----------|
| Customers | Company profiles with contacts and addresses | 93 |
| Orders | Order headers with dates, shipping, freight | 16,282 |
| Order Details | Line items with product, quantity, price, discount | ~58,000 |
| Products | Product catalog with pricing and stock levels | 77 |
| Suppliers | Vendor companies | 29 |
| Employees | Staff with hire dates and territory assignments | 9 |
| Shippers | Shipping carriers | 3 |
| Categories | Product categories | 8 |
| Territories | Sales territories | 53 |

The `seed_db.py` script downloads the pre-built database from GitHub on first use. The `northwind.db` file is gitignored — it's always regenerated from the download.

## The MCP server

`northwind_server.py` is a stdio MCP server (JSON-RPC over stdin/stdout) that exposes four tools:

### `get_schema`

Returns all table names with their columns and types. No parameters. This is the LLM's way of discovering the database structure before writing queries.

### `query`

Runs a read-only SQL SELECT query. Rejects anything that isn't a SELECT. Results are capped at 100 rows (auto-appends `LIMIT 100` if no LIMIT clause is present).

```json
{"sql": "SELECT CompanyName, Country FROM Customers WHERE Country = 'Germany'"}
```

Returns: `{"columns": [...], "rows": [...], "row_count": N}`

### `execute`

Runs a data modification statement (INSERT, UPDATE, DELETE). Rejects DDL (DROP, ALTER, CREATE, TRUNCATE) and rejects SELECT queries.

```json
{"sql": "UPDATE Products SET UnitsInStock = UnitsInStock - 5 WHERE ProductID = 17"}
```

Returns: `{"rows_affected": N}`

### `lookup`

Convenience tool: `SELECT * FROM <table> LIMIT <N>`. Validates the table name against the actual schema to prevent injection. Default limit is 10.

```json
{"table": "Customers", "limit": 5}
```

The separation of `query` (read) from `execute` (write) is what makes approval gates meaningful — the agent can freely explore data, but data modifications require human sign-off.

## The five skills

### 1. Order Fulfillment (4 steps)

Processes a pending order end-to-end: finds an unshipped order, verifies inventory for all line items, updates the database, and generates a shipping summary.

- **Step 1:** Look up a pending order (ShippedDate IS NULL) and customer info
- **Step 2:** Check product inventory for each line item
- **Step 3:** **[PRE-APPROVAL REQUIRED]** Update ShippedDate and reduce UnitsInStock
- **Step 4:** Generate shipping summary with carrier and total cost

Demonstrates: schema discovery via `get_schema`, error recovery (the LLM learns the table is `[Order Details]` not `OrderDetails`), pre-approval before data modification.

### 2. Inventory Restock (5 steps)

Identifies products running low on stock and creates purchase orders from suppliers.

- **Step 1:** Find products where UnitsInStock <= ReorderLevel and not discontinued
- **Step 2:** Look up supplier details for each low-stock product
- **Step 3:** Calculate recommended restock quantities (2x ReorderLevel - current stock - on order)
- **Step 4:** **[PRE-APPROVAL REQUIRED]** Update UnitsOnOrder for restocked products
- **Step 5:** **[OPTIONAL]** Verify updates took effect

Demonstrates: multi-step analytical reasoning, optional verification step, supplier consolidation logic.

### 3. Customer Onboarding (4 steps)

Adds a new customer to the database with duplicate checking and sales rep assignment.

- **Step 1:** Collect customer details (generates a 5-char CustomerID from company name)
- **Step 2:** Check for duplicate companies or phone numbers
- **Step 3:** Find a sales representative by querying order history
- **Step 4:** **[APPROVAL REQUIRED]** Insert the customer record, then confirm

Demonstrates: human interaction (collecting info), post-approval pattern (insert happens, then human reviews), error recovery when the LLM tries non-existent columns.

### 4. Monthly Sales Report (4 steps)

Pure read-only analytics — no data modifications, no approval gates.

- **Step 1:** Aggregate monthly revenue, order count, unique customers
- **Step 2:** Top 5 products and customers by revenue/volume
- **Step 3:** Flag anomalies — unusually large orders, demand spikes, churned customers
- **Step 4:** Compile an executive summary

Demonstrates: that not every skill needs approval gates, complex analytical SQL, LLM-generated narrative from data.

### 5. Returns Processing (5 steps)

Handles a product return with eligibility checking and chained approval gates.

- **Step 1:** Look up the original order and verify it was shipped
- **Step 2:** Check return eligibility (30-day window from ShippedDate)
- **Step 3:** **[PRE-APPROVAL REQUIRED]** Calculate refund amount
- **Step 4:** **[PRE-APPROVAL REQUIRED]** Update OrderDetails and restore UnitsInStock
- **Step 5:** Confirm all changes were applied correctly

Demonstrates: chained pre-approval gates (steps 3 and 4), date arithmetic, inventory reversal.

## Approval gate patterns

The five skills collectively demonstrate all of Proceda's approval patterns:

| Pattern | Where used | What it means |
|---------|-----------|---------------|
| `[PRE-APPROVAL REQUIRED]` | order-fulfillment step 3, inventory-restock step 4, returns-processing steps 3-4 | Human approves *before* the step begins |
| `[APPROVAL REQUIRED]` | customer-onboarding step 4 | Human approves *after* the step completes |
| `[OPTIONAL]` | inventory-restock step 5 | Agent may skip this step |
| No marker | monthly-sales-report (all steps) | No human approval needed |

## Execution traces

After running `run_all.py`, traces are saved to `examples/northwind-ops/traces/`:

```
traces/
├── order-fulfillment/
│   ├── events.jsonl        # Full event log (every tool call, message, approval)
│   └── summary.json        # Structured run summary
├── inventory-restock/
│   ├── events.jsonl
│   └── summary.json
├── customer-onboarding/
│   ├── events.jsonl
│   └── summary.json
├── monthly-sales-report/
│   ├── events.jsonl
│   └── summary.json
├── returns-processing/
│   ├── events.jsonl
│   └── summary.json
└── report.json             # Combined report across all skills
```

### Reading event logs

Each `events.jsonl` file contains one JSON object per line, each a `RunEvent` with a `type` and `payload`. Key event types:

- `STEP_STARTED` / `STEP_COMPLETED` — step lifecycle
- `TOOL_CALLED` / `TOOL_COMPLETED` / `TOOL_FAILED` — every MCP tool interaction
- `APPROVAL_REQUESTED` / `APPROVAL_RESPONDED` — approval gate activations
- `MESSAGE_ASSISTANT` — the LLM's reasoning and responses

You can replay a trace with:

```bash
proceda replay examples/northwind-ops/traces/order-fulfillment/
```

## Configuration

The demo's `proceda.yaml`:

```yaml
llm:
  model: anthropic/claude-haiku-4-5-20251001
  temperature: 0.3

apps:
  - name: northwind
    description: Northwind company database (customers, orders, products, employees)
    transport: stdio
    command: ["uv", "run", "python", "examples/northwind-ops/northwind_server.py"]
```

Temperature is set to 0.3 (lower than default 0.7) for more deterministic SQL generation.

You can use any model — swap in Sonnet for more thorough analysis, or keep Haiku for faster and cheaper runs.

## How the pieces connect

```
┌─────────────┐
│  SKILL.md   │  Defines the SOP: steps, approval gates, required tools
└──────┬──────┘
       │
       ▼
┌─────────────┐    ┌──────────────────┐
│   Proceda   │───▶│  northwind_server │  MCP server (stdio)
│   Runtime   │◀───│    .py            │  JSON-RPC over stdin/stdout
└──────┬──────┘    └────────┬─────────┘
       │                    │
       ▼                    ▼
┌─────────────┐    ┌──────────────────┐
│  LLM (API)  │    │  northwind.db    │  SQLite database
└─────────────┘    └──────────────────┘
```

1. Proceda loads the SKILL.md and connects to the MCP server
2. For each step, Proceda sends the step instructions + tool schemas to the LLM
3. The LLM decides which tools to call (query, execute, get_schema, lookup)
4. Proceda routes tool calls to the MCP server, which executes SQL against northwind.db
5. Results flow back to the LLM, which reasons about them and decides next actions
6. At approval gates, Proceda pauses for human review (or auto-approves in batch mode)
7. Events are emitted at every transition and captured by event sinks

## Extending the demo

To add a new skill:

1. Create a new directory under `examples/northwind-ops/` (e.g., `employee-performance/`)
2. Write a `SKILL.md` with the standard format
3. Use `northwind__query`, `northwind__execute`, `northwind__get_schema`, and/or `northwind__lookup` in `required_tools`
4. Add the skill name to the `SKILLS` list in `run.py` and `run_all.py`
5. The existing test suite will automatically pick it up via `_get_example_dirs()`

To add a new tool to the MCP server:

1. Add the tool function in `northwind_server.py`
2. Add the tool schema to the `TOOLS` list
3. Add the dispatch case in `handle_request()`
4. Add tests in `tests/test_integration/test_northwind_server.py`
