"""Generic Pydantic models for the workflow definition layer.

These models represent workflow structure, inputs, steps, bindings, and conditions
without embedding any workflow-specific or business-specific logic.
"""

from __future__ import annotations

from enum import Enum
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


VALID_CONDITION_OPERATORS = {
    "==", "!=", ">", ">=", "<", "<=",
    "contains", "not_contains", "in", "not_in",
    "exists", "not_exists", "is_empty", "is_not_empty",
}

VALID_CONDITION_ACTIONS = {
    "CONTINUE", "SKIP", "STOP", "ASK_USER", "NEEDS_INPUT", "ESCALATE"
}


class Condition(BaseModel):
    """Structured condition representation for workflow steps.
    
    Evaluated by the generic condition engine without using python eval().
    Supports both single field-operator checks and composite AND / OR trees.
    """
    field: Optional[str] = Field(default=None, description="Field or context key to evaluate")
    operator: Optional[str] = Field(default=None, description="Operator for comparison")
    value: Any = Field(default=None, description="Target value or threshold to compare against")
    and_conditions: Optional[List[Condition]] = Field(default=None, alias="and", description="List of AND conditions")
    or_conditions: Optional[List[Condition]] = Field(default=None, alias="or", description="List of OR conditions")
    action_on_true: str = Field(default="CONTINUE", description="Action when condition is True: CONTINUE, SKIP, STOP, ASK_USER, ESCALATE")
    action_on_false: str = Field(default="CONTINUE", description="Action when condition is False: CONTINUE, SKIP, STOP, ASK_USER, ESCALATE")

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean_op = v.strip().lower()
        canonical = clean_op if clean_op in VALID_CONDITION_OPERATORS else v.strip()
        if canonical not in VALID_CONDITION_OPERATORS:
            raise ValueError(
                f"Invalid condition operator: '{v}'. Supported operators: {sorted(VALID_CONDITION_OPERATORS)}"
            )
        return canonical

    @field_validator("action_on_true", "action_on_false")
    @classmethod
    def validate_action(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in VALID_CONDITION_ACTIONS:
            raise ValueError(
                f"Invalid condition action: '{v}'. Must be one of: {sorted(VALID_CONDITION_ACTIONS)}"
            )
        return clean

    @model_validator(mode="after")
    def validate_structure(self) -> Condition:
        is_composite = bool(self.and_conditions or self.or_conditions)
        if not is_composite:
            if not self.field:
                raise ValueError("Single condition must define a 'field' to evaluate.")
            if not self.operator:
                raise ValueError("Single condition must define an 'operator'.")
        return self

    @classmethod
    def from_raw(cls, raw: Any) -> Optional[Condition]:
        """Parse condition from string, dict, or existing Condition instance."""
        if raw is None or raw == "":
            return None
        if isinstance(raw, Condition):
            return raw
        if isinstance(raw, str):
            text = raw.strip()
            if not text or text.lower() in {"none", "null"}:
                return None
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return cls(**data)
            except Exception as e:
                raise ValueError(f"Failed to parse condition JSON: {text}. Error: {e}") from e
        if isinstance(raw, dict):
            return cls(**raw)
        raise ValueError(f"Unsupported condition format: {type(raw).__name__}")


class InputSourceType(str, Enum):
    """Classification of parameter input origin."""
    REPOSITORY = "repository"
    USER = "user"
    DEFAULT = "default"
    OPTIONAL = "optional"


class DataSourceMetadata(BaseModel):
    """Metadata describing a data source or input parameter binding."""
    name: str = Field(..., description="Parameter name as referenced in step bindings")
    source_type: InputSourceType = Field(default=InputSourceType.USER, description="Input origin type")
    data_source: Optional[str] = Field(default=None, description="Repository file path or source identifier")
    default_value: Optional[Any] = Field(default=None, description="Default value if not supplied")
    required: bool = Field(default=True, description="Whether this input is mandatory")
    description: Optional[str] = Field(default=None, description="Human-readable description")


class WorkflowInput(BaseModel):
    """Specification of an expected input parameter for a workflow."""
    name: str = Field(..., description="Name of the input variable")
    type: str = Field(default="string", description="Expected type: string, int, float, bool, file, list, dict")
    required: bool = Field(default=True, description="Whether this input is mandatory")
    description: Optional[str] = Field(default=None, description="Human-readable description")
    default: Optional[Any] = Field(default=None, description="Default value if not provided")
    source_type: str = Field(default="user", description="Input origin: repository, user, default, optional")
    data_source: Optional[str] = Field(default=None, description="Repository data source path if applicable")


class StepBinding(BaseModel):
    """Executable binding mapping a workflow step to a tool and its parameters."""
    workflow_id: str = Field(..., description="Workflow ID, e.g. WF001")
    step_no: int = Field(..., ge=1, description="1-based step index")
    tool: str = Field(..., min_length=1, description="Registered tool name")
    params: Dict[str, Any] = Field(default_factory=dict, description="Mapped parameter definitions or templates")
    output_variable: str = Field(..., min_length=1, description="Context variable name for tool output")
    condition: Optional[Condition] = Field(default=None, description="Optional condition rule evaluated at this step")
    on_error: str = Field(default="FAIL", description="Error policy: FAIL, RETRY, SKIP, ESCALATE")

    @field_validator("on_error")
    @classmethod
    def validate_on_error(cls, v: str) -> str:
        valid_policies = {"FAIL", "RETRY", "SKIP", "ESCALATE"}
        clean = v.strip().upper()
        if clean not in valid_policies:
            raise ValueError(f"Invalid on_error policy: '{v}'. Must be one of: {sorted(valid_policies)}")
        return clean


class WorkflowStep(BaseModel):
    """Normalized executable step inside a WorkflowDefinition."""
    step_no: int = Field(..., ge=1, description="1-based step index")
    step_name: str = Field(..., min_length=1, description="Short descriptive name of the step")
    description: Optional[str] = Field(default=None, description="Detailed business description of the step")
    tool: str = Field(..., min_length=1, description="Name of the tool to execute")
    params: Dict[str, Any] = Field(default_factory=dict, description="Tool parameter mapping")
    output_variable: str = Field(..., min_length=1, description="Variable name to store output in context")
    condition: Optional[Condition] = Field(default=None, description="Optional condition evaluated before/after step")
    on_error: str = Field(default="FAIL", description="Error policy: FAIL, RETRY, SKIP, ESCALATE")


class WorkflowDefinition(BaseModel):
    """Complete, normalized, validated workflow definition loaded from data."""
    workflow_id: str = Field(..., min_length=2, description="Unique workflow identifier, e.g. WF001")
    name: str = Field(..., min_length=1, description="Human-readable workflow name")
    trigger: str = Field(..., min_length=1, description="Natural-language trigger description")
    inputs: List[WorkflowInput] = Field(default_factory=list, description="Declared workflow inputs")
    steps: List[WorkflowStep] = Field(default_factory=list, description="Ordered executable steps")
    data_sources: Dict[str, DataSourceMetadata] = Field(default_factory=dict, description="Configured data sources and input metadata")
    decision_logic: Optional[str] = Field(default=None, description="Business decision logic text")
    tools_required: List[str] = Field(default_factory=list, description="Declared tools from catalog")
    expected_output: Optional[str] = Field(default=None, description="Description of expected output")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional properties or raw source data")

    @field_validator("steps")
    @classmethod
    def validate_step_sequence(cls, steps: List[WorkflowStep]) -> List[WorkflowStep]:
        if not steps:
            raise ValueError("Workflow definition must have at least one step.")
        sorted_steps = sorted(steps, key=lambda s: s.step_no)
        for expected_idx, step in enumerate(sorted_steps, start=1):
            if step.step_no != expected_idx:
                raise ValueError(
                    f"Steps must be contiguous and start at 1. Expected step_no {expected_idx}, got {step.step_no}."
                )
        return sorted_steps
