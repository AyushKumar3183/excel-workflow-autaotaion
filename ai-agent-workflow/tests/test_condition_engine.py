"""Unit tests for the condition engine: operators, actions, context lookup, and composite logic.

Tests cover:
- All required comparison operators: ==, !=, >, >=, <, <=, contains, not_contains, in, not_in, exists, not_exists
- Missing context variable controlled error
- Invalid operator rejection
- Boolean composite conditions: AND, OR
- All actions: CONTINUE, SKIP, STOP, ASK_USER, ESCALATE
- Action alias mapping (NEEDS_INPUT -> ASK_USER)
- Dotted context path resolution and context-to-context comparisons
- Numerical and boundary conditions
"""

import pytest

from app.workflow.models import Condition
from app.workflow.conditions import (
    ConditionAction,
    ConditionEngine,
    ConditionResult,
    InvalidOperatorError,
    MissingVariableError,
    evaluate,
    evaluate_action,
    evaluate_condition,
)


# ---------------------------------------------------------------------------
# Comparison Operator Tests
# ---------------------------------------------------------------------------

def test_operator_equality():
    """Test == operator with integers, floats, strings, and booleans."""
    context = {"int_val": 42, "float_val": 42.0, "str_val": "active", "bool_val": True}

    # Int == Float
    assert evaluate({"field": "int_val", "operator": "==", "value": 42.0}, context).passed is True
    # String equality
    assert evaluate({"field": "str_val", "operator": "==", "value": "active"}, context).passed is True
    assert evaluate({"field": "str_val", "operator": "==", "value": "inactive"}, context).passed is False
    # Boolean equality
    assert evaluate({"field": "bool_val", "operator": "==", "value": True}, context).passed is True
    assert evaluate({"field": "bool_val", "operator": "==", "value": False}, context).passed is False


def test_operator_inequality():
    """Test != operator."""
    context = {"status": "pending", "count": 10}
    assert evaluate({"field": "status", "operator": "!=", "value": "completed"}, context).passed is True
    assert evaluate({"field": "status", "operator": "!=", "value": "pending"}, context).passed is False
    assert evaluate({"field": "count", "operator": "!=", "value": 10}, context).passed is False
    assert evaluate({"field": "count", "operator": "!=", "value": 20}, context).passed is True


def test_operator_greater_than():
    """Test > operator."""
    context = {"stock": 15, "limit": 10}
    assert evaluate({"field": "stock", "operator": ">", "value": 10}, context).passed is True
    assert evaluate({"field": "stock", "operator": ">", "value": 15}, context).passed is False
    assert evaluate({"field": "stock", "operator": ">", "value": 20}, context).passed is False


def test_operator_greater_than_or_equal_boundary():
    """Test >= operator including boundary cases."""
    context = {"price": 100.0}
    # Boundary: price == 100.0
    assert evaluate({"field": "price", "operator": ">=", "value": 100.0}, context).passed is True
    # Below boundary
    assert evaluate({"field": "price", "operator": ">=", "value": 99.9}, context).passed is True
    # Above boundary
    assert evaluate({"field": "price", "operator": ">=", "value": 100.1}, context).passed is False


def test_operator_less_than():
    """Test < operator."""
    context = {"stock": 5}
    assert evaluate({"field": "stock", "operator": "<", "value": 10}, context).passed is True
    assert evaluate({"field": "stock", "operator": "<", "value": 5}, context).passed is False
    assert evaluate({"field": "stock", "operator": "<", "value": 2}, context).passed is False


def test_operator_less_than_or_equal_boundary():
    """Test <= operator including exact boundary."""
    context = {"stock": 10}
    # Exact boundary
    assert evaluate({"field": "stock", "operator": "<=", "value": 10}, context).passed is True
    # Below boundary
    assert evaluate({"field": "stock", "operator": "<=", "value": 11}, context).passed is True
    # Above boundary
    assert evaluate({"field": "stock", "operator": "<=", "value": 9}, context).passed is False


def test_operator_contains_and_not_contains():
    """Test contains and not_contains with strings and lists."""
    context = {
        "tags": ["electronics", "audio", "wireless"],
        "description": "Ergonomic wireless keyboard with backlit keys",
    }

    # List contains
    assert evaluate({"field": "tags", "operator": "contains", "value": "audio"}, context).passed is True
    assert evaluate({"field": "tags", "operator": "contains", "value": "apparel"}, context).passed is False

    # List not_contains
    assert evaluate({"field": "tags", "operator": "not_contains", "value": "apparel"}, context).passed is True
    assert evaluate({"field": "tags", "operator": "not_contains", "value": "audio"}, context).passed is False

    # String contains (case-insensitive substring)
    assert evaluate({"field": "description", "operator": "contains", "value": "wireless"}, context).passed is True
    assert evaluate({"field": "description", "operator": "contains", "value": "bluetooth"}, context).passed is False

    # String not_contains
    assert evaluate({"field": "description", "operator": "not_contains", "value": "bluetooth"}, context).passed is True
    assert evaluate({"field": "description", "operator": "not_contains", "value": "ergonomic"}, context).passed is False


def test_operator_in_and_not_in():
    """Test in and not_in operators."""
    context = {
        "selected_role": "admin",
        "allowed_roles": ["admin", "manager", "auditor"],
    }
    assert evaluate({"field": "selected_role", "operator": "in", "value": ["admin", "editor"]}, context).passed is True
    assert evaluate({"field": "selected_role", "operator": "in", "value": ["guest", "viewer"]}, context).passed is False

    assert evaluate({"field": "selected_role", "operator": "not_in", "value": ["guest", "viewer"]}, context).passed is True
    assert evaluate({"field": "selected_role", "operator": "not_in", "value": ["admin", "editor"]}, context).passed is False


def test_operator_exists_and_not_exists():
    """Test exists and not_exists operators without raising MissingVariableError."""
    context = {"existing_key": 123, "empty_str": ""}

    # Exists
    assert evaluate({"field": "existing_key", "operator": "exists"}, context).passed is True
    assert evaluate({"field": "missing_key", "operator": "exists"}, context).passed is False

    # Not Exists
    assert evaluate({"field": "missing_key", "operator": "not_exists"}, context).passed is True
    assert evaluate({"field": "existing_key", "operator": "not_exists"}, context).passed is False


# ---------------------------------------------------------------------------
# Missing Variable & Invalid Operator Error Handling
# ---------------------------------------------------------------------------

def test_missing_variable_raises_controlled_error():
    """Test that referencing a missing variable raises MissingVariableError for standard operators."""
    context = {"stock": 10}

    with pytest.raises(MissingVariableError, match="Required context variable 'undefined_metric' not found"):
        evaluate({"field": "undefined_metric", "operator": "<", "value": 5}, context)


def test_invalid_operator_rejected():
    """Test that an invalid or unsupported operator raises InvalidOperatorError."""
    context = {"x": 10}

    # Direct evaluate with dict
    with pytest.raises(InvalidOperatorError, match="Operator 'regex_match' is not supported"):
        evaluate({"field": "x", "operator": "regex_match", "value": "10"}, context)


# ---------------------------------------------------------------------------
# Boolean Composite Conditions (AND / OR)
# ---------------------------------------------------------------------------

def test_composite_and_condition():
    """Test structured AND condition combining multiple checks."""
    context = {"stock": 5, "min_stock": 10, "is_active": True}

    # Both conditions pass
    cond_both_pass = {
        "and": [
            {"field": "stock", "operator": "<", "value": 10},
            {"field": "is_active", "operator": "==", "value": True},
        ],
        "action_on_true": "STOP",
        "action_on_false": "CONTINUE",
    }
    res1 = evaluate(cond_both_pass, context)
    assert res1.passed is True
    assert res1.action == "STOP"

    # One condition fails
    cond_one_fails = {
        "and": [
            {"field": "stock", "operator": "<", "value": 10},
            {"field": "is_active", "operator": "==", "value": False},
        ],
        "action_on_true": "STOP",
        "action_on_false": "CONTINUE",
    }
    res2 = evaluate(cond_one_fails, context)
    assert res2.passed is False
    assert res2.action == "CONTINUE"


def test_composite_or_condition():
    """Test structured OR condition combining multiple checks."""
    context = {"failure_rate": 0.15, "avg_duration_ms": 3000}

    # One condition passes (failure_rate > 0.10)
    cond_or = {
        "or": [
            {"field": "failure_rate", "operator": ">", "value": 0.10},
            {"field": "avg_duration_ms", "operator": ">", "value": 5000},
        ],
        "action_on_true": "ESCALATE",
        "action_on_false": "CONTINUE",
    }
    res = evaluate(cond_or, context)
    assert res.passed is True
    assert res.action == "ESCALATE"

    # Both fail
    cond_both_fail = {
        "or": [
            {"field": "failure_rate", "operator": ">", "value": 0.50},
            {"field": "avg_duration_ms", "operator": ">", "value": 5000},
        ],
        "action_on_true": "ESCALATE",
        "action_on_false": "CONTINUE",
    }
    res_fail = evaluate(cond_both_fail, context)
    assert res_fail.passed is False
    assert res_fail.action == "CONTINUE"


# ---------------------------------------------------------------------------
# Action Mapping Tests
# ---------------------------------------------------------------------------

def test_all_condition_actions():
    """Test that all required actions (CONTINUE, SKIP, STOP, ASK_USER, ESCALATE) are returned."""
    context = {"status": "ready"}

    # CONTINUE
    res = evaluate({"field": "status", "operator": "==", "value": "ready", "action_on_true": "CONTINUE"}, context)
    assert res.action == ConditionAction.CONTINUE.value

    # SKIP
    res = evaluate({"field": "status", "operator": "==", "value": "ready", "action_on_true": "SKIP"}, context)
    assert res.action == ConditionAction.SKIP.value

    # STOP
    res = evaluate({"field": "status", "operator": "==", "value": "ready", "action_on_true": "STOP"}, context)
    assert res.action == ConditionAction.STOP.value

    # ASK_USER
    res = evaluate({"field": "status", "operator": "==", "value": "ready", "action_on_true": "ASK_USER"}, context)
    assert res.action == ConditionAction.ASK_USER.value

    # ESCALATE
    res = evaluate({"field": "status", "operator": "==", "value": "ready", "action_on_true": "ESCALATE"}, context)
    assert res.action == ConditionAction.ESCALATE.value


def test_needs_input_alias_maps_to_ask_user():
    """Test that NEEDS_INPUT action alias normalizes to canonical ASK_USER."""
    context = {"missing_fields": ["campaign_dates"]}
    res = evaluate(
        {"field": "missing_fields", "operator": "is_not_empty", "action_on_true": "NEEDS_INPUT"},
        context,
    )
    assert res.action == ConditionAction.ASK_USER.value


# ---------------------------------------------------------------------------
# Dotted Context Paths & Context Value References
# ---------------------------------------------------------------------------

def test_nested_context_dot_and_bracket_notation():
    """Test accessing nested dictionary properties and array elements."""
    context = {
        "order": {
            "id": "ORD-1001",
            "status": "Delivered",
            "items": [{"sku": "SKU-A", "qty": 2}, {"sku": "SKU-B", "qty": 1}],
        }
    }

    # Nested dot notation
    res1 = evaluate({"field": "order.status", "operator": "==", "value": "Delivered"}, context)
    assert res1.passed is True

    # Array indexing
    res2 = evaluate({"field": "order.items[0].sku", "operator": "==", "value": "SKU-A"}, context)
    assert res2.passed is True


def test_context_to_context_variable_comparison():
    """Test comparing two variables within context (e.g. stock < minimum_stock)."""
    # Pattern shown in README: {"field": "stock", "operator": "<", "value": "minimum_stock"}
    context = {"stock": 4, "minimum_stock": 10}

    res1 = evaluate({"field": "stock", "operator": "<", "value": "minimum_stock"}, context)
    assert res1.passed is True

    # Explicit $ prefix reference
    res2 = evaluate({"field": "stock", "operator": "<", "value": "$minimum_stock"}, context)
    assert res2.passed is True


def test_evaluate_action_helper():
    """Test evaluate_action helper directly returns the action string."""
    context = {"score": 95}
    action = evaluate_action({"field": "score", "operator": ">=", "value": 90, "action_on_true": "CONTINUE"}, context)
    assert action == "CONTINUE"
