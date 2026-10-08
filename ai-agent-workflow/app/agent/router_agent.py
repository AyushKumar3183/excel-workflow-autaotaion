"""Google ADK Router Agent for natural-language workflow discovery and parameter extraction.

Routes natural-language user requests to validated workflows in the dynamic catalog,
extracts parameters, handles multi-turn clarification sessions, and delegates deterministic
execution to GenericWorkflowExecutor.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from google.adk.agents import Agent

from app.agent.llm_fallback import (
    LLMResult,
    create_fallback_adk_model,
    generate_with_fallback,
)
from app.tracing.trace import format_trace
from app.workflow.executor import (
    ExecutionStatus,
    GenericWorkflowExecutor,
    WorkflowExecutionResult,
    default_executor,
)
from app.workflow.models import InputSourceType, WorkflowDefinition
from app.workflow.registry import WorkflowRegistry, default_registry
from app.workflow.resolver import (
    GenericInputResolver,
    InputResolutionResult,
    find_param_in_dict,
)


# ---------------------------------------------------------------------------
# Router Models & Session State
# ---------------------------------------------------------------------------

class RoutingDecision(BaseModel):
    """Structured decision made by the router agent."""
    workflow_id: Optional[str] = Field(default=None, description="Selected workflow identifier (e.g. WF001)")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Routing confidence score")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Extracted workflow parameters")
    needs_clarification: bool = Field(default=False, description="True if input is ambiguous or missing required fields")
    clarification_prompt: Optional[str] = Field(default=None, description="Prompt asking user for clarification or input")
    reasoning: Optional[str] = Field(default=None, description="Diagnostic explanation for decision")


class SessionState(BaseModel):
    """Multi-turn conversation session tracking pending workflows and accumulated parameters."""
    session_id: str
    pending_workflow_id: Optional[str] = None
    accumulated_parameters: Dict[str, Any] = Field(default_factory=dict)
    last_prompt: Optional[str] = None


class RouterResponse(BaseModel):
    """User-facing response containing message, routing decision, and optional execution result."""
    response_text: str
    decision: RoutingDecision
    execution_result: Optional[WorkflowExecutionResult] = None
    session_id: str


# ---------------------------------------------------------------------------
# Dynamic Catalog Prompt Builder
# ---------------------------------------------------------------------------

def build_router_instruction(registry: WorkflowRegistry) -> str:
    """Build a comprehensive dynamic system prompt from the currently loaded workflow catalog.
    
    This ensures that any newly added workflow (such as WF011) automatically becomes
    visible to the model without code changes.
    """
    catalog = registry.catalog()
    catalog_lines = []

    for item in catalog:
        wf_obj = registry.get(item["workflow_id"])
        if not wf_obj:
            continue

        user_req: List[str] = []
        auto_ds: List[str] = []

        if wf_obj.data_sources:
            for ds in wf_obj.data_sources.values():
                if ds.source_type == InputSourceType.USER and ds.required:
                    desc = f" ({ds.description})" if ds.description else ""
                    user_req.append(f"{ds.name}{desc}")
                elif ds.source_type in (InputSourceType.REPOSITORY, InputSourceType.DEFAULT):
                    src_val = ds.data_source or ds.default_value
                    auto_ds.append(f"{ds.name} (auto-resolved from {src_val})")
        else:
            needed_vars = IntentRouterEngine._get_workflow_external_inputs(wf_obj)
            declared_inputs = [inp.name for inp in wf_obj.inputs if inp.name]
            combined = list(dict.fromkeys(needed_vars + declared_inputs))
            user_req = combined

        user_req_desc = ", ".join(user_req) if user_req else "None (Auto-resolved from repository data and defaults)"
        auto_ds_desc = ", ".join(auto_ds) if auto_ds else "None"

        catalog_lines.append(
            f"- Workflow ID: {item['workflow_id']}\n"
            f"  Name: {item['name']}\n"
            f"  Trigger/Description: {item['trigger']}\n"
            f"  Required User Inputs: {user_req_desc}\n"
            f"  Auto-Resolved Repository Data: {auto_ds_desc}\n"
            f"  Expected Output: {item.get('expected_output') or 'Structured Report'}\n"
        )

    formatted_catalog = "\n".join(catalog_lines) if catalog_lines else "No workflows loaded."

    return (
        "You are the root AI Workflow Router Agent for an enterprise automation system.\n"
        "Your sole responsibility is to understand user requests, identify the appropriate workflow, "
        "extract any user-provided parameters, and invoke the execute_workflow tool.\n\n"
        "STRICT ARCHITECTURAL RULES:\n"
        "1. NEVER perform calculations, price comparisons, or restock formulas yourself. ALWAYS invoke execute_workflow.\n"
        "2. AUTOMATIC DATA SOURCE RESOLUTION:\n"
        "   - Repository files (such as data/inventory.csv, data/product_catalog.csv, data/vendor_prices.csv, data/orders.csv, data/workflow_logs.jsonl) and default settings are automatically resolved by the workflow execution engine from repository data.\n"
        "   - The user NEVER needs to supply internal repository file paths or default parameters.\n"
        "   - When a user request matches a workflow with Required User Inputs as 'None' (e.g., WF001 Inventory Restock, WF002 Price Validation, WF006 Duplicate Detection, WF010 Performance Report), IMMEDIATELY invoke execute_workflow(workflow_id=..., parameters={}).\n"
        "   - NEVER prompt the user for repository files or default parameters.\n"
        "3. USER-SPECIFIC REQUIRED INPUTS:\n"
        "   - ONLY ask the user for clarification if a workflow has 'Required User Inputs' that were omitted by the user (e.g., order ID for WF005, campaign goal and dates for WF007, or user-provided product file for WF003).\n"
        "   - If the user explicitly mentions custom files or specific values (e.g. threshold of 35 or ORD-1001), pass them in `parameters`.\n"
        "4. If a request is completely ambiguous or doesn't match any workflow, ask for clarification.\n\n"
        "CURRENT DYNAMIC WORKFLOW CATALOG:\n"
        f"{formatted_catalog}\n"
    )


# ---------------------------------------------------------------------------
# Deterministic Intent Matching & Parameter Extraction Engine
# ---------------------------------------------------------------------------

class IntentRouterEngine:
    """Deterministic intent understanding and parameter extraction engine.
    
    Powers offline tests and provides a reliable fallback when live Gemini credentials
    are not supplied.
    """

    def __init__(self, registry: WorkflowRegistry, resolver: Optional[GenericInputResolver] = None):
        self.registry = registry
        self.resolver = resolver or GenericInputResolver()

    def route(
        self,
        user_message: str,
        session: Optional[SessionState] = None,
    ) -> RoutingDecision:
        """Analyze message, extract parameters, and decide workflow selection or clarification."""
        msg = user_message.strip()
        msg_lower = msg.lower()

        # 1. Handle Multi-turn Session Continuation
        if session and session.pending_workflow_id:
            wf = self.registry.get(session.pending_workflow_id)
            if wf:
                # Merge newly extracted parameters into accumulated parameters
                new_params = self._extract_parameters_for_workflow(wf, msg)
                merged_params = dict(session.accumulated_parameters)
                merged_params.update(new_params)

                # Check if required inputs are now satisfied
                missing = self._find_missing_required(wf, merged_params)
                if not missing:
                    session.pending_workflow_id = None
                    session.accumulated_parameters.clear()
                    return RoutingDecision(
                        workflow_id=wf.workflow_id,
                        confidence=0.95,
                        parameters=merged_params,
                        needs_clarification=False,
                        reasoning=f"Resumed {wf.workflow_id} with supplied parameters.",
                    )
                else:
                    session.accumulated_parameters = merged_params
                    return RoutingDecision(
                        workflow_id=wf.workflow_id,
                        confidence=0.90,
                        parameters=merged_params,
                        needs_clarification=True,
                        clarification_prompt=f"Please provide the remaining required information: {', '.join(missing)}.",
                        reasoning=f"Still missing required inputs: {missing}",
                    )

        # 2. Score user message against each workflow in the dynamic registry
        best_wf: Optional[WorkflowDefinition] = None
        best_score = 0.0
        ambiguous_matches: List[WorkflowDefinition] = []

        catalog = self.registry.list_workflows()
        for wf in catalog:
            score = self._compute_match_score(msg_lower, wf)
            if score > best_score:
                best_score = score
                best_wf = wf

            if score >= 0.50:
                ambiguous_matches.append(wf)

        # 3. Check for Ambiguous or Low Confidence requests
        if best_score < 0.40 or best_wf is None:
            return RoutingDecision(
                workflow_id=None,
                confidence=best_score,
                needs_clarification=True,
                clarification_prompt=(
                    "I am not sure which workflow you would like to run. "
                    "Could you please specify your goal or task?"
                ),
                reasoning="Request does not match any workflow in the catalog with sufficient confidence.",
            )

        if len(ambiguous_matches) > 1 and best_score < 0.70:
            wf_names = ", ".join(f"'{w.workflow_id} ({w.name})'" for w in ambiguous_matches)
            return RoutingDecision(
                workflow_id=None,
                confidence=best_score,
                needs_clarification=True,
                clarification_prompt=f"Your request could match multiple workflows: {wf_names}. Which one would you like to run?",
                reasoning="Ambiguous match across multiple workflows.",
            )

        # 4. Extract parameters for the matched workflow
        extracted = self._extract_parameters_for_workflow(best_wf, msg)

        # 5. Check if required parameters are missing
        missing = self._find_missing_required(best_wf, extracted)
        if missing:
            if session:
                session.pending_workflow_id = best_wf.workflow_id
                session.accumulated_parameters = extracted

            return RoutingDecision(
                workflow_id=best_wf.workflow_id,
                confidence=best_score,
                parameters=extracted,
                needs_clarification=True,
                clarification_prompt=f"I identified {best_wf.name} ({best_wf.workflow_id}), but need: {', '.join(missing)}.",
                reasoning=f"Identified {best_wf.workflow_id} but missing required inputs: {missing}",
            )

        return RoutingDecision(
            workflow_id=best_wf.workflow_id,
            confidence=best_score,
            parameters=extracted,
            needs_clarification=False,
            reasoning=f"Matched {best_wf.workflow_id} ({best_wf.name}) with confidence {best_score:.2f}.",
        )

    STOPWORDS = {
        "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any",
        "are", "aren", "as", "at", "be", "because", "been", "before", "being", "below",
        "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down",
        "during", "each", "few", "for", "from", "further", "had", "has", "have", "having",
        "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if",
        "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my",
        "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or",
        "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should",
        "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
        "then", "there", "these", "they", "this", "those", "through", "to", "too", "under",
        "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while",
        "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself",
        "yourselves", "user", "asks", "please", "can", "would", "like"
    }

    def _extract_tokens(self, text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z0-9]+", text.lower())
        return {w for w in words if w not in self.STOPWORDS and len(w) > 2}

    def _token_match(self, t1: str, t2: str) -> float:
        if t1 == t2:
            return 1.0
        if len(t1) >= 4 and len(t2) >= 4:
            if t1.startswith(t2[:4]) or t2.startswith(t1[:4]):
                return 0.90
        return 0.0

    def _compute_match_score(self, query: str, wf: WorkflowDefinition) -> float:
        """Calculate dynamic relevance score between user query and workflow definition."""
        # Direct workflow ID mention gives maximum confidence
        if wf.workflow_id.lower() in query:
            return 1.0

        # Explicit Order ID pattern strongly signals order workflow
        if re.search(r"\bORD-\d+\b", query, re.IGNORECASE) and "order" in wf.name.lower():
            return 0.95

        q_tokens = self._extract_tokens(query)
        if not q_tokens:
            return 0.0

        t_tokens = self._extract_tokens(wf.trigger)
        n_tokens = self._extract_tokens(wf.name)

        def similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
            if not tokens_a or not tokens_b:
                return 0.0
            matched_a = sum(max((self._token_match(a, b) for b in tokens_b), default=0.0) for a in tokens_a)
            matched_b = sum(max((self._token_match(b, a) for a in tokens_a), default=0.0) for b in tokens_b)
            cov_a = matched_a / len(tokens_a)
            cov_b = matched_b / len(tokens_b)
            return max(cov_a, cov_b) * 0.65 + min(cov_a, cov_b) * 0.35

        score_trigger = similarity(q_tokens, t_tokens)
        score_name = similarity(q_tokens, n_tokens)
        combined = max(score_trigger, score_name) * 0.70 + ((score_trigger + score_name) / 2.0) * 0.30

        # Scale confident matches into [0.85, 0.99] range
        if combined >= 0.50:
            normalized = 0.85 + (combined - 0.50) * 0.28
            return min(0.99, normalized)
        return combined

    @staticmethod
    def _get_workflow_external_inputs(wf: WorkflowDefinition) -> List[str]:
        """Extract external variable names required by workflow steps."""
        produced: set[str] = set()
        needed: set[str] = set()
        for step in wf.steps:
            for v in step.params.values():
                if isinstance(v, str) and v.startswith("$"):
                    ref = v[1:].split(".")[0]
                    if ref not in produced:
                        needed.add(ref)
            produced.add(step.output_variable)
        return sorted(needed)

    def _extract_parameters_for_workflow(self, wf: WorkflowDefinition, text: str) -> Dict[str, Any]:
        """Extract workflow inputs from natural language without inventing values."""
        params: Dict[str, Any] = {}

        # 1. Order ID pattern (e.g. ORD-1001, ORD-12345)
        order_match = re.search(r"(ORD-\d+)", text, re.IGNORECASE)
        if order_match:
            params["order_id_or_email"] = order_match.group(1).upper()

        # Customer email pattern
        email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
        if email_match:
            params["order_id_or_email"] = email_match.group(0).lower()

        # 2. File references (e.g. data/inventory.csv, custom.csv, etc.)
        file_matches = re.findall(r"([\w/-]+\.(?:csv|xlsx|jsonl|txt))", text, re.IGNORECASE)
        for fm in file_matches:
            fm_lower = fm.lower()
            if "inventory" in fm_lower:
                params["inventory_file"] = fm
            elif "vendor" in fm_lower or "price" in fm_lower:
                params["vendor_file"] = fm
                params["vendor_price_file"] = fm
            elif "catalog" in fm_lower or "product" in fm_lower:
                params["catalog_file"] = fm
                params["product_file"] = fm
            elif "keyword" in fm_lower:
                params["keyword_file"] = fm
            elif "log" in fm_lower:
                params["execution_logs_file"] = fm

        # 3. Explicit threshold values (e.g. "threshold 20" or "threshold of 35")
        thresh_match = re.search(r"(?:threshold|minimum|limit)\s*(?:of|is|:)?\s*(\d+)", text, re.IGNORECASE)
        if thresh_match:
            params["minimum_threshold"] = int(thresh_match.group(1))

        # 4. Campaign goal and dates
        goal_match = re.search(r"goal\s*(?:is|:)\s*([a-zA-Z0-9\s]+?)(?:\.|$|,|dates)", text, re.IGNORECASE)
        if goal_match:
            params.setdefault("campaign_inputs", {})["goal"] = goal_match.group(1).strip()

        dates_match = re.search(r"(?:dates|timeline)\s*(?:are|:)\s*([a-zA-Z0-9\s–-]+)", text, re.IGNORECASE)
        if dates_match:
            params.setdefault("campaign_inputs", {})["dates"] = dates_match.group(1).strip()

        # 5. Task description
        if any(term in text.lower() for term in ("assign", "task", "redesign", "website")):
            params["task_description"] = text.strip()

        # 6. Apply generic schema-driven input resolution (NO workflow-specific if/elif)
        resolution = self.resolver.resolve(wf, params)
        return resolution.resolved_params

    def _find_missing_required(self, wf: WorkflowDefinition, params: Dict[str, Any]) -> List[str]:
        """Check if any mandatory workflow input is missing via generic schema resolver."""
        resolution = self.resolver.resolve(wf, params)
        if resolution.status != "SUCCESS":
            return resolution.missing_user_inputs + resolution.missing_repo_files
        return []


# ---------------------------------------------------------------------------
# ADK Router Agent Wrapper
# ---------------------------------------------------------------------------

class RouterAgent:
    """Root ADK Router Agent.
    
    Coordinates intent routing, multi-turn session state, execution tool delegation,
    and user-facing response formatting.
    """

    def __init__(
        self,
        registry: Optional[WorkflowRegistry] = None,
        executor: Optional[GenericWorkflowExecutor] = None,
        model: Optional[str] = None,
        claude_model: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        self.registry = registry or default_registry
        self.executor = executor or GenericWorkflowExecutor(registry=self.registry)
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.claude_model = claude_model or os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
        self.workspace_id = workspace_id or os.getenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_01YFjk7sHarCu1eJBpiitzFR")
        
        self.engine = IntentRouterEngine(self.registry, resolver=self.executor.resolver)
        self.sessions: Dict[str, SessionState] = {}

        # Build Google ADK Agent with Gemini primary and Claude fallback
        self._adk_agent = self._init_adk_agent()

    def _init_adk_agent(self) -> Agent:
        """Instantiate Google ADK Agent with dynamic catalog prompt and execute_workflow tool."""
        instruction = build_router_instruction(self.registry)

        # High-level ADK tool function
        def execute_workflow(workflow_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
            """Execute a validated workflow through the GenericWorkflowExecutor."""
            res = self.executor.execute(workflow_id=workflow_id, params=parameters)
            return res.model_dump()

        # Multi-model fallback: Gemini primary -> Claude secondary (with workspace header)
        adk_model = create_fallback_adk_model(
            self.model,
            self.claude_model,
            self.workspace_id,
        )

        return Agent(
            name="adk_workflow_router",
            model=adk_model,
            instruction=instruction,
            tools=[execute_workflow],
        )

    def call_llm(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Invoke LLM with automated Gemini primary -> Claude fallback."""
        return generate_with_fallback(
            prompt=prompt,
            system_instruction=system_instruction or build_router_instruction(self.registry),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def handle_message(
        self,
        user_message: str,
        session_id: Optional[str] = None,
    ) -> RouterResponse:
        """Process natural-language request from user.
        
        Args:
            user_message: Natural language query.
            session_id: Optional session identifier for multi-turn state.
            
        Returns:
            RouterResponse with decision and optional execution result.
        """
        sid = session_id or "default_session"
        session = self.sessions.setdefault(sid, SessionState(session_id=sid))

        # 1. Route Intent
        decision = self.engine.route(user_message, session=session)

        # 2. Check if clarification is required
        if decision.needs_clarification or not decision.workflow_id:
            prompt = decision.clarification_prompt or "Could you please clarify your request?"
            return RouterResponse(
                response_text=prompt,
                decision=decision,
                execution_result=None,
                session_id=sid,
            )

        # 3. Delegate to Generic Executor
        exec_result = self.executor.execute(
            workflow_id=decision.workflow_id,
            params=decision.parameters,
        )

        # 4. Format User-Facing Response
        wf = self.registry.get(decision.workflow_id)
        wf_name = wf.name if wf else decision.workflow_id
        formatted_trace = format_trace(exec_result, workflow_name=wf_name)

        if exec_result.status == ExecutionStatus.SUCCESS:
            response_text = f"Workflow '{wf_name}' completed successfully.\n\n{formatted_trace}"
        elif exec_result.status == ExecutionStatus.NEEDS_INPUT:
            missing = ", ".join(exec_result.missing_inputs) or "required information"
            response_text = f"Workflow '{wf_name}' requires additional input: {missing}.\n\n{formatted_trace}"
        elif exec_result.status == ExecutionStatus.ESCALATED:
            response_text = f"Workflow '{wf_name}' has been ESCALATED for review.\n\n{formatted_trace}"
        else:
            response_text = f"Workflow '{wf_name}' execution failed.\n\n{formatted_trace}"

        return RouterResponse(
            response_text=response_text,
            decision=decision,
            execution_result=exec_result,
            session_id=sid,
        )

    def get_adk_agent(self) -> Agent:
        """Return the underlying Google ADK Agent object."""
        return self._adk_agent
