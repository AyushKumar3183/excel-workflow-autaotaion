"""Domain-agnostic, reusable data primitives for workflow execution.

All functions operate on generic Python data structures (lists, dicts, primitives)
without any workflow-specific or business-specific dependencies.
"""

from __future__ import annotations

import csv
import difflib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Union

import openpyxl

from app.tools.registry import ToolParameterError


# ---------------------------------------------------------------------------
# File Operations
# ---------------------------------------------------------------------------

def load_file(filepath: Union[str, Path], format: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
    """Load tabular data from CSV, TSV, JSON, JSONL, or XLSX files into a list of dicts.
    
    Args:
        filepath: Path to the target file.
        format: Optional format hint ('csv', 'tsv', 'json', 'jsonl', 'xlsx').
        
    Returns:
        List of row dictionaries.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    ext = (format or path.suffix).lower().lstrip(".")

    if ext in {"csv", "tsv"}:
        delimiter = "\t" if ext == "tsv" else ","
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            return [dict(row) for row in reader]

    elif ext == "json":
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
            if isinstance(content, list):
                return content
            elif isinstance(content, dict):
                return [content]
            raise ToolParameterError(f"Unexpected JSON root type in {path}: {type(content).__name__}")

    elif ext == "jsonl":
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean_line = line.strip()
                if clean_line:
                    try:
                        records.append(json.loads(clean_line))
                    except json.JSONDecodeError as jde:
                        raise ToolParameterError(f"Malformed JSONL on line {line_no} in {path}: {jde}") from jde
        return records

    elif ext in {"xlsx", "xls"}:
        wb = openpyxl.load_workbook(path, data_only=True)
        sheet = wb.active
        if sheet is None:
            return []
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip().lower().replace(" ", "_") if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
        records = []
        for r in rows[1:]:
            if any(c is not None for c in r):
                record = {headers[i]: r[i] if i < len(r) else None for i in range(len(headers))}
                records.append(record)
        return records

    else:
        # Fallback: try reading as CSV
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [dict(row) for row in reader]


# ---------------------------------------------------------------------------
# Tabular Processing Primitives
# ---------------------------------------------------------------------------

def filter_rows(
    data: List[Dict[str, Any]],
    field: Optional[str] = None,
    operator: Optional[str] = None,
    value: Any = None,
    operation: Optional[str] = None,
    column: Optional[str] = None,
    **kwargs,
) -> List[Dict[str, Any]]:
    """Filter or transform rows based on conditions or specific operations.
    
    Supported operations:
        - Deduplication: operation='deduplicate', column='<name>'
        - Summarization: operation='summarize'
        - Operator filtering: field='<field>', operator='<op>', value=<val>
    """
    if isinstance(data, (str, Path)):
        data = load_file(data)
    elif not isinstance(data, list):
        raise ToolParameterError(f"filter_rows expects a list of dicts, got {type(data).__name__}.")

    # Deduplication operation
    if operation == "deduplicate":
        seen = set()
        deduped = []
        for row in data:
            key = row.get(column) if column else tuple(sorted(row.items()))
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        return deduped

    # Summarization operation
    if operation == "summarize":
        return [{"total_count": len(data), "sample_items": [r.get("name") or r.get("sku") for r in data[:5]]}]

    # Operator filtering
    if field is None or operator is None:
        return data

    filtered: List[Dict[str, Any]] = []
    for row in data:
        row_val = row.get(field)
        if _evaluate_operator(row_val, operator, value):
            filtered.append(row)
    return filtered


def _evaluate_operator(left: Any, op: str, right: Any) -> bool:
    """Safe comparison helper for filtering and thresholds without eval()."""
    op = op.strip().lower()
    
    if op == "is_empty":
        return left is None or left == "" or (isinstance(left, (list, dict, set)) and len(left) == 0)
    if op == "is_not_empty":
        return not (left is None or left == "" or (isinstance(left, (list, dict, set)) and len(left) == 0))

    if op in ("contains", "in"):
        if op == "contains":
            if left is None:
                return False
            return str(right).lower() in str(left).lower()
        else:
            if right is None:
                return False
            return left in right

    if op == "not_in":
        return right is None or left not in right

    # Numeric comparisons
    if op in ("<", ">", "<=", ">="):
        try:
            num_left = float(left)
            num_right = float(right)
            if op == "<":
                return num_left < num_right
            if op == ">":
                return num_left > num_right
            if op == "<=":
                return num_left <= num_right
            if op == ">=":
                return num_left >= num_right
        except (ValueError, TypeError):
            return False

    # Equality comparisons
    if op in ("==", "="):
        if isinstance(right, bool):
            if isinstance(left, str):
                return (left.lower() == "true") is right
            return bool(left) == right
        return str(left) == str(right)

    if op == "!=":
        if isinstance(right, bool):
            if isinstance(left, str):
                return (left.lower() == "true") is not right
            return bool(left) != right
        return str(left) != str(right)

    return False


def compare_threshold(
    data: List[Dict[str, Any]],
    field: str,
    operator: str,
    threshold: Any,
    flag_column: str = "is_low_stock",
    **kwargs,
) -> List[Dict[str, Any]]:
    """Compare a field in each record against a threshold and attach a boolean flag column."""
    if isinstance(data, (str, Path)):
        data = load_file(data)
    elif not isinstance(data, list):
        raise ToolParameterError(f"compare_threshold expects a list of dicts, got {type(data).__name__}.")

    result: List[Dict[str, Any]] = []
    for row in data:
        row_copy = dict(row)
        val = row.get(field)
        # Check field aliases (e.g. stock vs current_stock)
        if val is None:
            if field in ("stock", "current_stock"):
                val = row.get("current_stock") if field == "stock" else row.get("stock")
            elif field in ("price", "unit_price"):
                val = row.get("unit_price") if field == "price" else row.get("price")

        row_copy[flag_column] = _evaluate_operator(val, operator, threshold)
        result.append(row_copy)
    return result


def join_on(
    left: List[Dict[str, Any]],
    right: List[Dict[str, Any]],
    key: str,
    how: str = "inner",
    right_prefix: str = "vendor_",
    **kwargs,
) -> List[Dict[str, Any]]:
    """Relational join of two tabular datasets on a matching key."""
    if isinstance(left, (str, Path)):
        left = load_file(left)
    if isinstance(right, (str, Path)):
        right = load_file(right)
    if not isinstance(left, list) or not isinstance(right, list):
        raise ToolParameterError("join_on expects two lists of dictionaries for 'left' and 'right'.")

    # Index right table by key
    right_indexed: Dict[str, List[Dict[str, Any]]] = {}
    for r in right:
        val = str(r.get(key, "")).strip().upper()
        if val:
            right_indexed.setdefault(val, []).append(r)

    joined: List[Dict[str, Any]] = []
    for l_row in left:
        l_key = str(l_row.get(key, "")).strip().upper()
        matches = right_indexed.get(l_key, [])
        if matches:
            for m in matches:
                merged = dict(l_row)
                for k, v in m.items():
                    if k != key:
                        merged[f"{right_prefix}{k}"] = v
                    else:
                        merged[k] = v
                joined.append(merged)
        elif how.lower() == "left":
            joined.append(dict(l_row))

    return joined


def compute_column(
    data: Any,
    operation: Optional[str] = None,
    formula: Optional[str] = None,
    field: Optional[str] = None,
    threshold: Any = None,
    **kwargs,
) -> Any:
    """Perform deterministic business calculations and column derivations."""
    op = (operation or formula or "").lower()

    if op == "select_top":
        if isinstance(data, list) and data:
            return data[0]
        return None

    if op == "failure_rate":
        if not isinstance(data, list) or not data:
            return 0.0
        failed = sum(1 for r in data if str(r.get("status", "")).upper() == "FAILED" or r.get("error"))
        return round(failed / len(data), 4)

    if op == "avg_execution_time":
        if not isinstance(data, list) or not data:
            return 0.0
        durations = [float(r.get("duration_ms", 0)) for r in data if r.get("duration_ms") is not None]
        return round(sum(durations) / len(durations), 2) if durations else 0.0

    if not isinstance(data, list):
        if isinstance(data, dict) and field:
            return data.get(field)
        return data

    updated: List[Dict[str, Any]] = []
    thresh_val = float(threshold) if threshold is not None else 0.0

    for row in data:
        row_copy = dict(row)

        if op in ("reorder_quantity", "reorder"):
            stock = float(row.get("current_stock") or row.get("stock") or 0)
            reorder_qty = max(0.0, (thresh_val * 2) - stock)
            row_copy["suggested_reorder_quantity"] = int(reorder_qty)

        elif op == "price_difference":
            internal = float(row.get("unit_price") or row.get("price") or row.get("our_price") or 0)
            vendor = float(row.get("vendor_vendor_price") or row.get("vendor_price") or 0)
            row_copy["price_diff"] = round(internal - vendor, 2)

        elif op in ("percentage_difference", "price_diff_percentage"):
            internal = float(row.get("unit_price") or row.get("price") or row.get("our_price") or 0)
            vendor = float(row.get("vendor_vendor_price") or row.get("vendor_price") or 0)
            if vendor > 0:
                pct = (abs(internal - vendor) / vendor) * 100.0
                row_copy["pct_diff"] = round(pct, 2)
            else:
                row_copy["pct_diff"] = 0.0

        elif op in ("workload_capacity", "capacity"):
            max_cap = float(row.get("max_capacity_hours") or 40)
            curr = float(row.get("current_workload_hours") or 0)
            row_copy["available_capacity"] = max(0.0, max_cap - curr)

        elif op == "assign_confidence":
            score = 0.5
            if row.get("exact_sku_match"):
                score = 1.0
            elif float(row.get("similarity_score", 0)) > 0.8:
                score = 0.85
            row_copy["confidence"] = score

        elif field:
            row_copy[field] = row.get(field)

        updated.append(row_copy)

    return updated


def group_by(
    data: List[Dict[str, Any]],
    group_key: Optional[str] = None,
    field: Optional[str] = None,
    operation: str = "count",
    **kwargs,
) -> List[Dict[str, Any]]:
    """Group list of records by key and compute aggregate counts or groupings."""
    if not isinstance(data, list):
        raise ToolParameterError(f"group_by expects list of dicts, got {type(data).__name__}.")

    key = group_key or field or "group"
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for row in data:
        k = str(row.get(key) or "unknown")
        grouped.setdefault(k, []).append(row)

    results: List[Dict[str, Any]] = []
    for k, rows in grouped.items():
        if operation == "count":
            results.append({key: k, "count": len(rows)})
        else:
            results.append({key: k, "items": rows, "count": len(rows)})

    return results


def rank(
    data: List[Dict[str, Any]],
    sort_by: str,
    ascending: bool = True,
    limit: Optional[int] = None,
    **kwargs,
) -> List[Dict[str, Any]]:
    """Sort list of records by the specified key in ascending or descending order."""
    if not isinstance(data, list):
        raise ToolParameterError(f"rank expects list of dicts, got {type(data).__name__}.")

    def _sort_key(item: Dict[str, Any]):
        val = item.get(sort_by)
        if val is None:
            return (0, 0)
        try:
            return (1, float(val))
        except (ValueError, TypeError):
            return (2, str(val))

    sorted_data = sorted(data, key=_sort_key, reverse=not ascending)
    if limit is not None and limit > 0:
        return sorted_data[:limit]
    return sorted_data


def fuzzy_match(
    data: List[Dict[str, Any]],
    field: Optional[str] = None,
    mode: str = "similarity",
    threshold: float = 0.75,
    **kwargs,
) -> List[Dict[str, Any]]:
    """Identify duplicate or similar records using exact match or fuzzy string similarity."""
    if not isinstance(data, list):
        raise ToolParameterError(f"fuzzy_match expects list of dicts, got {type(data).__name__}.")

    target_field = field or "product_name"
    matches: List[Dict[str, Any]] = []

    for i in range(len(data)):
        for j in range(i + 1, len(data)):
            item_a = data[i]
            item_b = data[j]
            val_a = str(item_a.get(target_field) or "").strip().lower()
            val_b = str(item_b.get(target_field) or "").strip().lower()

            if mode == "exact":
                is_match = val_a == val_b and val_a != ""
                if is_match:
                    matches.append({
                        "item_a": item_a,
                        "item_b": item_b,
                        "matched_field": target_field,
                        "exact_sku_match": True,
                        "similarity_score": 1.0,
                    })
            else:
                sim = difflib.SequenceMatcher(None, val_a, val_b).ratio()
                if sim >= threshold:
                    matches.append({
                        "item_a": item_a,
                        "item_b": item_b,
                        "matched_field": target_field,
                        "exact_sku_match": False,
                        "similarity_score": round(sim, 3),
                    })

    return matches


def detect_columns(data: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
    """Detect column schema and sample types from tabular data."""
    if not isinstance(data, list) or not data:
        return {"columns": [], "row_count": 0}

    columns: Dict[str, str] = {}
    for k, v in data[0].items():
        columns[k] = type(v).__name__

    return {"columns": list(columns.keys()), "schema": columns, "row_count": len(data)}


def normalize_columns(
    data: List[Dict[str, Any]],
    schema: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> List[Dict[str, Any]]:
    """Normalize dictionary keys to lowercase with stripped whitespace and underscores."""
    if not isinstance(data, list):
        raise ToolParameterError(f"normalize_columns expects list of dicts, got {type(data).__name__}.")

    normalized: List[Dict[str, Any]] = []
    for row in data:
        cleaned = {
            str(k).strip().lower().replace(" ", "_"): v
            for k, v in row.items()
        }
        normalized.append(cleaned)
    return normalized


def validate_data(
    data: Any = None,
    required_fields: Optional[List[str]] = None,
    attributes: Optional[Dict[str, Any]] = None,
    required: Optional[List[str]] = None,
    identifier: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Validate data presence against required fields or format constraints."""
    if identifier is not None:
        clean_id = str(identifier).strip()
        is_valid = bool(clean_id and len(clean_id) >= 3)
        return {"is_valid": is_valid, "identifier": clean_id}

    req_fields = required_fields or required or []
    target_data = attributes or inputs or data

    if isinstance(target_data, dict):
        missing = [f for f in req_fields if f not in target_data or target_data[f] is None or target_data[f] == ""]
        return {
            "is_valid": len(missing) == 0,
            "has_required": len(missing) == 0,
            "missing_fields": missing,
        }

    if isinstance(target_data, list):
        valid_rows = []
        invalid_rows = []
        for r in target_data:
            missing = [f for f in req_fields if f not in r or r[f] is None or str(r[f]).strip() == ""]
            if missing:
                invalid_rows.append({"row": r, "missing": missing})
            else:
                valid_rows.append(r)
        return {
            "is_valid": len(invalid_rows) == 0,
            "valid_count": len(valid_rows),
            "invalid_count": len(invalid_rows),
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows,
        }

    return {"is_valid": True}


def format_output(
    data: Any = None,
    format: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Format intermediate tool outputs into a final structured dictionary."""
    output = {"format": format or "standard"}
    if data is not None:
        output["data"] = data
    for k, v in kwargs.items():
        output[k] = v
    return output


_llm_provider: Optional[Callable[..., Any]] = None


def set_llm_provider(provider: Optional[Callable[..., Any]]) -> None:
    """Set custom LLM provider callable for llm_generate tool."""
    global _llm_provider
    _llm_provider = provider


def _deterministic_llm_generate(
    task: str,
    context: Any = None,
    **kwargs,
) -> Any:
    """Deterministic heuristic generator used when offline or as final safety fallback."""
    task_clean = task.lower()

    if "long_description" in task_clean or "product_description" in task_clean:
        name = (context or {}).get("name", "Product") if isinstance(context, dict) else "Product"
        return f"High-quality {name} designed for superior performance, comfort, and durability."

    if "short_description" in task_clean:
        return "Compact, durable, and premium quality."

    if "seo_title" in task_clean:
        name = (context or {}).get("name", "Item") if isinstance(context, dict) else "Item"
        return f"{name} - Best Deals & Fast Shipping"

    if "meta_description" in task_clean:
        return "Discover our top-rated collection with exceptional features and warranty."

    if "classify" in task_clean:
        keywords = kwargs.get("keywords") or []
        classified = []
        for kw in keywords:
            kw_text = kw.get("keyword", "") if isinstance(kw, dict) else str(kw)
            intent = "informational"
            if any(w in kw_text.lower() for w in ("buy", "order", "price", "discount")):
                intent = "transactional"
            elif any(w in kw_text.lower() for w in ("best", "review", "vs", "compare")):
                intent = "commercial"
            classified.append({"keyword": kw_text, "intent": intent})
        return classified

    if "campaign" in task_clean:
        return {
            "objective": kwargs.get("goal") or "Product Launch",
            "channels": ["Email", "Social Media", "Paid Search"],
            "status": "DRAFT",
        }

    if "parse_task" in task_clean:
        desc = kwargs.get("description", "")
        return {
            "required_skills": "python" if "python" in desc.lower() else "general",
            "priority": "HIGH" if "urgent" in desc.lower() else "NORMAL",
        }

    return {"status": "GENERATED", "task": task, "content": f"Generated output for {task}"}


def llm_generate(
    task: str,
    context: Any = None,
    prompt_template: Optional[str] = None,
    live: bool = False,
    **kwargs,
) -> Any:
    """Generic LLM generation interface decoupled from specific agent or executor implementations.
    
    Supports Gemini as primary generator, with automatic Claude fallback if Gemini fails.
    Defaults to fast deterministic generation for offline test safety unless live=True or ENABLE_LIVE_LLM=1.
    """
    if _llm_provider is not None:
        return _llm_provider(task=task, context=context, prompt_template=prompt_template, **kwargs)

    enable_live = live or os.getenv("ENABLE_LIVE_LLM", "false").lower() in ("true", "1", "yes")

    if enable_live:
        try:
            from app.agent.llm_fallback import generate_with_fallback
            prompt = (
                f"Task: {task}\n"
                f"Context: {json.dumps(context, default=str) if context else 'None'}\n"
                f"Parameters: {json.dumps(kwargs, default=str)}"
            )
            llm_res = generate_with_fallback(
                prompt=prompt,
                deterministic_fallback_fn=lambda: json.dumps(
                    _deterministic_llm_generate(task=task, context=context, **kwargs)
                ),
            )
            if llm_res.provider != "deterministic" and llm_res.text:
                clean_text = llm_res.text.strip()
                # Check if JSON output is expected and parseable
                if any(k in task.lower() for k in ("classify", "parse", "campaign")):
                    match = re.search(r"(\[.*\]|\{.*\})", clean_text, re.DOTALL)
                    if match:
                        try:
                            return json.loads(match.group(1))
                        except Exception:
                            pass
                return clean_text
        except Exception:
            pass

    return _deterministic_llm_generate(task=task, context=context, **kwargs)


def llm_generate_with_fallback(
    task: str,
    context: Any = None,
    prompt_template: Optional[str] = None,
    **kwargs,
) -> Any:
    """Explicitly invoke LLM generation with live Gemini primary -> Claude fallback."""
    return llm_generate(
        task=task,
        context=context,
        prompt_template=prompt_template,
        live=True,
        **kwargs,
    )
