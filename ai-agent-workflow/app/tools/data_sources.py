"""Simulated external business data sources.

Provides domain data loaders and database query simulations reading from the data/
directory. Decoupled from generic computation primitives.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Default data directory relative to project root
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _resolve_data_path(filename: str, data_dir: Optional[Union[str, Path]] = None) -> Path:
    base = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    return base / filename


def load_inventory(data_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load current product inventory from inventory.csv."""
    path = _resolve_data_path("inventory.csv", data_dir)
    if not path.exists():
        raise FileNotFoundError(f"Inventory data file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records: List[Dict[str, Any]] = []
        for r in reader:
            records.append({
                "sku": r.get("sku", "").strip(),
                "product_name": r.get("product_name", "").strip(),
                "category": r.get("category", "").strip(),
                "current_stock": int(r.get("current_stock") or 0),
                "minimum_stock": int(r.get("minimum_stock") or 0),
                "unit_price": float(r.get("unit_price") or 0.0),
            })
        return records


def load_product_catalog(data_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load internal product catalog with selling prices from product_catalog.csv."""
    path = _resolve_data_path("product_catalog.csv", data_dir)
    if not path.exists():
        path = _resolve_data_path("inventory.csv", data_dir)
    if not path.exists():
        raise FileNotFoundError(f"Product catalog data file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records: List[Dict[str, Any]] = []
        for r in reader:
            records.append({
                "sku": r.get("sku", "").strip(),
                "product_name": r.get("product_name", "").strip(),
                "category": r.get("category", "").strip(),
                "unit_price": float(r.get("unit_price") or r.get("price") or r.get("our_price") or 0.0),
            })
        return records


def load_vendor_prices(data_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load latest vendor catalog prices from vendor_prices.csv."""
    path = _resolve_data_path("vendor_prices.csv", data_dir)
    if not path.exists():
        raise FileNotFoundError(f"Vendor prices data file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records: List[Dict[str, Any]] = []
        for r in reader:
            records.append({
                "sku": r.get("sku", "").strip(),
                "vendor_name": r.get("vendor_name", "").strip(),
                "vendor_price": float(r.get("vendor_price") or 0.0),
                "lead_time_days": int(r.get("lead_time_days") or 0),
            })
        return records


def load_orders(data_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load customer order records from orders.csv."""
    path = _resolve_data_path("orders.csv", data_dir)
    if not path.exists():
        raise FileNotFoundError(f"Orders data file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records: List[Dict[str, Any]] = []
        for r in reader:
            records.append({
                "order_id": r.get("order_id", "").strip(),
                "customer_email": r.get("customer_email", "").strip(),
                "order_date": r.get("order_date", "").strip(),
                "status": r.get("status", "").strip(),
                "total_amount": float(r.get("total_amount") or 0.0),
                "items": r.get("items", "").strip(),
                "shipment_id": r.get("shipment_id", "").strip() or None,
                "tracking_number": r.get("tracking_number", "").strip() or None,
            })
        return records


def load_employees(data_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load employee roster, skills, and capacities from employees.csv."""
    path = _resolve_data_path("employees.csv", data_dir)
    if not path.exists():
        raise FileNotFoundError(f"Employees data file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records: List[Dict[str, Any]] = []
        for r in reader:
            records.append({
                "employee_id": r.get("employee_id", "").strip(),
                "name": r.get("name", "").strip(),
                "email": r.get("email", "").strip(),
                "role": r.get("role", "").strip(),
                "skills": r.get("skills", "").strip(),
                "current_workload_hours": float(r.get("current_workload_hours") or 0.0),
                "max_capacity_hours": float(r.get("max_capacity_hours") or 40.0),
            })
        return records


def load_execution_logs(data_dir: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load historical workflow execution trace logs from workflow_logs.jsonl."""
    path = _resolve_data_path("workflow_logs.jsonl", data_dir)
    if not path.exists():
        return []

    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            clean = line.strip()
            if clean:
                records.append(json.loads(clean))
    return records


def query_database(
    source: str,
    identifier: Optional[str] = None,
    order_id: Optional[str] = None,
    query: Optional[str] = None,
    key: Optional[str] = None,
    table: Optional[str] = None,
    data_dir: Optional[Union[str, Path]] = None,
    **kwargs,
) -> Any:
    """Simulated database query router for external data sources.
    
    Routes lookup requests to simulated relational tables based on source identifier.
    """
    target_source = (source or table or "").lower().strip()
    search_term = str(identifier or order_id or query or key or "").strip().lower()

    if target_source in ("orders", "order"):
        orders = load_orders(data_dir=data_dir)
        for ord_record in orders:
            if (
                ord_record["order_id"].lower() == search_term
                or ord_record["customer_email"].lower() == search_term
            ):
                return ord_record
        return {}

    elif target_source in ("shipments", "shipment"):
        orders = load_orders(data_dir=data_dir)
        for ord_record in orders:
            if (
                ord_record.get("shipment_id") and ord_record["shipment_id"].lower() == search_term
            ) or (
                ord_record.get("order_id") and ord_record["order_id"].lower() == search_term
            ):
                return {
                    "shipment_id": ord_record.get("shipment_id"),
                    "order_id": ord_record.get("order_id"),
                    "tracking_number": ord_record.get("tracking_number"),
                    "status": "In Transit" if ord_record.get("status") == "Shipped" else ord_record.get("status"),
                }
        return {}

    elif target_source in ("inventory", "products"):
        inventory = load_inventory(data_dir=data_dir)
        for item in inventory:
            if item["sku"].lower() == search_term:
                return item
        return {}

    elif target_source in ("employees", "employee"):
        employees = load_employees(data_dir=data_dir)
        for emp in employees:
            if emp["employee_id"].lower() == search_term or emp["email"].lower() == search_term:
                return emp
        return {}

    return {}
