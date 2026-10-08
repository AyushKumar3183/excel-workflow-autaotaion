"""Deterministic Tracing and Execution Logging layer.

Constructs structured traces from workflow execution results, formats human-readable
execution telemetry, sanitizes sensitive data, and maintains append-only JSONL logs.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

# Default path for persistent logs
DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "workflow_logs.jsonl"

SECRET_KEYWORDS = ("api_key", "token", "secret", "password", "auth", "credential", "private_key")


# ---------------------------------------------------------------------------
# Data Sanitization
# ---------------------------------------------------------------------------

def sanitize_value(val: Any) -> Any:
    """Recursively redact sensitive fields and secrets from trace payloads."""
    if isinstance(val, dict):
        sanitized = {}
        for k, v in val.items():
            if any(kw in str(k).lower() for kw in SECRET_KEYWORDS):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = sanitize_value(v)
        return sanitized
    elif isinstance(val, list):
        return [sanitize_value(item) for item in val]
    return val


# ---------------------------------------------------------------------------
# Trace Models
# ---------------------------------------------------------------------------

class StepTrace(BaseModel):
    """Execution telemetry for a single workflow step."""
    step_no: int
    step_name: str
    tool: str
    status: str  # COMPLETED, SKIPPED, FAILED
    params: Optional[Dict[str, Any]] = None
    output: Optional[Any] = None
    condition_passed: Optional[bool] = None
    condition_action: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    retries_attempted: int = 0


class ExecutionTrace(BaseModel):
    """Complete, structured record of a workflow execution run."""
    run_id: str
    workflow_id: str
    workflow_name: Optional[str] = None
    start_time: str
    end_time: str
    final_status: str  # SUCCESS, NEEDS_INPUT, ESCALATED, FAILED
    input_parameters: Dict[str, Any] = Field(default_factory=dict)
    executed_steps: List[StepTrace] = Field(default_factory=list)
    final_output: Optional[Any] = None
    errors: List[str] = Field(default_factory=list)
    missing_inputs: List[str] = Field(default_factory=list)
    duration_ms: Optional[float] = None


def create_trace(
    result: Any,
    workflow_name: Optional[str] = None,
    input_params: Optional[Dict[str, Any]] = None,
) -> ExecutionTrace:
    """Construct a sanitized ExecutionTrace from a WorkflowExecutionResult."""
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    duration_ms = getattr(result, "duration_ms", 0.0) or 0.0

    # Derive approximate start time from duration
    start_time = now_iso
    end_time = now_iso

    step_traces: List[StepTrace] = []
    for sr in getattr(result, "step_results", []):
        cond_passed = None
        cond_action = None
        if getattr(sr, "condition_result", None) is not None:
            cond_passed = sr.condition_result.passed
            cond_action = sr.condition_result.action

        step_traces.append(
            StepTrace(
                step_no=sr.step_no,
                step_name=sr.step_name,
                tool=sr.tool,
                status=sr.status.value if hasattr(sr.status, "value") else str(sr.status),
                params=None,  # Params can be attached if available
                output=sanitize_value(sr.output),
                condition_passed=cond_passed,
                condition_action=cond_action,
                error=sr.error,
                duration_ms=getattr(sr, "duration_ms", None),
                retries_attempted=getattr(sr, "retries_attempted", 0),
            )
        )

    status_str = result.status.value if hasattr(result.status, "value") else str(result.status)

    return ExecutionTrace(
        run_id=result.run_id,
        workflow_id=result.workflow_id,
        workflow_name=workflow_name,
        start_time=start_time,
        end_time=end_time,
        final_status=status_str,
        input_parameters=sanitize_value(input_params or {}),
        executed_steps=step_traces,
        final_output=sanitize_value(getattr(result, "final_output", None)),
        errors=list(getattr(result, "errors", [])),
        missing_inputs=list(getattr(result, "missing_inputs", [])),
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Trace Formatter
# ---------------------------------------------------------------------------

def format_trace(
    trace_or_result: Union[ExecutionTrace, Any],
    workflow_name: Optional[str] = None,
) -> str:
    """Format an execution trace or execution result into human-readable text."""
    if not isinstance(trace_or_result, ExecutionTrace):
        trace = create_trace(trace_or_result, workflow_name=workflow_name)
    else:
        trace = trace_or_result

    name_label = f" - {trace.workflow_name}" if trace.workflow_name else ""
    dur_label = f" ({trace.duration_ms:.1f}ms)" if trace.duration_ms is not None else ""

    lines = [
        f"Workflow: {trace.workflow_id}{name_label}",
        f"Run ID: {trace.run_id}",
        f"Status: {trace.final_status}{dur_label}",
    ]

    if trace.missing_inputs:
        lines.append(f"Missing Inputs: {', '.join(trace.missing_inputs)}")

    if trace.input_parameters:
        lines.append("\nInputs:")
        for k, v in trace.input_parameters.items():
            lines.append(f"  {k}: {v}")

    lines.append("\nSteps:")
    if not trace.executed_steps:
        lines.append("  (No steps executed)")
    else:
        for st in trace.executed_steps:
            st_dur = f" ({st.duration_ms:.1f}ms)" if st.duration_ms is not None else ""
            lines.append(f"{st.step_no}. {st.step_name}")
            lines.append(f"   Tool: {st.tool}")
            lines.append(f"   Status: {st.status}{st_dur}")

            if st.condition_passed is not None:
                passed_str = "TRUE" if st.condition_passed else "FALSE"
                act_str = f" -> {st.condition_action}" if st.condition_action else ""
                lines.append(f"   Condition: {passed_str}{act_str}")

            if st.error:
                lines.append(f"   Error: {st.error}")

    if trace.errors:
        lines.append("\nErrors:")
        for err in trace.errors:
            lines.append(f"  - {err}")

    lines.append("\nFinal Result:")
    if trace.final_output is not None:
        if isinstance(trace.final_output, (dict, list)):
            lines.append(json.dumps(trace.final_output, indent=2))
        else:
            lines.append(str(trace.final_output))
    else:
        lines.append("None")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistent Execution Logger (JSONL)
# ---------------------------------------------------------------------------

class ExecutionLogger:
    """Append-only persistent execution logger for JSONL storage."""

    def __init__(self, log_path: Optional[Union[str, Path]] = None):
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG_PATH

    def log_execution(
        self,
        trace_or_result: Union[ExecutionTrace, Any],
        workflow_name: Optional[str] = None,
        input_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append a single structured execution record to the JSONL log file.
        
        Returns:
            The logged dictionary payload.
        """
        if not isinstance(trace_or_result, ExecutionTrace):
            trace = create_trace(trace_or_result, workflow_name=workflow_name, input_params=input_params)
        else:
            trace = trace_or_result

        # Ensure parent directory exists
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # Build comprehensive record compatible with WF010 performance analysis
        record = {
            "execution_id": trace.run_id,
            "workflow_id": trace.workflow_id,
            "workflow_name": trace.workflow_name,
            "status": trace.final_status,
            "duration_ms": trace.duration_ms,
            "error": trace.errors[0] if trace.errors else None,
            "errors": trace.errors,
            "timestamp": trace.end_time,
            "step_count": len(trace.executed_steps),
            "completed_steps": sum(1 for s in trace.executed_steps if s.status == "COMPLETED"),
            "skipped_steps": sum(1 for s in trace.executed_steps if s.status == "SKIPPED"),
            "failed_steps": sum(1 for s in trace.executed_steps if s.status == "FAILED"),
            "final_output": trace.final_output,
            "steps": [s.model_dump() for s in trace.executed_steps],
        }

        line = json.dumps(record) + "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)

        return record

    def read_executions(self) -> List[Dict[str, Any]]:
        """Read all execution records from the persistent JSONL log."""
        if not self.log_path.exists():
            return []

        records: List[Dict[str, Any]] = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                clean = line.strip()
                if clean:
                    try:
                        records.append(json.loads(clean))
                    except json.JSONDecodeError:
                        continue
        return records

    def filter_by_workflow(self, workflow_id: str) -> List[Dict[str, Any]]:
        """Return execution records for a specific workflow ID."""
        target_id = workflow_id.strip().upper()
        return [
            r for r in self.read_executions()
            if str(r.get("workflow_id", "")).strip().upper() == target_id
        ]

    def filter_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Return execution records matching a specific execution status."""
        target_status = status.strip().upper()
        return [
            r for r in self.read_executions()
            if str(r.get("status", "")).strip().upper() == target_status
        ]


# Default logger instance
default_logger = ExecutionLogger()


def log_execution(
    trace_or_result: Union[ExecutionTrace, Any],
    workflow_name: Optional[str] = None,
    log_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Convenience helper to log an execution using the default or custom logger."""
    logger = ExecutionLogger(log_path) if log_path else default_logger
    return logger.log_execution(trace_or_result, workflow_name=workflow_name)


def read_executions(log_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Convenience helper to read all executions from the JSONL log."""
    logger = ExecutionLogger(log_path) if log_path else default_logger
    return logger.read_executions()


def filter_by_workflow(workflow_id: str, log_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Convenience helper to filter logged executions by workflow ID."""
    logger = ExecutionLogger(log_path) if log_path else default_logger
    return logger.filter_by_workflow(workflow_id)


def filter_by_status(status: str, log_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Convenience helper to filter logged executions by status."""
    logger = ExecutionLogger(log_path) if log_path else default_logger
    return logger.filter_by_status(status)
