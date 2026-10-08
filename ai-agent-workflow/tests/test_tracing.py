"""Unit tests for the tracing and execution logging layer.

Tests cover:
- Trace creation and StepTrace model verification
- Deterministic trace formatting for SUCCESS, FAILED, NEEDS_INPUT, and ESCALATED
- Skipped step representation in formatted trace
- Sensitive data sanitization (no secrets logged)
- JSONL append-only persistence
- Reading logs, filtering by workflow, and filtering by status
- Multiple execution append integrity
- Executor-to-tracing integration via dependency injection
"""

from pathlib import Path
import pytest

from app.tracing.trace import (
    ExecutionLogger,
    ExecutionTrace,
    StepTrace,
    create_trace,
    format_trace,
    sanitize_value,
)
from app.workflow.executor import (
    ExecutionStatus,
    GenericWorkflowExecutor,
    StepExecutionStatus,
    StepResult,
    WorkflowExecutionResult,
)
from app.workflow.models import WorkflowDefinition, WorkflowStep
from app.workflow.registry import WorkflowRegistry
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Trace Model & Sanitization Tests
# ---------------------------------------------------------------------------

def test_trace_creation():
    """Test constructing ExecutionTrace and StepTrace from WorkflowExecutionResult."""
    step1 = StepResult(
        step_no=1,
        step_name="Load Catalog",
        tool="load_file",
        status=StepExecutionStatus.COMPLETED,
        output=[{"sku": "SKU-1"}],
        duration_ms=12.5,
    )
    result = WorkflowExecutionResult(
        run_id="run-test-01",
        workflow_id="WF001",
        status=ExecutionStatus.SUCCESS,
        final_output={"count": 1},
        step_results=[step1],
        duration_ms=25.0,
    )

    trace = create_trace(result, workflow_name="Inventory Restock Check", input_params={"threshold": 10})

    assert trace.run_id == "run-test-01"
    assert trace.workflow_id == "WF001"
    assert trace.workflow_name == "Inventory Restock Check"
    assert trace.final_status == "SUCCESS"
    assert len(trace.executed_steps) == 1
    assert trace.executed_steps[0].step_name == "Load Catalog"
    assert trace.executed_steps[0].tool == "load_file"
    assert trace.executed_steps[0].status == "COMPLETED"
    assert trace.input_parameters["threshold"] == 10


def test_sanitize_secrets_redaction():
    """Test that sensitive credentials, API keys, and tokens are redacted."""
    sensitive_payload = {
        "api_key": "secret_gemini_key_12345",
        "user_token": "bearer_jwt_token_xyz",
        "auth_secret": "my_super_secret_password",
        "normal_field": "public_data",
        "nested": {
            "password": "hidden_db_password",
            "safe_val": 42,
        },
        "items": [
            {"private_key": "rsa_private", "name": "Item A"}
        ],
    }

    sanitized = sanitize_value(sensitive_payload)

    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["user_token"] == "[REDACTED]"
    assert sanitized["auth_secret"] == "[REDACTED]"
    assert sanitized["normal_field"] == "public_data"
    assert sanitized["nested"]["password"] == "[REDACTED]"
    assert sanitized["nested"]["safe_val"] == 42
    assert sanitized["items"][0]["private_key"] == "[REDACTED]"
    assert sanitized["items"][0]["name"] == "Item A"


def test_no_secrets_stored_in_trace():
    """Test that create_trace automatically redacts secret fields from inputs and outputs."""
    result = WorkflowExecutionResult(
        run_id="run-sec",
        workflow_id="WF002",
        status=ExecutionStatus.SUCCESS,
        final_output={"secret_token": "should_be_hidden", "total": 100},
        step_results=[],
    )
    trace = create_trace(
        result,
        input_params={"api_key": "top_secret_api_key", "vendor_file": "prices.csv"},
    )

    assert trace.input_parameters["api_key"] == "[REDACTED]"
    assert trace.input_parameters["vendor_file"] == "prices.csv"
    assert isinstance(trace.final_output, dict)
    assert trace.final_output["secret_token"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Trace Formatter Tests
# ---------------------------------------------------------------------------

def test_successful_execution_formatting():
    """Test deterministic formatting for a successful workflow execution."""
    step1 = StepResult(
        step_no=1,
        step_name="Load File",
        tool="load_file",
        status=StepExecutionStatus.COMPLETED,
        duration_ms=10.0,
    )
    result = WorkflowExecutionResult(
        run_id="run-succ",
        workflow_id="WF001",
        status=ExecutionStatus.SUCCESS,
        final_output={"restock_count": 5},
        step_results=[step1],
        duration_ms=15.2,
    )

    formatted = format_trace(result, workflow_name="Inventory Restock Check")

    assert "Workflow: WF001 - Inventory Restock Check" in formatted
    assert "Status: SUCCESS" in formatted
    assert "1. Load File" in formatted
    assert "Tool: load_file" in formatted
    assert "Status: COMPLETED" in formatted
    assert "restock_count" in formatted


def test_failed_execution_formatting():
    """Test formatting for a failed workflow execution with error details."""
    step1 = StepResult(
        step_no=1,
        step_name="Query Database",
        tool="query_db",
        status=StepExecutionStatus.FAILED,
        error="Connection timeout to orders DB",
        duration_ms=5000.0,
    )
    result = WorkflowExecutionResult(
        run_id="run-fail",
        workflow_id="WF005",
        status=ExecutionStatus.FAILED,
        step_results=[step1],
        errors=["Connection timeout to orders DB"],
        duration_ms=5005.0,
    )

    formatted = format_trace(result, workflow_name="Customer Order Status")

    assert "Status: FAILED" in formatted
    assert "Status: FAILED" in formatted
    assert "Error: Connection timeout to orders DB" in formatted
    assert "- Connection timeout to orders DB" in formatted


def test_needs_input_formatting():
    """Test formatting for a workflow requiring user input."""
    result = WorkflowExecutionResult(
        run_id="run-input",
        workflow_id="WF004",
        status=ExecutionStatus.NEEDS_INPUT,
        missing_inputs=["product_name", "category"],
        errors=["Missing required inputs"],
    )

    formatted = format_trace(result, workflow_name="Product Description Generator")

    assert "Status: NEEDS_INPUT" in formatted
    assert "Missing Inputs: product_name, category" in formatted


def test_escalated_formatting():
    """Test formatting for an escalated workflow run."""
    step1 = StepResult(
        step_no=1,
        step_name="Match Skills",
        tool="filter_rows",
        status=StepExecutionStatus.COMPLETED,
    )
    result = WorkflowExecutionResult(
        run_id="run-esc",
        workflow_id="WF009",
        status=ExecutionStatus.ESCALATED,
        step_results=[step1],
        errors=["No qualified engineer found with capacity"],
    )

    formatted = format_trace(result, workflow_name="Employee Task Assignment")

    assert "Status: ESCALATED" in formatted
    assert "No qualified engineer found with capacity" in formatted


def test_skipped_step_formatting():
    """Test that skipped steps are explicitly represented in the formatted trace."""
    step1 = StepResult(
        step_no=1,
        step_name="Initial Check",
        tool="compare_threshold",
        status=StepExecutionStatus.COMPLETED,
    )
    step2 = StepResult(
        step_no=2,
        step_name="Generate Email",
        tool="format_output",
        status=StepExecutionStatus.SKIPPED,
    )
    result = WorkflowExecutionResult(
        run_id="run-skip",
        workflow_id="WF001",
        status=ExecutionStatus.SUCCESS,
        step_results=[step1, step2],
    )

    formatted = format_trace(result)

    assert "2. Generate Email" in formatted
    assert "Status: SKIPPED" in formatted


# ---------------------------------------------------------------------------
# Persistent Execution Logger (JSONL) Tests
# ---------------------------------------------------------------------------

def test_jsonl_append_and_read(tmp_path):
    """Test append-only JSONL logging and reading records."""
    log_file = tmp_path / "test_logs.jsonl"
    logger = ExecutionLogger(log_path=log_file)

    res1 = WorkflowExecutionResult(
        run_id="exec-101",
        workflow_id="WF001",
        status=ExecutionStatus.SUCCESS,
        duration_ms=20.0,
    )
    logger.log_execution(res1, workflow_name="Inventory Restock Check")

    # Verify file exists and has 1 line
    assert log_file.exists()
    records = logger.read_executions()
    assert len(records) == 1
    assert records[0]["execution_id"] == "exec-101"
    assert records[0]["workflow_id"] == "WF001"
    assert records[0]["status"] == "SUCCESS"
    assert records[0]["duration_ms"] == 20.0


def test_multiple_executions_append_correctly(tmp_path):
    """Test that multiple logs append sequentially without overwriting."""
    log_file = tmp_path / "test_logs.jsonl"
    logger = ExecutionLogger(log_path=log_file)

    for i in range(5):
        res = WorkflowExecutionResult(
            run_id=f"exec-{i}",
            workflow_id=f"WF{i:03d}",
            status=ExecutionStatus.SUCCESS if i % 2 == 0 else ExecutionStatus.FAILED,
            duration_ms=10.0 * (i + 1),
        )
        logger.log_execution(res)

    records = logger.read_executions()
    assert len(records) == 5
    assert [r["execution_id"] for r in records] == [f"exec-{i}" for i in range(5)]


def test_filter_by_workflow(tmp_path):
    """Test filtering execution logs by workflow ID."""
    log_file = tmp_path / "test_logs.jsonl"
    logger = ExecutionLogger(log_path=log_file)

    logger.log_execution(WorkflowExecutionResult(run_id="1", workflow_id="WF001", status=ExecutionStatus.SUCCESS))
    logger.log_execution(WorkflowExecutionResult(run_id="2", workflow_id="WF002", status=ExecutionStatus.SUCCESS))
    logger.log_execution(WorkflowExecutionResult(run_id="3", workflow_id="WF001", status=ExecutionStatus.FAILED))

    wf001_records = logger.filter_by_workflow("WF001")
    assert len(wf001_records) == 2
    assert {r["execution_id"] for r in wf001_records} == {"1", "3"}

    wf002_records = logger.filter_by_workflow("WF002")
    assert len(wf002_records) == 1
    assert wf002_records[0]["execution_id"] == "2"


def test_filter_by_status(tmp_path):
    """Test filtering execution logs by execution status."""
    log_file = tmp_path / "test_logs.jsonl"
    logger = ExecutionLogger(log_path=log_file)

    logger.log_execution(WorkflowExecutionResult(run_id="1", workflow_id="WF001", status=ExecutionStatus.SUCCESS))
    logger.log_execution(WorkflowExecutionResult(run_id="2", workflow_id="WF002", status=ExecutionStatus.FAILED))
    logger.log_execution(WorkflowExecutionResult(run_id="3", workflow_id="WF003", status=ExecutionStatus.NEEDS_INPUT))
    logger.log_execution(WorkflowExecutionResult(run_id="4", workflow_id="WF004", status=ExecutionStatus.ESCALATED))

    successes = logger.filter_by_status("SUCCESS")
    assert len(successes) == 1
    assert successes[0]["execution_id"] == "1"

    failures = logger.filter_by_status("FAILED")
    assert len(failures) == 1
    assert failures[0]["execution_id"] == "2"

    needs_input = logger.filter_by_status("NEEDS_INPUT")
    assert len(needs_input) == 1
    assert needs_input[0]["execution_id"] == "3"


# ---------------------------------------------------------------------------
# Executor to Tracing Integration Test
# ---------------------------------------------------------------------------

def test_executor_with_injected_logger_integration(tmp_path):
    """Test GenericWorkflowExecutor automatically logging to injected ExecutionLogger."""
    log_file = tmp_path / "executor_audit.jsonl"
    logger = ExecutionLogger(log_path=log_file)

    tool_reg = ToolRegistry()
    tool_reg.register("calc", lambda x: x * 2)

    wf_reg = WorkflowRegistry()
    step1 = WorkflowStep(
        step_no=1,
        step_name="Double Value",
        tool="calc",
        params={"x": "$num"},
        output_variable="doubled",
    )
    wf_reg.register(
        WorkflowDefinition(
            workflow_id="WF_AUDIT",
            name="Audit Logging Test",
            trigger="Trigger",
            steps=[step1],
        )
    )

    executor = GenericWorkflowExecutor(
        registry=wf_reg,
        tool_registry=tool_reg,
        logger=logger,  # Dependency injected logger
    )

    # Execute workflow
    result = executor.execute("WF_AUDIT", {"num": 25})
    assert result.status == ExecutionStatus.SUCCESS
    assert result.final_output == 50

    # Verify that logger automatically persisted the execution
    records = logger.read_executions()
    assert len(records) == 1
    assert records[0]["workflow_id"] == "WF_AUDIT"
    assert records[0]["status"] == "SUCCESS"
    assert records[0]["step_count"] == 1
    assert records[0]["completed_steps"] == 1
    assert records[0]["final_output"] == 50
