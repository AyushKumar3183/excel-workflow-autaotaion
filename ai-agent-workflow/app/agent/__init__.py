"""Agent layer: Google ADK workflow router agent and intent routing engine."""

from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from app.agent.router_agent import (
    IntentRouterEngine,
    RouterAgent,
    RouterResponse,
    RoutingDecision,
    SessionState,
    build_router_instruction,
)
from app.workflow.registry import default_registry

WORKBOOK_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "workflows_working.xlsx"
if WORKBOOK_PATH.exists() and default_registry.count() == 0:
    default_registry.load_from_workbook(WORKBOOK_PATH)

router = RouterAgent(registry=default_registry)
root_agent = router.get_adk_agent()
agent = root_agent

__all__ = [
    "IntentRouterEngine",
    "RouterAgent",
    "RouterResponse",
    "RoutingDecision",
    "SessionState",
    "build_router_instruction",
    "router",
    "root_agent",
    "agent",
]
