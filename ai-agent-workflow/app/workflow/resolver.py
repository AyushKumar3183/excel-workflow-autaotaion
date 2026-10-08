"""Generic, schema-driven input and data source resolver.

Resolves workflow execution parameters deterministically from the workflow definition,
distinguishing between repository data sources, user-provided inputs, defaults,
and optional parameters without any workflow-specific branching.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field

from app.workflow.models import (
    DataSourceMetadata,
    InputSourceType,
    WorkflowDefinition,
)

# Default data directory relative to repository root
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def find_param_in_dict(name: str, params: Dict[str, Any]) -> Tuple[bool, Any]:
    """Find parameter value supporting exact match, case-insensitivity, and common descriptor suffixes.
    
    Ensures generic compatibility between descriptive catalog input names (e.g. product_csv,
    vendor_price_list) and execution step variables (e.g. product_file, vendor_file).
    """
    if name in params and params[name] is not None and params[name] != "":
        return True, params[name]
    clean = name.lower()
    for k, v in params.items():
        if k.lower() == clean and v is not None and v != "":
            return True, v
    
    # Common suffix variants: _file, _csv, _list, _data, _source
    clean_base = (
        clean.replace("_file", "")
        .replace("_csv", "")
        .replace("_list", "")
        .replace("_data", "")
        .replace("_source", "")
    )
    for k, v in params.items():
        k_base = (
            k.lower()
            .replace("_file", "")
            .replace("_csv", "")
            .replace("_list", "")
            .replace("_data", "")
            .replace("_source", "")
        )
        if clean_base and k_base:
            if clean_base == k_base and v is not None and v != "":
                return True, v
            if (clean_base in k_base or k_base in clean_base) and v is not None and v != "":
                if (
                    ("product" in clean_base and "product" in k_base)
                    or ("vendor" in clean_base and "vendor" in k_base)
                    or ("inventory" in clean_base and "inventory" in k_base)
                    or ("order" in clean_base and "order" in k_base)
                    or ("employee" in clean_base and "employee" in k_base)
                    or ("log" in clean_base and "log" in k_base)
                ):
                    return True, v
    return False, None


class InputResolutionResult(BaseModel):
    """Structured outcome of input parameter resolution."""
    status: str = Field(default="SUCCESS", description="SUCCESS, NEEDS_INPUT, or FAILED")
    resolved_params: Dict[str, Any] = Field(default_factory=dict, description="Fully resolved execution parameters")
    missing_user_inputs: List[str] = Field(default_factory=list, description="Missing user-supplied required parameters")
    missing_repo_files: List[str] = Field(default_factory=list, description="Repository files specified but not found on disk")
    errors: List[str] = Field(default_factory=list, description="Diagnostic error messages")


class GenericInputResolver:
    """Generic resolver that resolves workflow inputs against data sources and user parameters."""

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

    def resolve_file_path(self, raw_path: Union[str, Path]) -> Optional[Path]:
        """Check whether a file path exists as absolute, relative to CWD, or relative to data_dir."""
        p = Path(raw_path)
        if p.is_file():
            return p
        if (self.data_dir / p.name).is_file():
            return self.data_dir / p.name
        if (self.data_dir / p).is_file():
            return self.data_dir / p
        return None

    def get_external_step_requirements(self, workflow: WorkflowDefinition) -> Set[str]:
        """Collect all variable names that steps reference externally ($var)."""
        needed: Set[str] = set()
        produced: Set[str] = set()

        def _scan(obj: Any) -> None:
            if isinstance(obj, str):
                if obj.startswith("$"):
                    root = obj[1:].split(".")[0].split("[")[0]
                    if root not in produced:
                        needed.add(root)
            elif isinstance(obj, dict):
                for val in obj.values():
                    _scan(val)
            elif isinstance(obj, list):
                for item in obj:
                    _scan(item)

        for step in workflow.steps:
            _scan(step.params)
            produced.add(step.output_variable)

        return needed

    def resolve(
        self,
        workflow: WorkflowDefinition,
        user_params: Optional[Dict[str, Any]] = None,
    ) -> InputResolutionResult:
        """Resolve inputs for a workflow.
        
        Distinguishes:
        1. repository/default input
        2. user-provided input
        3. required input with no value
        4. optional input
        5. missing repository file
        """
        provided = dict(user_params or {})
        resolved: Dict[str, Any] = {}
        missing_user_inputs: List[str] = []
        missing_repo_files: List[str] = []
        errors: List[str] = []

        # 1. Determine all inputs needed: union of workflow.data_sources, step external requirements, and declared inputs
        step_needed = self.get_external_step_requirements(workflow)
        all_param_keys: Set[str] = set(workflow.data_sources.keys()) | step_needed

        # 2. Iterate through each parameter and apply generic resolution rules
        for param_key in sorted(all_param_keys):
            ds_meta = workflow.data_sources.get(param_key)
            
            # Check if user explicitly provided this parameter
            found_user, user_val = find_param_in_dict(param_key, provided)

            # Determine source type
            source_type = ds_meta.source_type if ds_meta else None
            if source_type is None:
                # Infer source type dynamically if no explicit metadata exists
                if any(kw in param_key.lower() for kw in ("file", "catalog", "logs", "csv", "jsonl")):
                    source_type = InputSourceType.REPOSITORY
                elif any(kw in param_key.lower() for kw in ("threshold", "limit", "rate")):
                    source_type = InputSourceType.DEFAULT
                else:
                    source_type = InputSourceType.USER

            # --- CASE 1: User explicitly provided a value ---
            if found_user:
                is_file_target = (
                    source_type == InputSourceType.REPOSITORY
                    or param_key.endswith("_file")
                    or (ds_meta and ds_meta.data_source and "." in ds_meta.data_source)
                )

                if is_file_target and isinstance(user_val, (str, Path)):
                    resolved_file = self.resolve_file_path(user_val)
                    if resolved_file:
                        # User's explicit alternative file exists: use it
                        resolved[param_key] = str(user_val)
                    else:
                        # User provided a file path that does not exist
                        missing_repo_files.append(
                            f"Specified file '{user_val}' for input '{param_key}' does not exist."
                        )
                elif "campaign" in param_key and isinstance(user_val, dict):
                    # Check composite user input for required sub-keys (goal and dates)
                    if not user_val.get("goal") or not user_val.get("dates"):
                        missing_user_inputs.append("campaign goal and dates")
                    else:
                        resolved[param_key] = user_val
                elif user_val is not None and user_val != "":
                    resolved[param_key] = user_val
                else:
                    is_required = ds_meta.required if ds_meta else True
                    if is_required:
                        missing_user_inputs.append(param_key)
                continue

            # --- CASE 2: User did NOT provide a value ---
            if source_type == InputSourceType.REPOSITORY:
                # Resolve repository file path from metadata or candidate search
                candidate_path = ds_meta.data_source if (ds_meta and ds_meta.data_source) else None
                if not candidate_path:
                    # Look for data/<param_key>.csv or similar
                    clean_name = param_key.replace("_file", "").replace("_source", "")
                    for ext in (".csv", ".jsonl", ".xlsx"):
                        test_file = self.data_dir / f"{clean_name}{ext}"
                        if test_file.is_file():
                            candidate_path = f"data/{test_file.name}"
                            break

                if candidate_path:
                    resolved_file = self.resolve_file_path(candidate_path)
                    if resolved_file:
                        # Repository file exists: automatically resolve it
                        resolved[param_key] = str(candidate_path)
                    else:
                        # Repository file does not exist
                        missing_repo_files.append(
                            f"Repository file '{candidate_path}' for input '{param_key}' does not exist."
                        )
                else:
                    # No repository path found
                    is_required = ds_meta.required if ds_meta else True
                    if is_required:
                        missing_repo_files.append(
                            f"Repository file for input '{param_key}' could not be resolved."
                        )

            elif source_type == InputSourceType.DEFAULT:
                # Default input: use declared default_value
                default_val = ds_meta.default_value if ds_meta else None
                if default_val is not None:
                    resolved[param_key] = default_val
                else:
                    resolved[param_key] = 20

            elif source_type == InputSourceType.USER:
                # Genuinely user-provided required input
                is_required = ds_meta.required if ds_meta else True
                if is_required:
                    label = "campaign goal and dates" if "campaign" in param_key else param_key
                    missing_user_inputs.append(label)

            elif source_type == InputSourceType.OPTIONAL:
                default_val = ds_meta.default_value if ds_meta else None
                if default_val is not None:
                    resolved[param_key] = default_val

        # 3. Copy any remaining extra user-provided parameters not mapped to defined inputs
        for k, v in provided.items():
            if k not in resolved and v is not None and v != "":
                resolved[k] = v

        # 4. Formulate overall outcome
        if missing_repo_files:
            return InputResolutionResult(
                status="NEEDS_INPUT",
                resolved_params=resolved,
                missing_user_inputs=missing_user_inputs,
                missing_repo_files=missing_repo_files,
                errors=missing_repo_files,
            )

        if missing_user_inputs:
            return InputResolutionResult(
                status="NEEDS_INPUT",
                resolved_params=resolved,
                missing_user_inputs=missing_user_inputs,
                missing_repo_files=missing_repo_files,
                errors=[f"Missing required input(s): {', '.join(missing_user_inputs)}"],
            )

        return InputResolutionResult(
            status="SUCCESS",
            resolved_params=resolved,
            missing_user_inputs=[],
            missing_repo_files=[],
            errors=[],
        )
