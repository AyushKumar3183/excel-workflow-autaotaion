"""Unit tests for the Google ADK workflow router agent.

Tests cover:
1. Dynamic catalog generation from registry
2. WF001 routing (Inventory restock check)
3. WF002 routing (Product price validation)
4. WF005 routing (Customer order status with extracted order ID)
5. WF007 routing (Marketing campaign brief)
6. Ambiguous request clarification
7. Missing required parameter prompt
8. Unknown workflow intent handling
9. Parameter extraction accuracy
10. Execution tool delegation to GenericWorkflowExecutor
11. WF011 Scalability Proof: Catalog visibility without router code modification
12. Multi-turn session state continuation
"""

from pathlib import Path
import pytest

from app.agent.router_agent import (
    IntentRouterEngine,
    RouterAgent,
    RoutingDecision,
    build_router_instruction,
)
from app.workflow.executor import ExecutionStatus, GenericWorkflowExecutor
from app.workflow.loader import load_workbook
from app.workflow.models import WorkflowDefinition, WorkflowStep
from app.workflow.registry import WorkflowRegistry
from app.tools import default_tool_registry


@pytest.fixture
def populated_registry():
    """Load the 10 standard workflows from data/workflows_working.xlsx."""
    reg = WorkflowRegistry()
    wfs = load_workbook("data/workflows_working.xlsx")
    for wf in wfs:
        reg.register(wf)
    return reg


# ---------------------------------------------------------------------------
# Catalog Generation & Scalability Tests
# ---------------------------------------------------------------------------

def test_dynamic_catalog_generation(populated_registry):
    """Test that build_router_instruction produces catalog text for all 10 workflows."""
    instruction = build_router_instruction(populated_registry)

    assert "CURRENT DYNAMIC WORKFLOW CATALOG:" in instruction
    for i in range(1, 11):
        wf_id = f"WF{i:03d}"
        assert wf_id in instruction, f"Catalog missing {wf_id}"


def test_wf011_scalability_proof(populated_registry):
    """WF011 Extensibility Proof.
    
    Proves that registering a new WF011 definition makes it immediately visible
    in the router's catalog and prompt without modifying any router code.
    """
    # 1. Verify WF011 is not initially present
    assert populated_registry.exists("WF011") is False
    prompt_before = build_router_instruction(populated_registry)
    assert "WF011" not in prompt_before

    # 2. Add synthetic WF011 to registry
    step1 = WorkflowStep(
        step_no=1,
        step_name="Process Return",
        tool="validate_data",
        output_variable="return_validation",
    )
    wf011 = WorkflowDefinition(
        workflow_id="WF011",
        name="Customer Return Processing",
        trigger="User asks to initiate or validate a customer product return",
        steps=[step1],
    )
    populated_registry.register(wf011)

    # 3. Router instruction automatically reflects WF011
    prompt_after = build_router_instruction(populated_registry)
    assert "WF011" in prompt_after
    assert "Customer Return Processing" in prompt_after

    # 4. Router engine automatically routes to WF011
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("I need to process a customer product return for a refund.")

    assert decision.workflow_id == "WF011"
    assert decision.confidence >= 0.80


# ---------------------------------------------------------------------------
# Workflow Routing Tests
# ---------------------------------------------------------------------------

def test_route_wf001_inventory_restock(populated_registry):
    """Test routing restocking query to WF001 with default files."""
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("Which products are currently low on stock and need to be restocked?")

    assert decision.workflow_id == "WF001"
    assert decision.confidence >= 0.85
    assert decision.parameters.get("inventory_file") == "data/inventory.csv"
    assert decision.parameters.get("minimum_threshold") == 20


def test_route_wf002_price_validation(populated_registry):
    """Test routing price validation query to WF002."""
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("Validate our product prices against the vendor price list.")

    assert decision.workflow_id == "WF002"
    assert decision.confidence >= 0.85


def test_route_wf005_order_status_with_order_id(populated_registry):
    """Test routing order status request with extracted order identifier to WF005."""
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("Where is my order ORD-1002 and what is its status?")

    assert decision.workflow_id == "WF005"
    assert decision.confidence >= 0.85
    assert decision.parameters.get("order_id_or_email") == "ORD-1002"
    assert decision.needs_clarification is False


def test_route_wf007_campaign_brief(populated_registry):
    """Test routing marketing campaign brief request to WF007."""
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("Create a marketing campaign brief for Black Friday. Goal is sales boost, dates are Nov 20-30.")

    assert decision.workflow_id == "WF007"
    assert decision.confidence >= 0.85
    c_inputs = decision.parameters.get("campaign_inputs", {})
    assert "sales boost" in c_inputs.get("goal", "").lower()


# ---------------------------------------------------------------------------
# Ambiguity & Missing Parameter Handling Tests
# ---------------------------------------------------------------------------

def test_ambiguous_request_clarification(populated_registry):
    """Test that ambiguous or vague queries do not guess a workflow, but ask for clarification."""
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("Check this thing right now.")

    assert decision.needs_clarification is True
    assert decision.confidence < 0.70
    assert decision.clarification_prompt is not None


def test_missing_required_parameter_asks_user(populated_registry):
    """Test that WF005 without an order ID asks the user for the identifier."""
    engine = IntentRouterEngine(populated_registry)
    # User asks for order status but omits order ID
    decision = engine.route("What is the status of my order?")

    assert decision.workflow_id == "WF005"
    assert decision.needs_clarification is True
    assert decision.clarification_prompt is not None
    assert "order_id_or_email" in decision.clarification_prompt


def test_unknown_intent_clarification(populated_registry):
    """Test that unrelated queries return low confidence and request clarification."""
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("Tell me a funny story about cats in space.")

    assert decision.workflow_id is None
    assert decision.confidence < 0.40
    assert decision.needs_clarification is True


def test_parameter_extraction_specific_files(populated_registry):
    """Test extracting custom filenames from natural language."""
    engine = IntentRouterEngine(populated_registry)
    query = "Check inventory in data/custom_inventory.csv with a threshold of 35."
    decision = engine.route(query)

    assert decision.workflow_id == "WF001"
    assert decision.parameters.get("inventory_file") == "data/custom_inventory.csv"
    assert decision.parameters.get("minimum_threshold") == 35


# ---------------------------------------------------------------------------
# Execution Tool Delegation & Multi-turn Session Tests
# ---------------------------------------------------------------------------

def test_router_agent_execution_delegation(populated_registry):
    """Test RouterAgent executing WF001 end-to-end through GenericWorkflowExecutor."""
    router = RouterAgent(registry=populated_registry)

    response = router.handle_message("Check low stock products and generate a restock report.")

    assert response.decision.workflow_id == "WF001"
    assert response.execution_result is not None
    assert response.execution_result.status == ExecutionStatus.SUCCESS
    assert "Workflow: WF001" in response.response_text
    assert "Status: SUCCESS" in response.response_text


def test_multi_turn_session_continuation(populated_registry):
    """Test multi-turn session state: prompt for missing order ID, then complete on follow-up."""
    router = RouterAgent(registry=populated_registry)
    session_id = "test_user_session_42"

    # Turn 1: User asks without order ID
    resp1 = router.handle_message("Can you check the tracking and status of my order?", session_id=session_id)
    assert resp1.decision.needs_clarification is True
    assert resp1.execution_result is None
    assert "order_id_or_email" in resp1.response_text

    # Turn 2: User provides order ID
    resp2 = router.handle_message("ORD-1001", session_id=session_id)
    assert resp2.decision.needs_clarification is False
    assert resp2.execution_result is not None
    assert resp2.execution_result.status == ExecutionStatus.SUCCESS
    assert resp2.execution_result.final_output.get("order", {}).get("order_id") == "ORD-1001"
    assert "Delivered" in str(resp2.execution_result.final_output)


def test_adk_agent_instance(populated_registry):
    """Test that RouterAgent exposes a valid Google ADK Agent instance with tools."""
    router = RouterAgent(registry=populated_registry)
    adk_agent = router.get_adk_agent()

    assert adk_agent.name == "adk_workflow_router"
    assert len(adk_agent.tools) == 1
    tool_fn = adk_agent.tools[0]
    assert callable(tool_fn)
    assert getattr(tool_fn, "__name__", "") == "execute_workflow"

    # Direct invocation of ADK tool function delegates to executor
    res_dict = tool_fn(
        workflow_id="WF001",
        parameters={"inventory_file": "data/inventory.csv", "minimum_threshold": 20},
    )
    assert isinstance(res_dict, dict)
    assert res_dict["workflow_id"] == "WF001"
    assert res_dict["status"] == "SUCCESS"
    assert "restock_quantities" in res_dict.get("context_summary", {})


def test_missing_campaign_parameters_asks_user(populated_registry):
    """Test WF007 without campaign goal or dates requests clarification from user."""
    engine = IntentRouterEngine(populated_registry)
    decision = engine.route("Launch a new marketing campaign brief.")

    assert decision.workflow_id == "WF007"
    assert decision.needs_clarification is True
    assert decision.clarification_prompt is not None
    assert "campaign goal and dates" in decision.clarification_prompt


def test_adk_agent_discovery_with_nested_loader():
    """Verify Google ADK NestedAgentLoader discovers app and exposes root_agent via app/agent.py."""
    from google.adk.cli.utils._nested_agent_loader import NestedAgentLoader
    loader = NestedAgentLoader(".")
    agents = loader.list_agents()
    assert "app" in agents

    loaded = loader.load_agent("app")
    assert getattr(loaded, "name", "") == "adk_workflow_router"
    assert len(getattr(loaded, "tools", [])) == 1


def test_route_and_execute_wf002_with_product_catalog_and_vendor_prices(populated_registry):
    """Verify router agent handles user request specifying product_catalog.csv and vendor_prices.csv.
    
    Verifies:
    1. WF002 is selected
    2. Both files are loaded
    3. Datasets joined on SKU
    4. Price difference calculated deterministically
    5. > 10% condition applied correctly
    6. Final status is SUCCESS
    7. Full trace formatted
    """
    router = RouterAgent(registry=populated_registry)
    query = (
        "Validate the product prices using data/product_catalog.csv and data/vendor_prices.csv "
        "and identify products where the vendor price differs by more than 10%."
    )
    response = router.handle_message(query)

    assert response.decision.workflow_id == "WF002"
    assert response.decision.confidence >= 0.85
    assert response.decision.parameters.get("product_file") == "data/product_catalog.csv"
    assert response.decision.parameters.get("vendor_file") == "data/vendor_prices.csv"

    assert response.execution_result is not None
    assert response.execution_result.status == ExecutionStatus.SUCCESS
    assert len(response.execution_result.step_results) == 5

    # Exactly 6 exceptions
    exceptions = response.execution_result.final_output
    assert isinstance(exceptions, list)
    assert len(exceptions) == 6

    # Verify trace details in user response
    assert "Workflow: WF002" in response.response_text
    assert "Status: SUCCESS" in response.response_text
    assert "Step 1" in response.response_text or "Load product prices" in response.response_text
    assert "Step 5" in response.response_text or "flag exceptions" in response.response_text

