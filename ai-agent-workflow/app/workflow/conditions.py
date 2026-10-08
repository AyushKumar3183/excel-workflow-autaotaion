"""Deterministic, safe Condition Engine for workflow execution.

Evaluates structured conditions against the execution context without ever using eval(),
exec(), or arbitrary code execution. Determines next actions (CONTINUE, SKIP, STOP,
ASK_USER, ESCALATE).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from app.workflow.models import Condition


class ConditionAction(str, Enum):
    """Actions the workflow executor can take based on condition outcomes."""
    CONTINUE = "CONTINUE"
    SKIP = "SKIP"
    STOP = "STOP"
    ASK_USER = "ASK_USER"
    ESCALATE = "ESCALATE"


# Normalization mapping for action aliases
ACTION_ALIASES: Dict[str, ConditionAction] = {
    "CONTINUE": ConditionAction.CONTINUE,
    "SKIP": ConditionAction.SKIP,
    "STOP": ConditionAction.STOP,
    "ASK_USER": ConditionAction.ASK_USER,
    "NEEDS_INPUT": ConditionAction.ASK_USER,  # Canonical equivalent
    "ESCALATE": ConditionAction.ESCALATE,
}

SUPPORTED_OPERATORS = {
    "==",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "exists",
    "not_exists",
    "is_empty",
    "is_not_empty",
}


class ConditionError(Exception):
    """Base exception for condition evaluation errors."""


class MissingVariableError(ConditionError):
    """Raised when a condition references a variable missing from execution context."""


class InvalidOperatorError(ConditionError, ValueError):
    """Raised when an unrecognized or unsupported operator is provided."""


class ConditionEvaluationError(ConditionError):
    """Raised when a condition comparison cannot be performed (e.g. type mismatch)."""


class ConditionResult(BaseModel):
    """Outcome of evaluating a condition against context."""
    passed: bool = Field(..., description="Whether the condition evaluated to True or False")
    action: str = Field(..., description="Action to take: CONTINUE, SKIP, STOP, ASK_USER, ESCALATE")
    field: Optional[str] = Field(default=None, description="Primary field evaluated")
    operator: Optional[str] = Field(default=None, description="Operator used")
    details: Optional[str] = Field(default=None, description="Diagnostic explanation")


def _resolve_context_path(context: Dict[str, Any], path: str) -> Tuple[bool, Any]:
    """Resolve dotted path (e.g., 'order.status' or 'items[0].sku') from context.
    
    Returns:
        (found: bool, value: Any)
    """
    if not isinstance(context, dict):
        return False, None

    # Direct key lookup first
    if path in context:
        return True, context[path]

    # Split by dot notation, supporting array indices like items[0]
    segments = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    current: Any = context

    for seg in segments:
        if seg.startswith("[") and seg.endswith("]"):
            try:
                idx = int(seg[1:-1])
                if isinstance(current, (list, tuple)) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return False, None
            except ValueError:
                return False, None
        else:
            if isinstance(current, dict) and seg in current:
                current = current[seg]
            else:
                return False, None

    return True, current


def _resolve_target_value(context: Dict[str, Any], target_val: Any) -> Any:
    """Resolve target comparison value, substituting from context if it is a context reference."""
    if isinstance(target_val, str):
        # Explicit reference with $ prefix
        if target_val.startswith("$"):
            var_name = target_val[1:]
            found, val = _resolve_context_path(context, var_name)
            if found:
                return val
            raise MissingVariableError(f"Referenced variable '{target_val}' not found in execution context.")

        # If value string matches an existing key in context, resolve it
        if target_val in context:
            return context[target_val]

    return target_val


def _evaluate_single_operator(left: Any, op: str, right: Any) -> bool:
    """Perform deterministic binary comparison without eval()."""
    clean_op = op.strip().lower()

    if clean_op == "is_empty":
        return left is None or left == "" or (isinstance(left, (list, dict, set, tuple)) and len(left) == 0)

    if clean_op == "is_not_empty":
        return not (left is None or left == "" or (isinstance(left, (list, dict, set, tuple)) and len(left) == 0))

    if clean_op in ("==", "="):
        # Numeric equality
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return left == right
        # Boolean equality
        if isinstance(left, bool) or isinstance(right, bool):
            return bool(left) is bool(right)
        return str(left) == str(right) if left is not None and right is not None else left == right

    if clean_op == "!=":
        return not _evaluate_single_operator(left, "==", right)

    if clean_op in ("<", "<=", ">", ">="):
        try:
            num_left = float(left)
            num_right = float(right)
        except (ValueError, TypeError) as e:
            raise ConditionEvaluationError(
                f"Numeric operator '{op}' requires numeric operands, got left={left!r} ({type(left).__name__}) "
                f"and right={right!r} ({type(right).__name__})."
            ) from e

        if clean_op == "<":
            return num_left < num_right
        if clean_op == "<=":
            return num_left <= num_right
        if clean_op == ">":
            return num_left > num_right
        if clean_op == ">=":
            return num_left >= num_right

    if clean_op == "contains":
        if left is None:
            return False
        if isinstance(left, (list, set, tuple)):
            return right in left
        if isinstance(left, dict):
            return right in left
        # Substring search
        return str(right).lower() in str(left).lower()

    if clean_op == "not_contains":
        return not _evaluate_single_operator(left, "contains", right)

    if clean_op == "in":
        if right is None:
            return False
        if isinstance(right, (list, set, tuple, dict)):
            return left in right
        return str(left).lower() in str(right).lower()

    if clean_op == "not_in":
        return not _evaluate_single_operator(left, "in", right)

    raise InvalidOperatorError(f"Unsupported operator: '{op}'")


class ConditionEngine:
    """Workflow-agnostic condition evaluator."""

    @classmethod
    def evaluate(
        cls,
        condition: Union[Condition, Dict[str, Any]],
        context: Dict[str, Any],
    ) -> ConditionResult:
        """Evaluate a condition against the given execution context dictionary.
        
        Args:
            condition: Condition Pydantic model or equivalent dictionary.
            context: Execution context variables.
            
        Returns:
            ConditionResult containing passed status and next action.
        """
        if not isinstance(context, dict):
            raise ConditionEvaluationError(f"Execution context must be a dictionary, got {type(context).__name__}")

        if not isinstance(condition, Condition):
            # Check for invalid operator before parsing to raise controlled InvalidOperatorError
            if isinstance(condition, dict) and "operator" in condition:
                op = condition.get("operator")
                if op is not None and str(op).strip().lower() not in SUPPORTED_OPERATORS:
                    raise InvalidOperatorError(
                        f"Operator '{op}' is not supported. Supported: {sorted(SUPPORTED_OPERATORS)}"
                    )
            try:
                cond_obj = Condition.from_raw(condition)
            except Exception as e:
                raise ConditionEvaluationError(f"Invalid condition definition: {e}") from e
        else:
            cond_obj = condition

        if cond_obj is None:
            return ConditionResult(passed=True, action=ConditionAction.CONTINUE.value, details="No condition provided")

        # 1. Handle Composite AND conditions
        if cond_obj.and_conditions:
            all_passed = True
            sub_details = []
            for sub_cond in cond_obj.and_conditions:
                sub_res = cls.evaluate(sub_cond, context)
                sub_details.append(f"({sub_res.field or 'sub'} {sub_res.operator or ''} -> {sub_res.passed})")
                if not sub_res.passed:
                    all_passed = False
                    break
            
            chosen_action = (
                cond_obj.action_on_true if all_passed else cond_obj.action_on_false
            )
            canonical_action = ACTION_ALIASES.get(chosen_action.upper(), ConditionAction.CONTINUE)
            return ConditionResult(
                passed=all_passed,
                action=canonical_action.value,
                details=f"AND({', '.join(sub_details)})",
            )

        # 2. Handle Composite OR conditions
        if cond_obj.or_conditions:
            any_passed = False
            sub_details = []
            for sub_cond in cond_obj.or_conditions:
                sub_res = cls.evaluate(sub_cond, context)
                sub_details.append(f"({sub_res.field or 'sub'} {sub_res.operator or ''} -> {sub_res.passed})")
                if sub_res.passed:
                    any_passed = True
                    break

            chosen_action = (
                cond_obj.action_on_true if any_passed else cond_obj.action_on_false
            )
            canonical_action = ACTION_ALIASES.get(chosen_action.upper(), ConditionAction.CONTINUE)
            return ConditionResult(
                passed=any_passed,
                action=canonical_action.value,
                details=f"OR({', '.join(sub_details)})",
            )

        # 3. Handle Single Condition
        field_name = cond_obj.field
        op_name = cond_obj.operator

        if not field_name:
            raise ConditionEvaluationError("Condition is missing a 'field' to evaluate.")
        if not op_name:
            raise ConditionEvaluationError("Condition is missing an 'operator'.")

        clean_op = op_name.strip().lower()
        if clean_op not in SUPPORTED_OPERATORS:
            raise InvalidOperatorError(
                f"Operator '{op_name}' is not supported. Supported: {sorted(SUPPORTED_OPERATORS)}"
            )

        # Resolve field in context
        found, left_val = _resolve_context_path(context, field_name)

        # Special handling for existence operators
        if clean_op == "exists":
            passed = found
        elif clean_op == "not_exists":
            passed = not found
        else:
            # Per Requirement 5: Missing context variable must produce a controlled condition error
            if not found:
                raise MissingVariableError(
                    f"Required context variable '{field_name}' not found in execution context. "
                    f"Available keys: {sorted(context.keys())}"
                )

            # Resolve comparison target
            right_val = _resolve_target_value(context, cond_obj.value)
            passed = _evaluate_single_operator(left_val, clean_op, right_val)

        chosen_action = cond_obj.action_on_true if passed else cond_obj.action_on_false
        canonical_action = ACTION_ALIASES.get(chosen_action.upper(), ConditionAction.CONTINUE)

        return ConditionResult(
            passed=passed,
            action=canonical_action.value,
            field=field_name,
            operator=clean_op,
            details=f"{field_name} {clean_op} {cond_obj.value!r} evaluated to {passed}",
        )


def evaluate_condition(
    condition: Union[Condition, Dict[str, Any]],
    context: Dict[str, Any],
) -> ConditionResult:
    """Evaluate condition against execution context using ConditionEngine."""
    return ConditionEngine.evaluate(condition, context)


# Direct alias for evaluate(condition, context) per requirement
evaluate = evaluate_condition


def evaluate_action(
    condition: Union[Condition, Dict[str, Any]],
    context: Dict[str, Any],
) -> str:
    """Evaluate condition and return just the resulting action string."""
    result = evaluate_condition(condition, context)
    return result.action
