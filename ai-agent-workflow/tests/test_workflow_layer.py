"""Unit tests for the workflow definition layer.

Tests cover:
- Workbook loading from the working copy
- Workflow count and schema verification
- Workflow lookup and catalog formatting
- Duplicate workflow ID detection
- Invalid workflow ID detection
- Invalid step numbers detection
- Bindings referencing unknown workflows
- Bindings referencing unknown steps
- Missing required step bindings
- Registry lookup, existence check, and dynamic catalog
"""

import json
from pathlib import Path
import openpyxl
import pytest

from app.workflow.models import (
    Condition,
    StepBinding,
    WorkflowDefinition,
    WorkflowInput,
    WorkflowStep,
)
from app.workflow.loader import (
    WorkflowLoader,
    WorkflowLoadError,
    WorkflowValidationError,
    load_workbook,
)
from app.workflow.registry import WorkflowRegistry


WORKING_WORKBOOK_PATH = Path("data/workflows_working.xlsx")
ORIGINAL_WORKBOOK_PATH = Path("data/workflows.xlsx")


# ---------------------------------------------------------------------------
# Model Unit Tests
# ---------------------------------------------------------------------------

def test_condition_model_valid():
    """Test valid condition instantiations and parsing."""
    cond1 = Condition(field="current_stock", operator="<", value=10, action_on_true="STOP")
    assert cond1.field == "current_stock"
    assert cond1.operator == "<"
    assert cond1.action_on_true == "STOP"

    cond2 = Condition.from_raw('{"field": "status", "operator": "==", "value": "shipped"}')
    assert cond2 is not None
    assert cond2.field == "status"
    assert cond2.operator == "=="

    assert Condition.from_raw(None) is None
    assert Condition.from_raw("") is None


def test_condition_model_invalid_operator():
    """Test that unsupported condition operators raise ValueError."""
    with pytest.raises(ValueError, match="Invalid condition operator"):
        Condition(field="x", operator="~~~", value=1)


def test_condition_model_invalid_action():
    """Test that unsupported condition actions raise ValueError."""
    with pytest.raises(ValueError, match="Invalid condition action"):
        Condition(field="x", operator="==", value=1, action_on_true="BLOW_UP")


def test_workflow_step_sequence_validation():
    """Test that WorkflowDefinition rejects non-contiguous step numbers."""
    step1 = WorkflowStep(
        step_no=1, step_name="Step 1", tool="load_file", output_variable="out1"
    )
    step3 = WorkflowStep(
        step_no=3, step_name="Step 3", tool="filter_rows", output_variable="out3"
    )
    with pytest.raises(ValueError, match="Steps must be contiguous and start at 1"):
        WorkflowDefinition(
            workflow_id="WF001",
            name="Test WF",
            trigger="Test trigger",
            steps=[step1, step3],
        )


# ---------------------------------------------------------------------------
# Workbook Loading & Verification Tests
# ---------------------------------------------------------------------------

def test_load_working_workbook():
    """Test loading workflows from the validated working copy."""
    workflows = load_workbook(WORKING_WORKBOOK_PATH)
    assert len(workflows) == 10
    
    expected_ids = [f"WF{i:03d}" for i in range(1, 11)]
    loaded_ids = [wf.workflow_id for wf in workflows]
    assert loaded_ids == expected_ids

    # Check WF001 specifically
    wf001 = next(wf for wf in workflows if wf.workflow_id == "WF001")
    assert wf001.name == "Inventory Restock Check"
    assert len(wf001.steps) == 5
    assert len(wf001.inputs) == 2
    assert wf001.steps[0].tool == "load_file"
    assert wf001.steps[0].step_no == 1


def test_missing_required_sheet_detection():
    """Test that loading a workbook without Step_Bindings raises WorkflowValidationError."""
    # The original workflows.xlsx does not contain Step_Bindings
    with pytest.raises(WorkflowValidationError, match="Missing required sheet"):
        load_workbook(ORIGINAL_WORKBOOK_PATH)


def test_nonexistent_workbook():
    """Test that loading a non-existent workbook raises WorkflowLoadError."""
    with pytest.raises(WorkflowLoadError, match="does not exist"):
        load_workbook("data/does_not_exist.xlsx")


# ---------------------------------------------------------------------------
# Validation Rule Tests using Temporary In-Memory Workbooks
# ---------------------------------------------------------------------------

def _create_base_workbook():
    wb = openpyxl.Workbook()
    ws_wf = wb.active
    assert ws_wf is not None
    ws_wf.title = "Workflows"
    ws_wf.append([
        "Workflow_ID", "Workflow_Name", "Trigger", "Inputs",
        "Steps", "Decision_Logic", "Tools_Required", "Expected_Output"
    ])
    ws_wf.append([
        "WF001", "Inventory Restock Check", "Trigger test", "inventory.csv",
        "Step One → Step Two", "Logic test", "CSV reader", "Report"
    ])

    ws_bind = wb.create_sheet(title="Step_Bindings")
    ws_bind.append([
        "workflow_id", "step_no", "tool", "params",
        "output_variable", "condition", "on_error"
    ])
    ws_bind.append(["WF001", 1, "load_file", "{}", "out1", "", "FAIL"])
    ws_bind.append(["WF001", 2, "filter_rows", "{}", "out2", "", "FAIL"])
    return wb


def test_duplicate_workflow_id_detection(tmp_path):
    """Test that duplicate workflow IDs in Workflows sheet are detected."""
    wb = _create_base_workbook()
    ws_wf = wb["Workflows"]
    # Append duplicate WF001
    ws_wf.append([
        "WF001", "Duplicate Inventory", "Trigger 2", "inv.csv",
        "Step One → Step Two", "Logic", "CSV", "Report"
    ])
    path = tmp_path / "dup_wf.xlsx"
    wb.save(path)

    with pytest.raises(WorkflowValidationError, match="Duplicate workflow ID found: 'WF001'"):
        load_workbook(path)


def test_invalid_workflow_id_format(tmp_path):
    """Test that invalid workflow ID format is detected."""
    wb = _create_base_workbook()
    ws_wf = wb["Workflows"]
    ws_wf.cell(row=2, column=1, value="INVALID_99")
    path = tmp_path / "invalid_id.xlsx"
    wb.save(path)

    with pytest.raises(WorkflowValidationError, match="Invalid workflow ID format"):
        load_workbook(path)


def test_bindings_referencing_unknown_workflow(tmp_path):
    """Test that bindings referencing an unknown workflow ID are detected."""
    wb = _create_base_workbook()
    ws_bind = wb["Step_Bindings"]
    # Append binding for unknown WF999
    ws_bind.append(["WF999", 1, "load_file", "{}", "out", "", "FAIL"])
    path = tmp_path / "unknown_wf_binding.xlsx"
    wb.save(path)

    with pytest.raises(WorkflowValidationError, match="references unknown workflow: 'WF999'"):
        load_workbook(path)


def test_invalid_step_number(tmp_path):
    """Test that negative or non-integer step number in bindings is detected."""
    wb = _create_base_workbook()
    ws_bind = wb["Step_Bindings"]
    # Set step_no to 0
    ws_bind.cell(row=2, column=2, value=0)
    path = tmp_path / "invalid_step_no.xlsx"
    wb.save(path)

    with pytest.raises(WorkflowValidationError, match="Invalid step number 0"):
        load_workbook(path)


def test_binding_referencing_unknown_step(tmp_path):
    """Test that binding step number exceeding defined workflow steps is detected."""
    wb = _create_base_workbook()
    ws_bind = wb["Step_Bindings"]
    # WF001 only has 2 steps defined in Steps column, but binding gives step 99
    ws_bind.append(["WF001", 99, "extra_tool", "{}", "out99", "", "FAIL"])
    path = tmp_path / "unknown_step_binding.xlsx"
    wb.save(path)

    with pytest.raises(WorkflowValidationError, match="references unknown step 99"):
        load_workbook(path)


def test_missing_required_step_bindings(tmp_path):
    """Test that a workflow with missing step bindings is detected."""
    wb = _create_base_workbook()
    ws_bind = wb["Step_Bindings"]
    # Remove step 2 binding, so step 2 is missing
    ws_bind.delete_rows(3, 1)
    path = tmp_path / "missing_step.xlsx"
    wb.save(path)

    with pytest.raises(WorkflowValidationError, match="Missing step bindings for workflow 'WF001'"):
        load_workbook(path)


# ---------------------------------------------------------------------------
# Registry Functionality Tests
# ---------------------------------------------------------------------------

def test_registry_lookup_and_methods():
    """Test WorkflowRegistry get, exists, list_workflows, catalog, and load_from_workbook."""
    registry = WorkflowRegistry()
    assert len(registry) == 0

    count = registry.load_from_workbook(WORKING_WORKBOOK_PATH)
    assert count == 10
    assert len(registry) == 10

    # exists
    assert registry.exists("WF001") is True
    assert registry.exists("WF010") is True
    assert registry.exists("WF999") is False

    # get
    wf001 = registry.get("WF001")
    assert wf001 is not None
    assert wf001.name == "Inventory Restock Check"

    # get non-existent
    assert registry.get("WF999") is None

    # get_or_raise
    with pytest.raises(KeyError, match="WF999"):
        registry.get_or_raise("WF999")

    # list_workflows
    all_wfs = registry.list_workflows()
    assert len(all_wfs) == 10

    # catalog
    catalog = registry.catalog()
    assert len(catalog) == 10
    first_item = catalog[0]
    assert "workflow_id" in first_item
    assert "name" in first_item
    assert "trigger" in first_item
    assert "inputs" in first_item
    assert "tools_required" in first_item
    assert "step_count" in first_item

    # clear
    registry.clear()
    assert len(registry) == 0
    assert registry.exists("WF001") is False
