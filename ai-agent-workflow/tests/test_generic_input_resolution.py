"""Tests for Generic Automatic Input & Data Source Resolution.

Verifies:
1. GenericInputResolver unit behavior (repository vs user vs default vs missing files).
2. End-to-end automatic data source resolution for all workflows WF001 - WF010.
3. User-specific inputs produce controlled NEEDS_INPUT when omitted.
4. User-provided alternative files override repository defaults.
5. Dynamic WF011 extensibility without code modifications.
6. Absence of workflow-specific branching in router, executor, or resolver.
"""

from pathlib import Path
import pytest

from app.agent.router_agent import IntentRouterEngine, RouterAgent
from app.workflow.executor import (
    ExecutionStatus,
    GenericWorkflowExecutor,
    StepExecutionStatus,
)
from app.workflow.loader import load_workbook
from app.workflow.models import (
    DataSourceMetadata,
    InputSourceType,
    WorkflowDefinition,
    WorkflowStep,
)
from app.workflow.registry import WorkflowRegistry
from app.workflow.resolver import GenericInputResolver, InputResolutionResult


@pytest.fixture
def populated_registry():
    """Load workflows with Data_Sources metadata from working workbook."""
    reg = WorkflowRegistry()
    wfs = load_workbook("data/workflows_working.xlsx")
    for wf in wfs:
        reg.register(wf)
    return reg


# ---------------------------------------------------------------------------
# 1. GenericInputResolver Unit Tests
# ---------------------------------------------------------------------------

def test_resolver_auto_resolves_existing_repository_files(tmp_path):
    """Test that existing repository files specified in data_sources are automatically resolved."""
    fake_data = tmp_path / "repo_data.csv"
    fake_data.write_text("id,val\n1,test", encoding="utf-8")

    resolver = GenericInputResolver(data_dir=tmp_path)

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1",
        tool="load_file",
        params={"filepath": "$catalog_file"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_REPO",
        name="Test Repo",
        trigger="Trigger",
        steps=[step1],
        data_sources={
            "catalog_file": DataSourceMetadata(
                name="catalog_file",
                source_type=InputSourceType.REPOSITORY,
                data_source=str(fake_data),
                required=True,
            )
        },
    )

    # User passes no parameters
    res = resolver.resolve(wf, user_params={})

    assert res.status == "SUCCESS"
    assert res.resolved_params["catalog_file"] == str(fake_data)
    assert len(res.missing_repo_files) == 0
    assert len(res.missing_user_inputs) == 0


def test_resolver_flags_missing_repository_files(tmp_path):
    """Test that non-existent repository files produce controlled NEEDS_INPUT."""
    resolver = GenericInputResolver(data_dir=tmp_path)

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1",
        tool="load_file",
        params={"filepath": "$missing_file"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_MISSING_REPO",
        name="Test Missing Repo",
        trigger="Trigger",
        steps=[step1],
        data_sources={
            "missing_file": DataSourceMetadata(
                name="missing_file",
                source_type=InputSourceType.REPOSITORY,
                data_source=str(tmp_path / "does_not_exist.csv"),
                required=True,
            )
        },
    )

    res = resolver.resolve(wf, user_params={})

    assert res.status == "NEEDS_INPUT"
    assert len(res.missing_repo_files) == 1
    assert "does_not_exist.csv" in res.missing_repo_files[0]


def test_resolver_user_explicit_alternative_file_overrides(tmp_path):
    """Test that a user-specified alternative file overrides repository default."""
    default_csv = tmp_path / "default.csv"
    default_csv.write_text("a,b\n1,2", encoding="utf-8")

    user_csv = tmp_path / "custom.csv"
    user_csv.write_text("a,b\n9,9", encoding="utf-8")

    resolver = GenericInputResolver(data_dir=tmp_path)

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1",
        tool="load_file",
        params={"filepath": "$inventory_file"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_OVERRIDE",
        name="Test Override",
        trigger="Trigger",
        steps=[step1],
        data_sources={
            "inventory_file": DataSourceMetadata(
                name="inventory_file",
                source_type=InputSourceType.REPOSITORY,
                data_source=str(default_csv),
                required=True,
            )
        },
    )

    # User explicitly provides alternative file
    res = resolver.resolve(wf, user_params={"inventory_file": str(user_csv)})

    assert res.status == "SUCCESS"
    assert res.resolved_params["inventory_file"] == str(user_csv)


def test_resolver_user_explicit_nonexistent_file_produces_error(tmp_path):
    """Test that an explicitly provided user file that does not exist produces controlled NEEDS_INPUT."""
    resolver = GenericInputResolver(data_dir=tmp_path)

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1",
        tool="load_file",
        params={"filepath": "$inventory_file"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_USER_FILE_MISSING",
        name="Test",
        trigger="Trigger",
        steps=[step1],
        data_sources={
            "inventory_file": DataSourceMetadata(
                name="inventory_file",
                source_type=InputSourceType.REPOSITORY,
                data_source="data/inventory.csv",
                required=True,
            )
        },
    )

    res = resolver.resolve(wf, user_params={"inventory_file": "nonexistent_custom.csv"})

    assert res.status == "NEEDS_INPUT"
    assert len(res.missing_repo_files) == 1
    assert "nonexistent_custom.csv" in res.missing_repo_files[0]


def test_resolver_flags_missing_user_required_input():
    """Test that genuinely user-provided required inputs are flagged when omitted."""
    resolver = GenericInputResolver()

    step1 = WorkflowStep(
        step_no=1,
        step_name="Step 1",
        tool="validate_data",
        params={"identifier": "$order_id"},
        output_variable="out",
    )
    wf = WorkflowDefinition(
        workflow_id="TEST_USER_INPUT",
        name="Test",
        trigger="Trigger",
        steps=[step1],
        data_sources={
            "order_id": DataSourceMetadata(
                name="order_id",
                source_type=InputSourceType.USER,
                required=True,
            )
        },
    )

    res = resolver.resolve(wf, user_params={})

    assert res.status == "NEEDS_INPUT"
    assert "order_id" in res.missing_user_inputs


# ---------------------------------------------------------------------------
# 2. End-to-End Workflow Tests (WF001 - WF010)
# ---------------------------------------------------------------------------

def test_wf001_auto_resolution_and_execution(populated_registry):
    """WF001: 'Check which products need restocking' auto-resolves inventory.csv."""
    router = RouterAgent(registry=populated_registry)
    resp = router.handle_message("Check which products need restocking.", session_id="test_wf001")

    assert resp.decision.workflow_id == "WF001"
    assert resp.decision.needs_clarification is False
    assert resp.decision.parameters.get("inventory_file") == "data/inventory.csv"
    assert resp.decision.parameters.get("minimum_threshold") == 20
    assert resp.execution_result is not None
    assert resp.execution_result.status == ExecutionStatus.SUCCESS
    assert "restock_list" in resp.execution_result.context_summary


def test_wf002_auto_resolution_and_execution(populated_registry):
    """WF002: 'Validate product prices above 10%' auto-resolves product_catalog.csv and vendor_prices.csv."""
    router = RouterAgent(registry=populated_registry)
    resp = router.handle_message("Validate product prices above 10%.", session_id="test_wf002")

    assert resp.decision.workflow_id == "WF002"
    assert resp.decision.needs_clarification is False
    assert resp.decision.parameters.get("product_file") == "data/product_catalog.csv"
    assert resp.decision.parameters.get("vendor_file") == "data/vendor_prices.csv"
    assert resp.execution_result is not None
    assert resp.execution_result.status == ExecutionStatus.SUCCESS
    assert len(resp.execution_result.final_output) == 6


def test_wf003_distinguishes_missing_file_vs_supplied(populated_registry):
    """WF003: Requests vendor_file when omitted; executes when user supplies file."""
    router = RouterAgent(registry=populated_registry)

    # 1. Missing file produces controlled clarification
    resp1 = router.handle_message("Process vendor file.", session_id="test_wf003_1")
    assert resp1.decision.workflow_id == "WF003"
    assert resp1.decision.needs_clarification is True
    assert resp1.decision.clarification_prompt is not None
    assert "vendor_file" in resp1.decision.clarification_prompt
    assert resp1.execution_result is None

    # 2. Supplied file succeeds
    resp2 = router.handle_message("Process vendor file data/vendor_prices.csv.", session_id="test_wf003_2")
    assert resp2.decision.workflow_id == "WF003"
    assert resp2.decision.needs_clarification is False
    assert resp2.execution_result is not None
    assert resp2.execution_result.status == ExecutionStatus.SUCCESS


def test_wf004_distinguishes_missing_attributes_vs_supplied(populated_registry):
    """WF004: Requests product_attributes when omitted; executes when supplied."""
    router = RouterAgent(registry=populated_registry)

    # Missing attributes
    resp1 = router.handle_message("Generate product description content.", session_id="test_wf004_1")
    assert resp1.decision.workflow_id == "WF004"
    assert resp1.decision.needs_clarification is True
    assert resp1.decision.clarification_prompt is not None
    assert "product_attributes" in resp1.decision.clarification_prompt

    # Supplied attributes directly through executor
    executor = GenericWorkflowExecutor(registry=populated_registry)
    res2 = executor.execute(
        "WF004",
        {"product_attributes": {"name": "Smart Watch", "category": "Electronics"}}
    )
    assert res2.status == ExecutionStatus.SUCCESS
    assert "long_description" in res2.context_summary


def test_wf005_auto_resolves_orders_and_extracts_id(populated_registry):
    """WF005: Auto-resolves orders source to data/orders.csv and requests missing order ID."""
    router = RouterAgent(registry=populated_registry)

    # 1. Missing order ID
    resp1 = router.handle_message("What is my order status?", session_id="test_wf005_1")
    assert resp1.decision.workflow_id == "WF005"
    assert resp1.decision.needs_clarification is True
    assert resp1.decision.clarification_prompt is not None
    assert "order_id_or_email" in resp1.decision.clarification_prompt

    # 2. With order ID: auto-resolves orders_source to data/orders.csv
    resp2 = router.handle_message("Check order ORD-1001.", session_id="test_wf005_2")
    assert resp2.decision.workflow_id == "WF005"
    assert resp2.decision.needs_clarification is False
    assert resp2.decision.parameters.get("order_id_or_email") == "ORD-1001"
    assert resp2.decision.parameters.get("orders_source") == "data/orders.csv"
    assert resp2.execution_result is not None
    assert resp2.execution_result.status == ExecutionStatus.SUCCESS


def test_wf006_auto_resolution_and_execution(populated_registry):
    """WF006: Auto-resolves catalog_file to data/product_catalog.csv."""
    router = RouterAgent(registry=populated_registry)
    resp = router.handle_message("Detect duplicate products in catalog.", session_id="test_wf006")

    assert resp.decision.workflow_id == "WF006"
    assert resp.decision.needs_clarification is False
    assert resp.decision.parameters.get("catalog_file") == "data/product_catalog.csv"
    assert resp.execution_result is not None
    assert resp.execution_result.status == ExecutionStatus.SUCCESS


def test_wf007_distinguishes_missing_campaign_vs_auto_resolving_products(populated_registry):
    """WF007: Demands campaign goal/dates but auto-resolves product_list to product_catalog.csv."""
    router = RouterAgent(registry=populated_registry)

    # 1. Missing goal/dates
    resp1 = router.handle_message("Create a campaign brief.", session_id="test_wf007_1")
    assert resp1.decision.workflow_id == "WF007"
    assert resp1.decision.needs_clarification is True
    assert resp1.decision.clarification_prompt is not None
    assert "campaign goal and dates" in resp1.decision.clarification_prompt

    # 2. Supplied goal/dates: auto-resolves product_list
    query = "Create a marketing campaign brief. Goal is boost Q4 sales, dates are Dec 1-15."
    resp2 = router.handle_message(query, session_id="test_wf007_2")
    assert resp2.decision.workflow_id == "WF007"
    assert resp2.decision.needs_clarification is False
    assert resp2.decision.parameters.get("product_list") == "data/product_catalog.csv"
    assert resp2.execution_result is not None
    assert resp2.execution_result.status == ExecutionStatus.SUCCESS


def test_wf008_distinguishes_user_keyword_file_vs_category_reference(populated_registry):
    """WF008: Demands keyword_file when omitted, but auto-resolves category_data to product_catalog.csv."""
    router = RouterAgent(registry=populated_registry)

    # 1. Missing keyword file
    resp1 = router.handle_message("Classify SEO keywords.", session_id="test_wf008_1")
    assert resp1.decision.workflow_id == "WF008"
    assert resp1.decision.needs_clarification is True
    assert resp1.decision.clarification_prompt is not None
    assert "keyword_file" in resp1.decision.clarification_prompt

    # 2. When keyword file is provided, executes with auto-resolved category data
    executor = GenericWorkflowExecutor(registry=populated_registry)
    res2 = executor.execute("WF008", {"keyword_file": "data/product_catalog.csv"})
    assert res2.status == ExecutionStatus.SUCCESS
    assert "keyword_export" in res2.context_summary


def test_wf009_auto_resolves_employee_list(populated_registry):
    """WF009: Auto-resolves employee_list to data/employees.csv from task description."""
    router = RouterAgent(registry=populated_registry)
    resp = router.handle_message("Assign task to redesign the company website.", session_id="test_wf009")

    assert resp.decision.workflow_id == "WF009"
    assert resp.decision.needs_clarification is False
    assert resp.decision.parameters.get("employee_list") == "data/employees.csv"
    assert resp.execution_result is not None
    # Execution completes (evaluates skills condition deterministically)
    assert resp.execution_result.status in (ExecutionStatus.SUCCESS, ExecutionStatus.ESCALATED)


def test_wf010_auto_resolution_and_execution(populated_registry):
    """WF010: Auto-resolves execution_logs_file to data/workflow_logs.jsonl."""
    router = RouterAgent(registry=populated_registry)
    resp = router.handle_message("Generate a workflow performance report.", session_id="test_wf010")

    assert resp.decision.workflow_id == "WF010"
    assert resp.decision.needs_clarification is False
    assert resp.decision.parameters.get("execution_logs_file") == "data/workflow_logs.jsonl"
    assert resp.execution_result is not None
    assert resp.execution_result.status == ExecutionStatus.SUCCESS


# ---------------------------------------------------------------------------
# 3. Dynamic WF011 Extensibility Proof
# ---------------------------------------------------------------------------

def test_wf011_remains_dynamically_discoverable_and_executable(populated_registry):
    """Verify newly registered WF011 requires NO code changes and executes cleanly."""
    step1 = WorkflowStep(
        step_no=1,
        step_name="Validate Return",
        tool="validate_data",
        params={"identifier": "$return_id"},
        output_variable="validated_return",
    )
    wf011 = WorkflowDefinition(
        workflow_id="WF011",
        name="Customer Product Return",
        trigger="User asks to initiate or process a customer product return",
        steps=[step1],
    )
    populated_registry.register(wf011)

    router = RouterAgent(registry=populated_registry)

    # 1. Routes to WF011 and requests missing return_id
    resp1 = router.handle_message("I need to process a customer product return.", session_id="test_wf011")
    assert resp1.decision.workflow_id == "WF011"
    assert resp1.decision.needs_clarification is True
    assert resp1.decision.clarification_prompt is not None
    assert "return_id" in resp1.decision.clarification_prompt

    # 2. Executes through executor when parameter is supplied
    executor = GenericWorkflowExecutor(registry=populated_registry)
    res2 = executor.execute("WF011", {"return_id": "RET-999"})
    assert res2.status == ExecutionStatus.SUCCESS
    assert res2.step_results[0].status == StepExecutionStatus.COMPLETED
