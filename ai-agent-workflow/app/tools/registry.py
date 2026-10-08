"""Tool Registry for dynamic tool discovery, registration, and invocation.

Provides a decoupled, extensible registry where tools can be looked up and executed
by name without any workflow-specific branching.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional


class ToolError(Exception):
    """Base exception for all tool registry and invocation errors."""


class ToolNotFoundError(ToolError):
    """Raised when an unregistered tool is requested."""


class ToolParameterError(ToolError):
    """Raised when a tool is called with invalid, missing, or incompatible arguments."""


class ToolExecutionError(ToolError):
    """Raised when an error occurs during tool execution."""


class ToolRegistry:
    """In-memory dynamic registry of callable tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, Callable[..., Any]] = {}

    def register(self, name: str, func: Callable[..., Any]) -> None:
        """Register a tool callable under a unique name.
        
        Args:
            name: The canonical tool identifier (e.g., 'filter_rows').
            func: A callable object.
        """
        if not name or not isinstance(name, str):
            raise ValueError("Tool name must be a non-empty string.")
        if not callable(func):
            raise TypeError(f"Tool '{name}' must be a callable object, got {type(func).__name__}.")
        self._tools[name.strip()] = func

    def get(self, name: str) -> Optional[Callable[..., Any]]:
        """Retrieve a tool callable by name, or None if not registered."""
        return self._tools.get(name.strip()) if name else None

    def get_or_raise(self, name: str) -> Callable[..., Any]:
        """Retrieve a tool callable by name or raise ToolNotFoundError."""
        clean_name = name.strip() if name else ""
        tool = self._tools.get(clean_name)
        if tool is None:
            raise ToolNotFoundError(
                f"Tool '{clean_name}' is not registered. Available tools: {sorted(self._tools.keys())}"
            )
        return tool

    def exists(self, name: str) -> bool:
        """Check if a tool is registered."""
        return bool(name and name.strip() in self._tools)

    def list_tools(self) -> List[str]:
        """Return a sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def execute(self, name: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Execute a registered tool by name with the given parameters dictionary.
        
        Args:
            name: Name of the registered tool.
            params: Dictionary of parameters passed as keyword arguments.
            
        Returns:
            The output returned by the tool function.
            
        Raises:
            ToolNotFoundError: If the tool does not exist.
            ToolParameterError: If parameters are invalid or missing.
            ToolExecutionError: If an unhandled exception occurs inside the tool.
        """
        tool_func = self.get_or_raise(name)
        call_params = params or {}
        if not isinstance(call_params, dict):
            raise ToolParameterError(
                f"Tool '{name}' expects a parameter dictionary, got {type(call_params).__name__}."
            )

        # Inspect parameters to provide clean error messages for missing/unexpected args
        sig = inspect.signature(tool_func)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

        if not has_var_kw:
            unknown_args = set(call_params.keys()) - set(sig.parameters.keys())
            if unknown_args:
                raise ToolParameterError(
                    f"Tool '{name}' received unexpected arguments: {sorted(unknown_args)}. "
                    f"Expected arguments: {list(sig.parameters.keys())}"
                )

        try:
            return tool_func(**call_params)
        except TypeError as te:
            raise ToolParameterError(f"Argument mismatch executing tool '{name}': {te}") from te
        except ToolError:
            raise
        except Exception as e:
            raise ToolExecutionError(f"Error executing tool '{name}': {e}") from e

    def clear(self) -> None:
        """Clear all registered tools."""
        self._tools.clear()

    def count(self) -> int:
        """Return number of registered tools."""
        return len(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return self.exists(name)


# Global default tool registry
default_tool_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    """Return the global default tool registry."""
    return default_tool_registry
