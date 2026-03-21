# ABOUTME: Tests for the Northwind MCP server's tool functions and request handling.
# ABOUTME: Uses a temporary SQLite database with minimal schema, not the full Northwind.

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "examples" / "northwind-ops"))

import northwind_server


@pytest.fixture()
def temp_db(tmp_path: Path) -> Path:
    """Create a minimal test database with a few tables and rows."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE Customers (
            CustomerID TEXT PRIMARY KEY,
            CompanyName TEXT NOT NULL,
            ContactName TEXT,
            Country TEXT
        );
        CREATE TABLE Products (
            ProductID INTEGER PRIMARY KEY,
            ProductName TEXT NOT NULL,
            UnitPrice REAL,
            UnitsInStock INTEGER
        );
        CREATE TABLE Orders (
            OrderID INTEGER PRIMARY KEY,
            CustomerID TEXT,
            OrderDate TEXT,
            ShippedDate TEXT
        );

        INSERT INTO Customers VALUES ('ALFKI', 'Alfreds Futterkiste', 'Maria Anders', 'Germany');
        INSERT INTO Customers VALUES ('BERGS', 'Berglunds', 'Christina Berglund', 'Sweden');
        INSERT INTO Customers VALUES ('CHOPS', 'Chop-suey Chinese', 'Yang Wang', 'Switzerland');

        INSERT INTO Products VALUES (1, 'Chai', 18.00, 39);
        INSERT INTO Products VALUES (2, 'Chang', 19.00, 17);
        INSERT INTO Products VALUES (3, 'Aniseed Syrup', 10.00, 13);

        INSERT INTO Orders VALUES (10248, 'ALFKI', '1996-07-04', '1996-07-16');
        INSERT INTO Orders VALUES (10249, 'BERGS', '1996-07-05', NULL);
    """)
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def _set_db_path(temp_db: Path) -> None:
    """Point the server at the temp database."""
    northwind_server.DB_PATH = temp_db


class TestHandleRequest:
    def test_initialize(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        resp = northwind_server.handle_request(req)
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "northwind"

    def test_tools_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        resp = northwind_server.handle_request(req)
        tools = resp["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"get_schema", "query", "execute", "lookup"}

    def test_unknown_method(self) -> None:
        req = {"jsonrpc": "2.0", "id": 3, "method": "bogus/method"}
        resp = northwind_server.handle_request(req)
        assert "error" in resp

    def test_unknown_tool(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        }
        resp = northwind_server.handle_request(req)
        assert "error" in resp


class TestGetSchema:
    def test_returns_tables(self) -> None:
        result = northwind_server.tool_get_schema()
        table_names = {t["name"] for t in result["tables"]}
        assert "Customers" in table_names
        assert "Products" in table_names
        assert "Orders" in table_names

    def test_returns_columns(self) -> None:
        result = northwind_server.tool_get_schema()
        customers = next(t for t in result["tables"] if t["name"] == "Customers")
        col_names = {c["name"] for c in customers["columns"]}
        assert "CustomerID" in col_names
        assert "CompanyName" in col_names


class TestQuery:
    def test_select(self) -> None:
        result = northwind_server.tool_query("SELECT * FROM Customers")
        assert "columns" in result
        assert "CustomerID" in result["columns"]
        assert result["row_count"] == 3

    def test_rejects_insert(self) -> None:
        result = northwind_server.tool_query("INSERT INTO Customers VALUES ('X','X','X','X')")
        assert "error" in result

    def test_rejects_update(self) -> None:
        result = northwind_server.tool_query("UPDATE Customers SET Country='X'")
        assert "error" in result

    def test_rejects_delete(self) -> None:
        result = northwind_server.tool_query("DELETE FROM Customers")
        assert "error" in result

    def test_auto_limit(self, temp_db: Path) -> None:
        # Insert enough rows to exceed MAX_ROWS
        conn = sqlite3.connect(str(temp_db))
        for i in range(150):
            conn.execute(
                "INSERT INTO Products VALUES (?, ?, ?, ?)",
                (100 + i, f"Product {i}", 10.0, 50),
            )
        conn.commit()
        conn.close()

        result = northwind_server.tool_query("SELECT * FROM Products")
        assert result["row_count"] <= northwind_server.MAX_ROWS

    def test_preserves_explicit_limit(self) -> None:
        result = northwind_server.tool_query("SELECT * FROM Customers LIMIT 1")
        assert result["row_count"] == 1

    def test_sql_error(self) -> None:
        result = northwind_server.tool_query("SELECT * FROM NonExistentTable")
        assert "error" in result


class TestExecute:
    def test_insert(self) -> None:
        result = northwind_server.tool_execute(
            "INSERT INTO Customers VALUES ('NEWCO', 'New Company', 'John', 'USA')"
        )
        assert result["rows_affected"] == 1

        # Verify the insert
        verify = northwind_server.tool_query("SELECT * FROM Customers WHERE CustomerID='NEWCO'")
        assert verify["row_count"] == 1

    def test_update(self) -> None:
        result = northwind_server.tool_execute(
            "UPDATE Customers SET Country='Austria' WHERE CustomerID='ALFKI'"
        )
        assert result["rows_affected"] == 1

    def test_delete(self) -> None:
        result = northwind_server.tool_execute("DELETE FROM Customers WHERE CustomerID='CHOPS'")
        assert result["rows_affected"] == 1

    def test_rejects_drop(self) -> None:
        result = northwind_server.tool_execute("DROP TABLE Customers")
        assert "error" in result

    def test_rejects_alter(self) -> None:
        result = northwind_server.tool_execute("ALTER TABLE Customers ADD COLUMN Email TEXT")
        assert "error" in result

    def test_rejects_create(self) -> None:
        result = northwind_server.tool_execute("CREATE TABLE Foo (id INTEGER)")
        assert "error" in result

    def test_rejects_truncate(self) -> None:
        result = northwind_server.tool_execute("TRUNCATE TABLE Customers")
        assert "error" in result

    def test_rejects_select(self) -> None:
        result = northwind_server.tool_execute("SELECT * FROM Customers")
        assert "error" in result


class TestLookup:
    def test_valid_table(self) -> None:
        result = northwind_server.tool_lookup("Customers")
        assert "columns" in result
        assert result["row_count"] == 3

    def test_case_insensitive(self) -> None:
        result = northwind_server.tool_lookup("customers")
        assert "columns" in result
        assert result["row_count"] == 3

    def test_with_limit(self) -> None:
        result = northwind_server.tool_lookup("Customers", limit=1)
        assert result["row_count"] == 1

    def test_invalid_table(self) -> None:
        result = northwind_server.tool_lookup("NonExistent")
        assert "error" in result
        assert "not found" in result["error"].lower()
