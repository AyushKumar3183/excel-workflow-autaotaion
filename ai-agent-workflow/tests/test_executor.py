"""Unit and integration tests for the GenericWorkflowExecutor.

Tests cover:
1. Successful multi-step execution
2. Workflow not found
3. Missing required input (status NEEDS_INPUT, not FAILED)
4. Tool not found
5. Invalid tool parameters
6. Tool execution failure
7. Context propagation between steps
8. Condition CONTINUE
9. Condition SKIP
10. Condition STOP
11. Condition ASK_USER
12. Condition ESCALATE
13. Skipped steps recorded with SKIPPED status
14. Final SUCCESS status
15. Final NEEDS_INPUT status
16. Final ESCALATED status
17. Final FAILED status
18. Transient retry behavior
19. Validation errors are not retried
20. WF001 End-to-End Integration Proof (from Excel workbook)
"""

from pathlib import Path
import pytest

from app.tools.registry import ToolParameterError, ToolRegistry
from app.workflow.models import (
    Condition,
    WorkflowDefinition,
    WorkflowInput,
    WorkflowStep,
)
from app.workflow.registry import WorkflowRegistry
from app.workflow.executor import (
    ExecutionContext,
    ExecutionStatus,
    GenericWorkflowExecutor,
    StepExecutionStatus,
    execute_workflow,
)
from app.tools import default_tool_registry


# ---------------------------------------------------------------------------
# Helpers for Deterministic Test Setup
# ---------------------------------------------------------------------------

def _create_mock_registry_and_tools():
    """Build isolated registry and tools for deterministic unit testing."""
    tool_reg = ToolRegistry()
    wf_reg = WorkflowRegistry()

    # Tool 1: add_one
    def add_one(val: int) -> int:
        return val + 1

    # Tool 2: multiply
    def multiply(a: int, b: int) -> int:
        return a * b

    # Tool 3: validate_tag
    def validate_tag(tag: str) -> dict:
        return {"is_valid": tag != "bad_tag", "tag": tag}

    # Tool 4: flakey_tool (fails once, then succeeds)
    attempts = {"count": 0}

    def flakey_tool(val: int) -> int:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("Transient connection blip")
        return val * 10

    tool_reg.register("add_one", add_one)
    tool_reg.register("multiply", multiply)
    tool_reg.register("validate_tag", validate_tag)
    tool_reg.register("flakey_tool", flakey_tool)

    return wf_reg, tool_reg, attempts


# ---------------------------------------------------------------------------
# Executor Unit Tests
# ---------------------------------------------------------------------------

def test_successful_multi_step_execution():
    """Test standard multi-step execution with context propagation and final SUCCESS."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Add One",
        tool="add_one",
        params={"val": "$initial_num"},
        output_variable="num_plus_one",
    )
    step2 = WorkflowStep(
        step_no=2,
        step_name="Multiply",
        tool="multiply",
        params={"a": "$num_plus_one", "b": 3},
        output_variable="multiplied",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST01",
        name="Multi Step Test",
        trigger="Test multi step",
        steps=[step1, step2],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST01", {"initial_num": 5})

    assert res.status == ExecutionStatus.SUCCESS
    assert len(res.step_results) == 2
    assert res.step_results[0].output == 6
    assert res.step_results[1].output == 18
    assert res.final_output == 18
    assert res.context_summary["num_plus_one"] == 6
    assert res.context_summary["multiplied"] == 18


def test_workflow_not_found():
    """Test that requesting an unknown workflow ID returns FAILED."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()
    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)

    res = executor.execute("UNKNOWN_WF", {})
    assert res.status == ExecutionStatus.FAILED
    assert "not found in registry" in res.errors[0]


def test_missing_required_input_returns_needs_input():
    """Test missing required input returns status NEEDS_INPUT without executing tools."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step One",
        tool="add_one",
        params={"val": "$mandatory_val"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_INPUT",
        name="Input Check",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_INPUT", {})  # Missing mandatory_val

    assert res.status == ExecutionStatus.NEEDS_INPUT
    assert "mandatory_val" in res.missing_inputs
    assert len(res.step_results) == 0  # No tools executed!


def test_tool_not_found_returns_failed():
    """Test that a step referencing an unregistered tool returns FAILED."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Unknown Tool Step",
        tool="non_existent_tool",
        params={"x": "$num"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_TOOL_FAIL",
        name="Tool Missing",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_TOOL_FAIL", {"num": 10})

    assert res.status == ExecutionStatus.FAILED
    assert len(res.step_results) == 1
    assert res.step_results[0].status == StepExecutionStatus.FAILED
    assert res.step_results[0].error is not None
    assert "not found in ToolRegistry" in res.step_results[0].error


def test_invalid_tool_parameters_returns_failed():
    """Test invalid tool arguments produce FAILED without retrying."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    # add_one expects val: int, but we provide unexpected arg 'foo'
    step1 = WorkflowStep(
        step_no=1,
        step_name="Bad Params Step",
        tool="add_one",
        params={"foo": "$num"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_BAD_PARAMS",
        name="Bad Params",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg, max_retries=3)
    res = executor.execute("TEST_BAD_PARAMS", {"num": 10})

    assert res.status == ExecutionStatus.FAILED
    assert res.step_results[0].status == StepExecutionStatus.FAILED
    assert res.step_results[0].retries_attempted == 0  # Not retried!


def test_tool_execution_failure():
    """Test tool raising an exception causes step and workflow failure."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    def exploding_tool(x: int) -> int:
        raise ValueError("Fatal calculation exception")

    tool_reg.register("exploding_tool", exploding_tool)

    step1 = WorkflowStep(
        step_no=1,
        step_name="Explosion",
        tool="exploding_tool",
        params={"x": "$num"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_EXPLODE",
        name="Explosion Test",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_EXPLODE", {"num": 10})

    assert res.status == ExecutionStatus.FAILED
    assert res.step_results[0].status == StepExecutionStatus.FAILED
    assert res.step_results[0].error is not None
    assert "Fatal calculation exception" in res.step_results[0].error


def test_context_propagation_between_steps():
    """Test that step 2 correctly uses step 1 output and step 3 uses both."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Add",
        tool="add_one",
        params={"val": "$x"},
        output_variable="x_plus_1",
    )
    step2 = WorkflowStep(
        step_no=2,
        step_name="Add Again",
        tool="add_one",
        params={"val": "$x_plus_1"},
        output_variable="x_plus_2",
    )
    step3 = WorkflowStep(
        step_no=3,
        step_name="Multiply",
        tool="multiply",
        params={"a": "$x_plus_1", "b": "$x_plus_2"},
        output_variable="final_product",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_CONTEXT",
        name="Context Propagation",
        trigger="Trigger",
        steps=[step1, step2, step3],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    # x = 2 -> step1: 3, step2: 4, step3: 3 * 4 = 12
    res = executor.execute("TEST_CONTEXT", {"x": 2})

    assert res.status == ExecutionStatus.SUCCESS
    assert res.final_output == 12
    assert res.step_results[0].output == 3
    assert res.step_results[1].output == 4
    assert res.step_results[2].output == 12


def test_condition_continue():
    """Test that condition action CONTINUE allows subsequent steps to run."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Add",
        tool="add_one",
        params={"val": "$x"},
        output_variable="val1",
        condition=Condition(field="val1", operator=">", value=0, action_on_true="CONTINUE"),
    )
    step2 = WorkflowStep(
        step_no=2,
        step_name="Multiply",
        tool="multiply",
        params={"a": "$val1", "b": 2},
        output_variable="val2",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_COND_CONT",
        name="Condition Continue",
        trigger="Trigger",
        steps=[step1, step2],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_COND_CONT", {"x": 5})

    assert res.status == ExecutionStatus.SUCCESS
    assert len(res.step_results) == 2
    assert res.step_results[0].status == StepExecutionStatus.COMPLETED
    assert res.step_results[1].status == StepExecutionStatus.COMPLETED


def test_condition_skip():
    """Test that condition action SKIP skips subsequent steps and records status SKIPPED."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1",
        tool="add_one",
        params={"val": "$x"},
        output_variable="val1",
        condition=Condition(field="val1", operator=">", value=0, action_on_true="SKIP"),
    )
    step2 = WorkflowStep(
        step_no=2,
        step_name="Step 2 (Skipped)",
        tool="multiply",
        params={"a": "$val1", "b": 2},
        output_variable="val2",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_COND_SKIP",
        name="Condition Skip",
        trigger="Trigger",
        steps=[step1, step2],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_COND_SKIP", {"x": 5})

    assert res.status == ExecutionStatus.SUCCESS
    assert len(res.step_results) == 2
    assert res.step_results[0].status == StepExecutionStatus.COMPLETED
    assert res.step_results[1].status == StepExecutionStatus.SKIPPED


def test_condition_stop():
    """Test that condition action STOP terminates workflow normally with SUCCESS."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1",
        tool="add_one",
        params={"val": "$x"},
        output_variable="val1",
        # If val1 > 0, STOP immediately
        condition=Condition(field="val1", operator=">", value=0, action_on_true="STOP"),
    )
    step2 = WorkflowStep(
        step_no=2,
        step_name="Step 2 (Not Reached)",
        tool="multiply",
        params={"a": "$val1", "b": 2},
        output_variable="val2",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_COND_STOP",
        name="Condition Stop",
        trigger="Trigger",
        steps=[step1, step2],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_COND_STOP", {"x": 5})

    assert res.status == ExecutionStatus.SUCCESS
    assert len(res.step_results) == 1
    assert res.final_output == 6


def test_condition_ask_user_returns_needs_input():
    """Test that condition returning ASK_USER terminates with status NEEDS_INPUT."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Validate Tag",
        tool="validate_tag",
        params={"tag": "$tag"},
        output_variable="tag_result",
        condition=Condition(field="is_valid", operator="==", value=True, action_on_false="ASK_USER"),
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_COND_ASK",
        name="Condition Ask User",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_COND_ASK", {"tag": "bad_tag"})

    assert res.status == ExecutionStatus.NEEDS_INPUT
    assert len(res.step_results) == 1
    assert res.step_results[0].condition_result is not None
    assert res.step_results[0].condition_result.action == "ASK_USER"


def test_condition_escalate_returns_escalated():
    """Test that condition returning ESCALATE terminates with status ESCALATED."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Validate Tag",
        tool="validate_tag",
        params={"tag": "$tag"},
        output_variable="tag_result",
        condition=Condition(field="is_valid", operator="==", value=True, action_on_false="ESCALATE"),
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_COND_ESCALATE",
        name="Condition Escalate",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_COND_ESCALATE", {"tag": "bad_tag"})

    assert res.status == ExecutionStatus.ESCALATED
    assert len(res.step_results) == 1
    assert res.step_results[0].condition_result is not None
    assert res.step_results[0].condition_result.action == "ESCALATE"


def test_transient_retry_behavior():
    """Test that step with on_error=RETRY retries transient failure and succeeds."""
    wf_reg, tool_reg, attempts = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Flakey Step",
        tool="flakey_tool",
        params={"val": "$x"},
        output_variable="res",
        on_error="RETRY",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_RETRY",
        name="Retry Test",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg, max_retries=1)
    res = executor.execute("TEST_RETRY", {"x": 7})

    assert res.status == ExecutionStatus.SUCCESS
    assert res.final_output == 70
    assert attempts["count"] == 2  # Proves it retried after 1 failure!
    assert res.step_results[0].retries_attempted == 1


def test_skipped_steps_recorded_explicitly():
    """Test that skipped steps are explicitly recorded in step_results with SKIPPED status."""
    wf_reg, tool_reg, _ = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1 Skip Trigger",
        tool="add_one",
        params={"val": "$x"},
        output_variable="out1",
        condition=Condition(field="out1", operator=">", value=0, action_on_true="SKIP"),
    )
    step2 = WorkflowStep(
        step_no=2,
        step_name="Step 2 Must Be Skipped",
        tool="add_one",
        params={"val": "$out1"},
        output_variable="out2",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_EXPLICIT_SKIP",
        name="Explicit Skip",
        trigger="Trigger",
        steps=[step1, step2],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg)
    res = executor.execute("TEST_EXPLICIT_SKIP", {"x": 5})

    assert len(res.step_results) == 2
    assert res.step_results[0].status == StepExecutionStatus.COMPLETED
    assert res.step_results[1].status == StepExecutionStatus.SKIPPED
    assert res.step_results[1].step_no == 2
    assert res.step_results[1].step_name == "Step 2 Must Be Skipped"


def test_validation_errors_are_not_retried():
    """Test that input validation errors immediately return NEEDS_INPUT with zero tool retries."""
    wf_reg, tool_reg, attempts = _create_mock_registry_and_tools()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step One",
        tool="flakey_tool",
        params={"val": "$required_x"},
        output_variable="res",
        on_error="RETRY",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_NO_RETRY_VAL",
        name="Validation No Retry",
        trigger="Trigger",
        steps=[step1],
    )
    wf_reg.register(wf)

    executor = GenericWorkflowExecutor(registry=wf_reg, tool_registry=tool_reg, max_retries=5)
    # Call without required_x
    res = executor.execute("TEST_NO_RETRY_VAL", {})

    assert res.status == ExecutionStatus.NEEDS_INPUT
    assert attempts["count"] == 0  # No tool attempts whatsoever!
    assert len(res.step_results) == 0



# ---------------------------------------------------------------------------
# WF001 End-to-End Integration Proof
# ---------------------------------------------------------------------------

def test_wf001_end_to_end_integration_proof():
    """Integration proof for WF001 from Excel workbook through Generic Executor.
    
    Proves:
    Workbook Definition -> Registry -> Generic Executor -> Tool Registry -> Condition Engine -> Result
    """
    from app.workflow.loader import load_workbook

    # 1. Load actual workflows from working copy Excel workbook
    workbook_path = Path("data/workflows_working.xlsx")
    workflows = load_workbook(workbook_path)

    wf_registry = WorkflowRegistry()
    for wf in workflows:
        wf_registry.register(wf)

    assert wf_registry.exists("WF001") is True
    wf001 = wf_registry.get("WF001")
    assert wf001 is not None
    assert len(wf001.steps) == 5

    # 2. Instantiate generic executor with standard tool registry
    executor = GenericWorkflowExecutor(
        registry=wf_registry,
        tool_registry=default_tool_registry,
    )

    # 3. Execute WF001 with sample inventory.csv and threshold
    params = {
        "inventory_file": "data/inventory.csv",
        "minimum_threshold": 20,
    }
    result = executor.execute("WF001", params)

    # 4. Verify successful execution and outcomes
    assert result.status == ExecutionStatus.SUCCESS
    assert len(result.step_results) == 5

    # Check that all 5 steps completed
    for sr in result.step_results:
        assert sr.status == StepExecutionStatus.COMPLETED

    # Verify final output structure
    assert result.final_output is not None
    assert result.final_output.get("format") == "restock_report"
    assert "data" in result.final_output

    restock_items = result.final_output["data"]
    assert len(restock_items) > 0

    # Verify restock calculation logic executed properly
    for item in restock_items:
        assert item["is_low_stock"] is True
        assert "suggested_reorder_quantity" in item
        assert item["suggested_reorder_quantity"] > 0


# ---------------------------------------------------------------------------
# WF002 End-to-End Integration Proof & Exact 10% Boundary Regression Test
# ---------------------------------------------------------------------------

def test_wf002_end_to_end_integration_proof():
    """Integration proof for WF002 using product_catalog.csv and vendor_prices.csv.
    
    Verifies:
    1. Both files loaded
    2. Datasets joined on SKU
    3. Percentage difference calculated deterministically
    4. > 10% exception filter applied correctly
    5. Final status is SUCCESS with full trace
    """
    from app.workflow.loader import load_workbook

    workbook_path = Path("data/workflows_working.xlsx")
    workflows = load_workbook(workbook_path)

    wf_registry = WorkflowRegistry()
    for wf in workflows:
        wf_registry.register(wf)

    assert wf_registry.exists("WF002") is True

    executor = GenericWorkflowExecutor(
        registry=wf_registry,
        tool_registry=default_tool_registry,
    )

    params = {
        "product_file": "data/product_catalog.csv",
        "vendor_file": "data/vendor_prices.csv",
    }
    result = executor.execute("WF002", params)

    assert result.status == ExecutionStatus.SUCCESS
    assert len(result.step_results) == 5

    for sr in result.step_results:
        assert sr.status == StepExecutionStatus.COMPLETED

    # Check Step 4 pct_diff derivations
    step4_output = result.step_results[3].output
    assert isinstance(step4_output, list)
    assert len(step4_output) == 8
    for row in step4_output:
        assert "pct_diff" in row
        assert "vendor_vendor_price" in row

    # Step 5 filters strictly > 10.0%
    exceptions = result.final_output
    assert isinstance(exceptions, list)
    # Exactly 6 products exceed 10% price difference
    assert len(exceptions) == 6
    for exc in exceptions:
        assert exc["pct_diff"] > 10.0

    # Ensure items with <= 10% difference (SKU-1004: 9.43%, SKU-1008: 1.14%) are excluded
    flagged_skus = {e["sku"] for e in exceptions}
    assert "SKU-1004" not in flagged_skus
    assert "SKU-1008" not in flagged_skus
    assert "SKU-1001" in flagged_skus


def test_wf002_exact_10_percent_boundary_regression(tmp_path):
    """Regression test for the exact 10% boundary condition in WF002.
    
    Verifies that the condition is strictly > 10% (greater than), and NOT >= 10%:
    - Exact 10.00% difference must NOT be flagged (> 10.0 is False)
    - 10.01% difference MUST be flagged (> 10.0 is True)
    - 9.99% difference must NOT be flagged (> 10.0 is False)
    """
    import csv
    from app.workflow.loader import load_workbook

    # Create synthetic test files targeting exact 10% boundaries
    # Vendor price = 100.00 for all
    vendor_csv = tmp_path / "test_vendor_prices.csv"
    with open(vendor_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "vendor_name", "vendor_price", "lead_time_days"])
        writer.writerow(["BND-EXACT-HIGH", "Vendor A", "100.00", "5"])
        writer.writerow(["BND-JUST-OVER", "Vendor A", "100.00", "5"])
        writer.writerow(["BND-JUST-UNDER", "Vendor A", "100.00", "5"])
        writer.writerow(["BND-EXACT-LOW", "Vendor A", "100.00", "5"])
        writer.writerow(["BND-JUST-LOWER", "Vendor A", "100.00", "5"])

    # Our prices:
    # 1. 110.00 -> |110.00 - 100.00| / 100 = exactly 10.00%
    # 2. 110.01 -> |110.01 - 100.00| / 100 = 10.01%
    # 3. 109.99 -> |109.99 - 100.00| / 100 = 9.99%
    # 4. 90.00  -> |90.00 - 100.00| / 100  = exactly 10.00%
    # 5. 89.99  -> |89.99 - 100.00| / 100  = 10.01%
    product_csv = tmp_path / "test_product_catalog.csv"
    with open(product_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["sku", "product_name", "category", "unit_price"])
        writer.writerow(["BND-EXACT-HIGH", "Exact 10 Percent High", "Testing", "110.00"])
        writer.writerow(["BND-JUST-OVER", "Just Over 10 Percent", "Testing", "110.01"])
        writer.writerow(["BND-JUST-UNDER", "Just Under 10 Percent", "Testing", "109.99"])
        writer.writerow(["BND-EXACT-LOW", "Exact 10 Percent Low", "Testing", "90.00"])
        writer.writerow(["BND-JUST-LOWER", "Just Lower Than Neg 10", "Testing", "89.99"])

    workbook_path = Path("data/workflows_working.xlsx")
    workflows = load_workbook(workbook_path)
    wf_registry = WorkflowRegistry()
    for wf in workflows:
        wf_registry.register(wf)

    executor = GenericWorkflowExecutor(
        registry=wf_registry,
        tool_registry=default_tool_registry,
    )

    result = executor.execute("WF002", {
        "product_file": str(product_csv),
        "vendor_file": str(vendor_csv),
    })

    assert result.status == ExecutionStatus.SUCCESS

    # Step 4: Verify percentage difference calculations
    step4_records = {r["sku"]: r["pct_diff"] for r in result.step_results[3].output}
    assert step4_records["BND-EXACT-HIGH"] == 10.0
    assert step4_records["BND-JUST-OVER"] == 10.01
    assert step4_records["BND-JUST-UNDER"] == 9.99
    assert step4_records["BND-EXACT-LOW"] == 10.0
    assert step4_records["BND-JUST-LOWER"] == 10.01

    # Step 5: Verify > 10.0 boundary filtering
    flagged_skus = {r["sku"] for r in result.final_output}

    # MUST be flagged (> 10.0% is True)
    assert "BND-JUST-OVER" in flagged_skus
    assert "BND-JUST-LOWER" in flagged_skus

    # MUST NOT be flagged (exact 10.0% > 10.0 is False, and 9.99% > 10.0 is False)
    assert "BND-EXACT-HIGH" not in flagged_skus, "Exact 10.00% should NOT be flagged with operator '>'"
    assert "BND-EXACT-LOW" not in flagged_skus, "Exact 10.00% should NOT be flagged with operator '>'"
    assert "BND-JUST-UNDER" not in flagged_skus, "9.99% should NOT be flagged with operator '>'"
    assert len(flagged_skus) == 2
