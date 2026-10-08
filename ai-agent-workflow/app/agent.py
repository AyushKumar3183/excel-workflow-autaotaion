"""Google ADK agent discovery entry point.

Exposes `root_agent` for Google ADK discovery (`adk web`).
"""

from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from app.agent.router_agent import RouterAgent
from app.workflow.registry import default_registry

WORKBOOK_PATH = Path(__file__).resolve().parent.parent / "data" / "workflows_working.xlsx"
if WORKBOOK_PATH.exists() and default_registry.count() == 0:
    default_registry.load_from_workbook(WORKBOOK_PATH)

router = RouterAgent(registry=default_registry)
root_agent = router.get_adk_agent()
agent = root_agent

__all__ = ["root_agent", "agent", "router"]
