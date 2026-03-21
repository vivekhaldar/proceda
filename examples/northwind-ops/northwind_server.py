# ABOUTME: MCP stdio server that exposes the Northwind SQLite database as tools.
# ABOUTME: Provides get_schema, query (read-only), execute (DML only), and lookup tools.

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

from seed_db import ensure_db

DB_PATH: Path | None = None
MAX_ROWS = 100


def get_connection() -> sqlite3.Connection:
    assert DB_PATH is not None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def tool_get_schema() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = []
    for (table_name,) in cursor.fetchall():
        cursor.execute(f"PRAGMA table_info([{table_name}])")
        columns = [{"name": row[1], "type": row[2]} for row in cursor.fetchall()]
        tables.append({"name": table_name, "columns": columns})
    conn.close()
    return {"tables": tables}


def tool_query(sql: str) -> dict:
    stripped = sql.strip()
    if not re.match(r"(?i)^SELECT\b", stripped):
        return {
            "error": "Only SELECT queries are allowed. Use the 'execute' tool for modifications."
        }

    if not re.search(r"(?i)\bLIMIT\b", stripped):
        stripped = stripped.rstrip(";") + f" LIMIT {MAX_ROWS}"

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(stripped)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = [list(row) for row in cursor.fetchall()]
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()


def tool_execute(sql: str) -> dict:
    ddl_pattern = r"(?i)^\s*(DROP|ALTER|CREATE|TRUNCATE)\b"
    if re.match(ddl_pattern, sql.strip()):
        return {"error": "DDL statements (DROP, ALTER, CREATE, TRUNCATE) are not allowed."}

    allowed_pattern = r"(?i)^\s*(INSERT|UPDATE|DELETE)\b"
    if not re.match(allowed_pattern, sql.strip()):
        return {"error": "Only INSERT, UPDATE, and DELETE statements are allowed."}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        conn.commit()
        return {"rows_affected": cursor.rowcount}
    except sqlite3.Error as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def tool_lookup(table: str, limit: int = 10) -> dict:
    conn = get_connection()
    cursor = conn.cursor()

    # Validate table name against actual schema
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    valid_tables = {row[0].lower() for row in cursor.fetchall()}
    if table.lower() not in valid_tables:
        conn.close()
        actual = sorted(t for t in valid_tables)
        return {"error": f"Table '{table}' not found. Available tables: {actual}"}

    # Find the actual table name with correct casing
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)", (table,)
    )
    actual_name = cursor.fetchone()[0]

    cursor.execute(f"SELECT * FROM [{actual_name}] LIMIT ?", (min(limit, MAX_ROWS),))
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    rows = [list(row) for row in cursor.fetchall()]
    conn.close()
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


TOOLS = [
    {
        "name": "get_schema",
        "description": "Get the database schema: all table names and their columns with types.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "query",
        "description": (
            "Run a read-only SQL SELECT query against the Northwind database. "
            "Returns columns, rows, and row count. Results are capped at 100 rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A SELECT SQL query"}},
            "required": ["sql"],
        },
    },
    {
        "name": "execute",
        "description": (
            "Run a data modification statement (INSERT, UPDATE, or DELETE) "
            "against the Northwind database. DDL statements are not allowed. "
            "Returns rows affected."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "An INSERT, UPDATE, or DELETE statement"}
            },
            "required": ["sql"],
        },
    },
    {
        "name": "lookup",
        "description": (
            "Look up rows from a specific table by name. "
            "A convenience wrapper around SELECT * with a configurable row limit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name to look up"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return (default 10, max 100)",
                    "default": 10,
                },
            },
            "required": ["table"],
        },
    },
]


def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "northwind", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None  # type: ignore[return-value]

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        name = request.get("params", {}).get("name", "")
        arguments = request.get("params", {}).get("arguments", {})

        if name == "get_schema":
            result = tool_get_schema()
        elif name == "query":
            result = tool_query(arguments.get("sql", ""))
        elif name == "execute":
            result = tool_execute(arguments.get("sql", ""))
        elif name == "lookup":
            result = tool_lookup(arguments.get("table", ""), arguments.get("limit", 10))
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }

        is_error = "error" in result
        text = json.dumps(result, indent=2, default=str)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main() -> None:
    global DB_PATH
    DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "northwind.db"
    ensure_db(DB_PATH)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
