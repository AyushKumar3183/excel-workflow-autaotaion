"""Generic, workflow-agnostic Workflow Executor.

Executes any validated WorkflowDefinition through dynamic ToolRegistry resolution,
context propagation, and deterministic ConditionEngine evaluation.
"""

from __future__ import annotations

import re
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

from app.tools.registry import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterError,
    ToolRegistry,
    default_tool_registry,
)
from app.workflow.conditions import (
    ConditionAction,
    ConditionEngine,
    ConditionResult,
    MissingVariableError,
    _resolve_context_path,
)
from app.workflow.models import (
    Condition,
    StepBinding,
    WorkflowDefinition,
    WorkflowInput,
    WorkflowStep,
)
from app.workflow.registry import WorkflowRegistry, default_registry
from app.workflow.resolver import (
    GenericInputResolver,
    InputResolutionResult,
    find_param_in_dict,
)


# ---------------------------------------------------------------------------
# Execution Status Enums & Result Models
# ---------------------------------------------------------------------------

class ExecutionStatus(str, Enum):
    """Overall workflow run status."""
    SUCCESS = "SUCCESS"
    NEEDS_INPUT = "NEEDS_INPUT"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class StepExecutionStatus(str, Enum):
    """Individual workflow step status."""
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class StepResult(BaseModel):
    """Execution telemetry and outcome for a single workflow step."""
    step_no: int
    step_name: str
    tool: str
    status: StepExecutionStatus
    output: Any = None
    condition_result: Optional[ConditionResult] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    retries_attempted: int = 0


class WorkflowExecutionResult(BaseModel):
    """Complete, structured outcome of a workflow execution run."""
    run_id: str
    workflow_id: str
    status: ExecutionStatus
    final_output: Any = None
    step_results: List[StepResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    missing_inputs: List[str] = Field(default_factory=list)
    context_summary: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: Optional[float] = None


# ---------------------------------------------------------------------------
# Execution Context
# ---------------------------------------------------------------------------

class ExecutionContext:
    """Stateful, structured execution context tracking inputs, step outputs, and variables."""

    def __init__(
        self,
        workflow_id: str,
        run_id: Optional[str] = None,
        input_parameters: Optional[Dict[str, Any]] = None,
    ):
        self.workflow_id = workflow_id
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self.input_parameters: Dict[str, Any] = dict(input_parameters or {})
        self.step_outputs: Dict[str, Any] = {}
        self.current_step: int = 0
        self.status: ExecutionStatus = ExecutionStatus.SUCCESS
        self.errors: List[str] = []
        self.condition_results: List[ConditionResult] = []
        
        # Merged variable store containing inputs and step outputs
        self._variables: Dict[str, Any] = dict(self.input_parameters)

    def set(self, key: str, value: Any) -> None:
        """Store a step output variable in context."""
        self._variables[key] = value
        self.step_outputs[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a variable by key."""
        return self._variables.get(key, default)

    def get_path(self, path: str) -> Tuple[bool, Any]:
        """Resolve dotted or bracketed path (e.g. 'order.status' or 'items[0].sku')."""
        return _resolve_context_path(self._variables, path)

    def to_dict(self) -> Dict[str, Any]:
        """Return a flat dictionary representation for condition evaluation and summaries."""
        combined = dict(self._variables)
        combined["workflow_id"] = self.workflow_id
        combined["run_id"] = self.run_id
        combined["current_step"] = self.current_step
        return combined


# ---------------------------------------------------------------------------
# Parameter Resolution Helper
# ---------------------------------------------------------------------------

def find_param_in_dict(name: str, params: Dict[str, Any]) -> Tuple[bool, Any]:
    """Find parameter value supporting exact match, case-insensitivity, and common descriptor suffixes.
    
    Ensures generic compatibility between descriptive catalog input names (e.g. product_csv,
    vendor_price_list) and execution step variables (e.g. product_file, vendor_file).
    """
    if name in params and params[name] is not None and params[name] != "":
        return True, params[name]
    clean = name.lower()
    for k, v in params.items():
        if k.lower() == clean and v is not None and v != "":
            return True, v
    # Common suffix variants: _file, _csv, _list, _data
    clean_base = clean.replace("_file", "").replace("_csv", "").replace("_list", "").replace("_data", "")
    for k, v in params.items():
        k_base = k.lower().replace("_file", "").replace("_csv", "").replace("_list", "").replace("_data", "")
        if clean_base and k_base:
            if clean_base == k_base and v is not None and v != "":
                return True, v
            if (clean_base in k_base or k_base in clean_base) and v is not None and v != "":
                if ("product" in clean_base and "product" in k_base) or ("vendor" in clean_base and "vendor" in k_base):
                    return True, v
    return False, None


def _resolve_param_value(val: Any, context: ExecutionContext) -> Any:
    """Recursively resolve parameter references starting with '$' from execution context."""
    if isinstance(val, str):
        if val.startswith("$"):
            var_path = val[1:]
            found, resolved_val = context.get_path(var_path)
            if found:
                return resolved_val
            
            # Check direct input parameters or generic aliases
            found_param, val_param = find_param_in_dict(var_path, context.input_parameters)
            if found_param:
                return val_param

            raise MissingVariableError(
                f"Step parameter '{val}' could not be resolved from execution context. "
                f"Available keys: {sorted(context.to_dict().keys())}"
            )
        return val

    elif isinstance(val, dict):
        return {k: _resolve_param_value(v, context) for k, v in val.items()}

    elif isinstance(val, list):
        return [_resolve_param_value(item, context) for item in val]

    return val


def resolve_step_parameters(params: Dict[str, Any], context: ExecutionContext) -> Dict[str, Any]:
    """Resolve all parameters for a tool call against the current context."""
    resolved: Dict[str, Any] = {}
    for k, v in params.items():
        resolved[k] = _resolve_param_value(v, context)
    return resolved


# ---------------------------------------------------------------------------
# Generic Workflow Executor
# ---------------------------------------------------------------------------

class GenericWorkflowExecutor:
    """Deterministic, domain-agnostic workflow executor."""

    def __init__(
        self,
        registry: Optional[WorkflowRegistry] = None,
        tool_registry: Optional[ToolRegistry] = None,
        max_retries: int = 1,
        logger: Optional[Any] = None,
        resolver: Optional[GenericInputResolver] = None,
    ):
        self.registry = registry or default_registry
        self.tool_registry = tool_registry or default_tool_registry
        self.max_retries = max(0, max_retries)
        self.logger = logger
        self.resolver = resolver or GenericInputResolver()

    def execute(
        self,
        workflow_id: str,
        params: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> WorkflowExecutionResult:
        """Execute a workflow definition deterministically.
        
        Args:
            workflow_id: Identifier of the workflow to run (e.g., 'WF001').
            params: User-provided inputs dictionary.
            run_id: Optional unique execution identifier.
            
        Returns:
            WorkflowExecutionResult with final status, step details, and telemetry.
        """
        start_time = time.perf_counter()
        active_run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        user_params = dict(params or {})

        # 1. Locate WorkflowDefinition
        workflow = self.registry.get(workflow_id)
        if workflow is None:
            return WorkflowExecutionResult(
                run_id=active_run_id,
                workflow_id=workflow_id,
                status=ExecutionStatus.FAILED,
                errors=[f"Workflow '{workflow_id}' not found in registry."],
            )

        # 2. Schema-Driven Input & Data Source Resolution
        resolution = self.resolver.resolve(workflow, user_params)
        if resolution.status != ExecutionStatus.SUCCESS.value:
            missing_items = resolution.missing_user_inputs + resolution.missing_repo_files
            err_msgs = resolution.errors if resolution.errors else [f"Missing required input(s): {', '.join(missing_items)}"]
            status_enum = (
                ExecutionStatus.NEEDS_INPUT
                if resolution.status == ExecutionStatus.NEEDS_INPUT.value
                else ExecutionStatus.FAILED
            )
            return WorkflowExecutionResult(
                run_id=active_run_id,
                workflow_id=workflow_id,
                status=status_enum,
                missing_inputs=missing_items,
                errors=err_msgs,
            )

        # 3. Initialize ExecutionContext with resolved parameters
        context = ExecutionContext(
            workflow_id=workflow_id,
            run_id=active_run_id,
            input_parameters=resolution.resolved_params,
        )

        step_results: List[StepResult] = []
        final_output: Any = None
        overall_status = ExecutionStatus.SUCCESS
        errors: List[str] = []
        skip_remaining = False

        # 4. Step Execution Loop
        for step in workflow.steps:
            context.current_step = step.step_no

            # Handle skipped state
            if skip_remaining:
                step_results.append(
                    StepResult(
                        step_no=step.step_no,
                        step_name=step.step_name,
                        tool=step.tool,
                        status=StepExecutionStatus.SKIPPED,
                        output=None,
                        error=None,
                    )
                )
                continue

            # a. Resolve Parameters
            try:
                resolved_params = resolve_step_parameters(step.params, context)
            except MissingVariableError as mve:
                err_msg = f"Step {step.step_no} ('{step.step_name}') parameter resolution error: {mve}"
                errors.append(err_msg)
                step_results.append(
                    StepResult(
                        step_no=step.step_no,
                        step_name=step.step_name,
                        tool=step.tool,
                        status=StepExecutionStatus.FAILED,
                        error=err_msg,
                    )
                )
                overall_status = ExecutionStatus.FAILED
                break

            # b. Locate Tool in ToolRegistry
            if not self.tool_registry.exists(step.tool):
                err_msg = f"Step {step.step_no} tool '{step.tool}' not found in ToolRegistry."
                errors.append(err_msg)
                step_results.append(
                    StepResult(
                        step_no=step.step_no,
                        step_name=step.step_name,
                        tool=step.tool,
                        status=StepExecutionStatus.FAILED,
                        error=err_msg,
                    )
                )
                overall_status = ExecutionStatus.FAILED
                break

            # c. Execute Tool with controlled retry policy
            step_start = time.perf_counter()
            tool_output = None
            step_error = None
            retries_done = 0
            
            # Determine retry limit based on on_error policy
            max_attempts = (1 + self.max_retries) if step.on_error.upper() == "RETRY" else 1

            for attempt in range(max_attempts):
                try:
                    tool_output = self.tool_registry.execute(step.tool, resolved_params)
                    step_error = None
                    retries_done = attempt
                    break
                except ToolParameterError as tpe:
                    step_error = str(tpe)
                    retries_done = attempt
                    break
                except Exception as exc:
                    step_error = str(exc)
                    retries_done = attempt + 1
                    if attempt < max_attempts - 1:
                        time.sleep(0.01)

            step_duration_ms = round((time.perf_counter() - step_start) * 1000, 2)

            # d. Handle Tool Execution Failure
            if step_error is not None:
                err_msg = f"Step {step.step_no} execution failed: {step_error}"
                errors.append(err_msg)
                
                if step.on_error.upper() == "SKIP":
                    step_results.append(
                        StepResult(
                            step_no=step.step_no,
                            step_name=step.step_name,
                            tool=step.tool,
                            status=StepExecutionStatus.SKIPPED,
                            output=None,
                            error=step_error,
                            duration_ms=step_duration_ms,
                            retries_attempted=retries_done,
                        )
                    )
                    continue

                if step.on_error.upper() == "ESCALATE":
                    step_results.append(
                        StepResult(
                            step_no=step.step_no,
                            step_name=step.step_name,
                            tool=step.tool,
                            status=StepExecutionStatus.FAILED,
                            error=step_error,
                            duration_ms=step_duration_ms,
                            retries_attempted=retries_done,
                        )
                    )
                    overall_status = ExecutionStatus.ESCALATED
                    break

                # Default error action: FAIL
                step_results.append(
                    StepResult(
                        step_no=step.step_no,
                        step_name=step.step_name,
                        tool=step.tool,
                        status=StepExecutionStatus.FAILED,
                        error=step_error,
                        duration_ms=step_duration_ms,
                        retries_attempted=retries_done,
                    )
                )
                overall_status = ExecutionStatus.FAILED
                break

            # e. Tool Success: Store in Context
            context.set(step.output_variable, tool_output)
            final_output = tool_output

            # f. Evaluate Associated Condition
            cond_result: Optional[ConditionResult] = None
            if step.condition is not None:
                try:
                    eval_context = context.to_dict()
                    if isinstance(tool_output, dict):
                        eval_context.update(tool_output)

                    cond_result = ConditionEngine.evaluate(step.condition, eval_context)
                    context.condition_results.append(cond_result)
                except Exception as ce:
                    err_msg = f"Step {step.step_no} condition evaluation error: {ce}"
                    errors.append(err_msg)
                    step_results.append(
                        StepResult(
                            step_no=step.step_no,
                            step_name=step.step_name,
                            tool=step.tool,
                            status=StepExecutionStatus.FAILED,
                            output=tool_output,
                            error=err_msg,
                            duration_ms=step_duration_ms,
                        )
                    )
                    overall_status = ExecutionStatus.FAILED
                    break

            # Record step outcome
            step_results.append(
                StepResult(
                    step_no=step.step_no,
                    step_name=step.step_name,
                    tool=step.tool,
                    status=StepExecutionStatus.COMPLETED,
                    output=tool_output,
                    condition_result=cond_result,
                    duration_ms=step_duration_ms,
                    retries_attempted=retries_done,
                )
            )

            # g. Apply Condition Action
            if cond_result is not None:
                action = cond_result.action.upper()
                if action == ConditionAction.SKIP.value:
                    skip_remaining = True
                elif action == ConditionAction.STOP.value:
                    # Normal early termination
                    overall_status = ExecutionStatus.SUCCESS
                    break
                elif action in (ConditionAction.ASK_USER.value, "NEEDS_INPUT"):
                    overall_status = ExecutionStatus.NEEDS_INPUT
                    break
                elif action == ConditionAction.ESCALATE.value:
                    overall_status = ExecutionStatus.ESCALATED
                    break
                # CONTINUE: proceed normally

        total_duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        result = WorkflowExecutionResult(
            run_id=active_run_id,
            workflow_id=workflow_id,
            status=overall_status,
            final_output=final_output,
            step_results=step_results,
            errors=errors,
            missing_inputs=[],
            context_summary=context.step_outputs,
            duration_ms=total_duration_ms,
        )

        if self.logger is not None:
            try:
                if hasattr(self.logger, "log_execution"):
                    self.logger.log_execution(result, workflow_name=workflow.name, input_params=user_params)
                elif callable(self.logger):
                    self.logger(result)
            except Exception:
                pass

        return result

    def _validate_inputs(
        self,
        workflow: WorkflowDefinition,
        user_params: Dict[str, Any],
    ) -> List[str]:
        """Validate presence of required parameters before executing steps."""
        res = self.resolver.resolve(workflow, user_params)
        if res.status != ExecutionStatus.SUCCESS.value:
            return res.missing_user_inputs + res.missing_repo_files
        return []

    def _collect_var_references(self, obj: Any, needed: Set[str], produced: Set[str]) -> None:
        """Find variables that must be supplied externally because they are not yet produced."""
        if isinstance(obj, str):
            if obj.startswith("$"):
                root_var = obj[1:].split(".")[0].split("[")[0]
                if root_var not in produced:
                    needed.add(root_var)
        elif isinstance(obj, dict):
            for v in obj.values():
                self._collect_var_references(v, needed, produced)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_var_references(item, needed, produced)

    @staticmethod
    def _is_param_supplied(name: str, user_params: Dict[str, Any]) -> bool:
        """Check if parameter exists with non-empty, non-null value, supporting generic aliases."""
        found, _ = find_param_in_dict(name, user_params)
        return found


default_executor = GenericWorkflowExecutor()


def execute_workflow(
    workflow_id: str,
    params: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> WorkflowExecutionResult:
    """Execute a workflow through the default GenericWorkflowExecutor."""
    return default_executor.execute(workflow_id=workflow_id, params=params, **kwargs)
