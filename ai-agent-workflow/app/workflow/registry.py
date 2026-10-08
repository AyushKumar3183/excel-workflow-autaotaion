"""Workflow Registry for dynamic catalog management.

Stores and indexes validated WorkflowDefinition objects dynamically without hardcoding
any specific workflow IDs, logic branches, or workflow classes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.workflow.loader import load_workbook
from app.workflow.models import WorkflowDefinition


class WorkflowRegistry:
    """In-memory dynamic registry for validated workflow definitions."""

    def __init__(self) -> None:
        self._workflows: Dict[str, WorkflowDefinition] = {}

    def register(self, workflow: WorkflowDefinition) -> None:
        """Register a validated workflow definition.
        
        Overwrites any previous registration with the same workflow_id.
        """
        if not isinstance(workflow, WorkflowDefinition):
            raise TypeError(
                f"Expected WorkflowDefinition instance, got {type(workflow).__name__}"
            )
        self._workflows[workflow.workflow_id] = workflow

    def get(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Retrieve a workflow definition by its ID, or None if not found."""
        return self._workflows.get(workflow_id)

    def get_or_raise(self, workflow_id: str) -> WorkflowDefinition:
        """Retrieve a workflow definition by its ID or raise KeyError if missing."""
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            raise KeyError(f"Workflow with ID '{workflow_id}' not found in registry.")
        return workflow

    def exists(self, workflow_id: str) -> bool:
        """Check if a workflow exists in the registry."""
        return workflow_id in self._workflows

    def list_workflows(self) -> List[WorkflowDefinition]:
        """Return all registered workflow definitions."""
        return list(self._workflows.values())

    def catalog(self) -> List[Dict[str, Any]]:
        """Return a structured summary catalog for routing and LLM agent discovery."""
        return [
            {
                "workflow_id": wf.workflow_id,
                "name": wf.name,
                "trigger": wf.trigger,
                "inputs": [inp.model_dump() for inp in wf.inputs],
                "expected_output": wf.expected_output,
                "step_count": len(wf.steps),
                "tools_required": wf.tools_required,
                "decision_logic": wf.decision_logic,
            }
            for wf in self._workflows.values()
        ]

    def load_from_workbook(self, workbook_path: Union[str, Path]) -> int:
        """Load and register all workflows from an Excel workbook.
        
        Returns the count of loaded workflows.
        """
        definitions = load_workbook(workbook_path)
        for wf in definitions:
            self.register(wf)
        return len(definitions)

    def clear(self) -> None:
        """Clear all registered workflows."""
        self._workflows.clear()

    def count(self) -> int:
        """Return the number of registered workflows."""
        return len(self._workflows)

    def __len__(self) -> int:
        return len(self._workflows)

    def __contains__(self, workflow_id: str) -> bool:
        return workflow_id in self._workflows


# Global default registry instance
default_registry = WorkflowRegistry()


def get_registry() -> WorkflowRegistry:
    """Return the global default workflow registry."""
    return default_registry


def reset_registry() -> None:
    """Reset the global default workflow registry."""
    default_registry.clear()
