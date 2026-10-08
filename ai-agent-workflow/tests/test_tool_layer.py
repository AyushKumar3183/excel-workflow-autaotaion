"""Unit tests for the tool layer: ToolRegistry, primitives, and data sources.

Tests cover:
- Tool registration and lookup
- Unknown tool error handling
- Invalid parameter error handling
- Primitives: filter_rows, compare_threshold, compute_column, group_by, rank, join_on, fuzzy_match, format_output, llm_generate
- WF002 10% boundary condition testing for price percentage difference
- Data sources: inventory, vendor prices, orders, employees, execution logs, query_database
"""

from pathlib import Path
import pytest

from app.tools.registry import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterError,
    ToolRegistry,
)
from app.tools.primitives import (
    compare_threshold,
    compute_column,
    detect_columns,
    filter_rows,
    format_output,
    fuzzy_match,
    group_by,
    join_on,
    llm_generate,
    normalize_columns,
    rank,
    set_llm_provider,
    validate_data,
)
from app.tools.data_sources import (
    load_employees,
    load_execution_logs,
    load_inventory,
    load_orders,
    load_vendor_prices,
    query_database,
)
from app.tools import default_tool_registry, register_all_tools


# ---------------------------------------------------------------------------
# Registry Unit Tests
# ---------------------------------------------------------------------------

def test_tool_registration_and_lookup():
    """Test registering tools and checking existence / retrieval."""
    registry = ToolRegistry()
    assert len(registry) == 0

    def sample_tool(val: int) -> int:
        return val * 2

    registry.register("sample_tool", sample_tool)
    assert registry.exists("sample_tool") is True
    assert registry.exists("unknown") is False
    assert registry.get("sample_tool") is sample_tool
    assert registry.list_tools() == ["sample_tool"]

    result = registry.execute("sample_tool", {"val": 21})
    assert result == 42


def test_unknown_tool_raises_tool_not_found():
    """Test that invoking an unregistered tool raises ToolNotFoundError."""
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError, match="Tool 'missing_calc' is not registered"):
        registry.execute("missing_calc", {"x": 1})


def test_invalid_tool_parameters():
    """Test that invalid or missing parameters produce ToolParameterError."""
    registry = ToolRegistry()

    def strict_tool(a: int, b: int) -> int:
        return a + b

    registry.register("strict_tool", strict_tool)

    # Unexpected argument
    with pytest.raises(ToolParameterError, match="unexpected arguments"):
        registry.execute("strict_tool", {"a": 1, "b": 2, "extra": 3})

    # Missing mandatory argument
    with pytest.raises(ToolParameterError, match="Argument mismatch"):
        registry.execute("strict_tool", {"a": 1})


def test_auto_registered_standard_tools():
    """Test that all required workflow tools are pre-registered in default registry."""
    required = [
        "load_file",
        "filter_rows",
        "join_on",
        "compute_column",
        "compare_threshold",
        "group_by",
        "rank",
        "fuzzy_match",
        "format_output",
        "llm_generate",
        "query_database",
        "load_inventory",
        "load_vendor_prices",
        "load_orders",
        "load_employees",
    ]
    for tool_name in required:
        assert default_tool_registry.exists(tool_name) is True, f"Missing standard tool: {tool_name}"


# ---------------------------------------------------------------------------
# Primitive Tool Tests
# ---------------------------------------------------------------------------

def test_primitive_filter_rows_operators():
    """Test filter_rows with numeric and string comparison operators."""
    items = [
        {"sku": "SKU-1", "stock": 5, "status": "active"},
        {"sku": "SKU-2", "stock": 25, "status": "active"},
        {"sku": "SKU-3", "stock": 10, "status": "archived"},
    ]

    # Less than
    low = filter_rows(items, field="stock", operator="<", value=15)
    assert len(low) == 2
    assert [r["sku"] for r in low] == ["SKU-1", "SKU-3"]

    # Equality
    archived = filter_rows(items, field="status", operator="==", value="archived")
    assert len(archived) == 1
    assert archived[0]["sku"] == "SKU-3"

    # Contains
    matches = filter_rows(items, field="sku", operator="contains", value="SKU-2")
    assert len(matches) == 1
    assert matches[0]["sku"] == "SKU-2"


def test_primitive_filter_rows_deduplicate():
    """Test filter_rows deduplication operation."""
    keywords = [
        {"keyword": "wireless headphones", "vol": 1000},
        {"keyword": "running shoes", "vol": 500},
        {"keyword": "wireless headphones", "vol": 800},
    ]
    deduped = filter_rows(keywords, operation="deduplicate", column="keyword")
    assert len(deduped) == 2
    assert [r["keyword"] for r in deduped] == ["wireless headphones", "running shoes"]


def test_primitive_compare_threshold():
    """Test compare_threshold attaches boolean flag to records."""
    inventory = [
        {"sku": "A", "stock": 8},
        {"sku": "B", "stock": 35},
    ]
    flagged = compare_threshold(inventory, field="stock", operator="<", threshold=20, flag_column="is_low")
    assert flagged[0]["is_low"] is True
    assert flagged[1]["is_low"] is False


def test_primitive_compute_column_reorder():
    """Test deterministic reorder quantity calculation: max(0, threshold*2 - stock)."""
    low_stock = [
        {"sku": "A", "current_stock": 5},
        {"sku": "B", "current_stock": 18},
    ]
    res = compute_column(low_stock, operation="reorder_quantity", threshold=20)
    # Target = 20 * 2 = 40. A: 40 - 5 = 35. B: 40 - 18 = 22.
    assert res[0]["suggested_reorder_quantity"] == 35
    assert res[1]["suggested_reorder_quantity"] == 22


def test_primitive_compute_column_price_and_boundary_percentages():
    """Test WF002 price difference and percentage difference using vendor price as denominator (Rule 16)."""
    # Rule 16: diff_pct = abs(our_price - vendor_price) / vendor_price * 100
    rows = [
        # Exactly 10% boundary: vendor = 100, our = 110 -> (110 - 100)/100 * 100 = 10.0%
        {"sku": "BOUNDARY", "unit_price": 110.0, "vendor_price": 100.0},
        # Greater than 10%: vendor = 100, our = 115 -> (115 - 100)/100 * 100 = 15.0%
        {"sku": "OVER", "unit_price": 115.0, "vendor_price": 100.0},
        # Less than 10%: vendor = 100, our = 105 -> (105 - 100)/100 * 100 = 5.0%
        {"sku": "UNDER", "unit_price": 105.0, "vendor_price": 100.0},
    ]
    res = compute_column(rows, operation="percentage_difference")
    assert res[0]["pct_diff"] == 10.0
    assert res[1]["pct_diff"] == 15.0
    assert res[2]["pct_diff"] == 5.0

    # Test filtering strictly greater than 10% vs boundary
    flagged = filter_rows(res, field="pct_diff", operator=">", value=10.0)
    assert len(flagged) == 1
    assert flagged[0]["sku"] == "OVER"


def test_primitive_group_by():
    """Test group_by aggregates counts by key."""
    records = [
        {"category": "Electronics", "id": 1},
        {"category": "Apparel", "id": 2},
        {"category": "Electronics", "id": 3},
    ]
    groups = group_by(records, group_key="category")
    # Groups should have Electronics count: 2, Apparel count: 1
    electronics = next(g for g in groups if g["category"] == "Electronics")
    apparel = next(g for g in groups if g["category"] == "Apparel")
    assert electronics["count"] == 2
    assert apparel["count"] == 1


def test_primitive_rank():
    """Test rank sorts records ascending and descending with optional limits."""
    candidates = [
        {"name": "Alice", "capacity": 15},
        {"name": "Bob", "capacity": 25},
        {"name": "Carol", "capacity": 8},
    ]
    # Descending rank (highest capacity first)
    ranked = rank(candidates, sort_by="capacity", ascending=False, limit=2)
    assert len(ranked) == 2
    assert ranked[0]["name"] == "Bob"
    assert ranked[1]["name"] == "Alice"


def test_primitive_join_on():
    """Test join_on merges internal products with vendor prices."""
    internal = [
        {"sku": "SKU-1", "name": "Headphones", "unit_price": 99.0},
        {"sku": "SKU-2", "name": "Keyboard", "unit_price": 129.0},
    ]
    vendor = [
        {"sku": "SKU-1", "vendor_price": 85.0},
    ]
    joined = join_on(internal, vendor, key="sku", how="inner")
    assert len(joined) == 1
    assert joined[0]["sku"] == "SKU-1"
    assert joined[0]["vendor_vendor_price"] == 85.0


def test_primitive_fuzzy_match():
    """Test fuzzy_match detects both exact matches and high attribute similarity."""
    catalog = [
        {"sku": "SKU-01", "product_name": "Wireless Noise Canceling Headphones"},
        {"sku": "SKU-02", "product_name": "Wireless Noise Cancelling Headphone"},
        {"sku": "SKU-03", "product_name": "Ceramic Coffee Mug 16oz"},
    ]
    matches = fuzzy_match(catalog, field="product_name", mode="similarity", threshold=0.8)
    assert len(matches) == 1
    assert matches[0]["similarity_score"] >= 0.8
    assert matches[0]["matched_field"] == "product_name"


def test_primitive_format_output():
    """Test format_output structure."""
    out = format_output(data=[{"sku": "A"}], format="restock_report", count=1)
    assert out["format"] == "restock_report"
    assert out["data"] == [{"sku": "A"}]
    assert out["count"] == 1


def test_primitive_llm_generate_interface():
    """Test llm_generate fallback and pluggable custom provider."""
    # Default fallback
    desc = llm_generate(task="product_description", context={"name": "Wireless Earbuds"})
    assert "Wireless Earbuds" in desc

    # Custom mock provider
    def mock_provider(task: str, **kwargs) -> str:
        return f"MOCKED_{task.upper()}"

    set_llm_provider(mock_provider)
    try:
        custom_out = llm_generate(task="campaign_brief")
        assert custom_out == "MOCKED_CAMPAIGN_BRIEF"
    finally:
        set_llm_provider(None)


# ---------------------------------------------------------------------------
# Simulated Data Sources Tests
# ---------------------------------------------------------------------------

def test_load_inventory_data():
    """Test loading simulated inventory.csv."""
    inv = load_inventory()
    assert len(inv) >= 5
    first = inv[0]
    assert "sku" in first
    assert "current_stock" in first
    assert "minimum_stock" in first
    assert isinstance(first["current_stock"], int)


def test_load_vendor_prices_data():
    """Test loading simulated vendor_prices.csv."""
    prices = load_vendor_prices()
    assert len(prices) >= 5
    assert "vendor_price" in prices[0]
    assert isinstance(prices[0]["vendor_price"], float)


def test_load_orders_and_query_database():
    """Test loading simulated orders and querying by identifier or shipment."""
    orders = load_orders()
    assert len(orders) >= 3

    # Query by order ID
    ord_record = query_database(source="orders", identifier="ORD-1001")
    assert ord_record.get("order_id") == "ORD-1001"
    assert ord_record.get("customer_email") == "alice@example.com"

    # Query shipment by order ID
    shipment = query_database(source="shipments", order_id="ORD-1001")
    assert shipment.get("shipment_id") == "SHIP-8001"
    assert shipment.get("tracking_number") == "TRK-99001122"


def test_load_employees_and_execution_logs():
    """Test loading employee database and execution logs."""
    employees = load_employees()
    assert len(employees) >= 3
    assert any("python" in emp["skills"] for emp in employees)

    logs = load_execution_logs()
    assert len(logs) >= 5
    # Calculate failure rate on execution logs using compute_column
    rate = compute_column(logs, operation="failure_rate")
    assert isinstance(rate, float)
    assert rate > 0.0
