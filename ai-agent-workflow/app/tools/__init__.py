"""Tools layer: dynamic registry, reusable primitives, and domain data sources."""

from typing import Optional

from app.tools.registry import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterError,
    ToolRegistry,
    default_tool_registry,
    get_tool_registry,
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
    load_file,
    normalize_columns,
    set_llm_provider,
    validate_data,
)
from app.tools.data_sources import (
    load_employees,
    load_execution_logs,
    load_inventory,
    load_orders,
    load_product_catalog,
    load_vendor_prices,
    query_database,
)


def register_all_tools(registry: Optional[ToolRegistry] = None) -> ToolRegistry:
    """Register all domain-agnostic primitives and simulated data sources into a registry."""
    reg = registry or default_tool_registry

    # Standard computational primitives
    reg.register("load_file", load_file)
    reg.register("filter_rows", filter_rows)
    reg.register("join_on", join_on)
    reg.register("compute_column", compute_column)
    reg.register("compare_threshold", compare_threshold)
    reg.register("group_by", group_by)
    reg.register("rank", rank)
    reg.register("fuzzy_match", fuzzy_match)
    reg.register("detect_columns", detect_columns)
    reg.register("normalize_columns", normalize_columns)
    reg.register("validate_data", validate_data)
    reg.register("format_output", format_output)
    reg.register("llm_generate", llm_generate)

    # Simulated data source tools
    reg.register("query_database", query_database)
    reg.register("load_inventory", load_inventory)
    reg.register("load_product_catalog", load_product_catalog)
    reg.register("load_vendor_prices", load_vendor_prices)
    reg.register("load_orders", load_orders)
    reg.register("load_employees", load_employees)
    reg.register("load_execution_logs", load_execution_logs)

    return reg


# Auto-register standard tools into default_tool_registry on import
from app.tools.primitives import rank  # ensure rank is imported
register_all_tools(default_tool_registry)


__all__ = [
    "ToolRegistry",
    "ToolError",
    "ToolNotFoundError",
    "ToolParameterError",
    "ToolExecutionError",
    "default_tool_registry",
    "get_tool_registry",
    "register_all_tools",
    "load_file",
    "filter_rows",
    "join_on",
    "compute_column",
    "compare_threshold",
    "group_by",
    "rank",
    "fuzzy_match",
    "detect_columns",
    "normalize_columns",
    "validate_data",
    "format_output",
    "llm_generate",
    "set_llm_provider",
    "load_inventory",
    "load_vendor_prices",
    "load_orders",
    "load_employees",
    "load_execution_logs",
    "query_database",
]
