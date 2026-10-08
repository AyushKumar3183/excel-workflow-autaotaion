AI Agent Workflow Automation
A reusable AI workflow automation system built with Python + Google ADK + Gemini.
This project is the technical assignment for converting business workflows supplied in Excel into an executable, traceable AI-agent workflow system.
Objective
The system accepts a natural-language user request, identifies the appropriate workflow, extracts the required parameters, validates the inputs, and executes the selected workflow through a single generic workflow executor.
The design deliberately avoids creating one hard-coded agent per workflow.
User Request
     |
     v
ADK Router
     |
     | select workflow + extract parameters
     v
execute_workflow(workflow_id, params)
     |
     v
Input Validation
     |
     v
Generic Executor
     |
     +--> Tool Registry --> Tool execution
     |
     +--> Condition Engine
     |
     +--> Trace
     |
     v
Result + Status
     |
     v
User
Core Architecture
workflows.xlsx
      |
      v
Loader + Validation
      |
      v
Pydantic Models
      |
      v
Workflow Registry
      |
      | workflow catalog
      v
ADK Router
      |
      | execute_workflow(workflow_id, params)
      v
Input Validator
      |
      v
Generic Executor
      |
      +--> Step Definition
      |       |
      |       +--> Tool Registry --> Tool
      |       |
      |       +--> Context
      |       |
      |       +--> Condition Engine
      |               |
      |               +--> continue
      |               +--> skip
      |               +--> stop
      |               +--> ask user
      |               +--> escalate
      |
      +--> Trace / Execution Log
      |
      v
Result Formatter
      |
      v
User
Architectural principle
The LLM is not responsible for deterministic business execution.
- ADK / LLM: intent understanding, workflow selection, parameter extraction, and selected content-generation/classification tasks.
- Generic executor: deterministic workflow execution.
- Python tools: calculations, filtering, comparison, ranking, deduplication, file processing, and other deterministic operations.
- Trace/logging: records every step and outcome.
Excel as the Workflow Source
The supplied Excel workbook is part of the project source of truth.
The loader converts the workbook into validated workflow definitions. The original workflow Steps and Decision_Logic fields are business descriptions, so executable step bindings are required to map workflow steps to tools and parameters.
Recommended binding model:
workflow_id
step_no
tool
params
output_variable
condition
on_error
The loader should validate these definitions before registering them.
Workflows in the supplied workbook
The workbook contains 10 workflow definitions plus test questions.
- WF001 — Inventory Restock Check
- WF002 — Product Price Validation
- WF003 — Vendor File Processing
- WF004 — Product Description Generator
- WF005 — Customer Order Status
- WF006 — Duplicate Product Detection
- WF007 — Marketing Campaign Brief
- WF008 — SEO Keyword Classification
- WF009 — Employee Task Assignment
- WF010 — Workflow Performance Report
Execution Statuses
The executor uses explicit statuses:
- SUCCESS — workflow completed.
- NEEDS_INPUT — required information is missing; the system asks the user and can continue in a later turn.
- ESCALATED — the workflow reached a business condition requiring escalation.
- FAILED — execution failed after controlled error handling.
Transient tool/LLM failures may be retried. Validation failures should not be blindly retried.
Conditions
Conditions are evaluated between workflow steps, inside the generic executor.
Do not use Python eval() for workflow conditions.
Use a structured condition representation such as:
{
  "field": "stock",
  "operator": "<",
  "value": "minimum_stock"
}
The condition engine decides whether to continue, skip, stop, ask the user, or escalate.
Tools
The tool layer should favor reusable primitives over one Python function per workflow.
Examples:
- load_file
- filter_rows
- join_on
- compute_column
- compare_threshold
- group_by
- rank
- fuzzy_match
- llm_generate
Domain-specific tools can be used where simulated business data sources are required, such as orders, shipments, employees, or inventory.
Deterministic vs LLM Operations
Deterministic Python
Use Python for:
- reorder quantity calculations
- price difference calculations
- threshold comparisons
- duplicate detection
- ranking
- failure rates
- execution-time analysis
- data validation
LLM
Use Gemini/ADK for:
- workflow routing
- parameter extraction
- product-description generation
- marketing-content generation
- keyword intent classification
LLM-generated outputs should be validated before they are accepted as workflow results.
Trace and Execution Log
Every workflow step should generate trace information.
Example:
{
  "execution_id": "exec-001",
  "workflow_id": "WF001",
  "step": 2,
  "tool": "compare_threshold",
  "status": "COMPLETED",
  "duration_ms": 18,
  "input": {},
  "output": {},
  "error": null
}
Skipped steps should be recorded as SKIPPED, not reported as successful.
The persistent execution log is also used by WF010 for workflow-performance analysis.
Missing Input Handling
The system must not invent missing business data.
Examples of expected behavior:
- Missing product attributes → NEEDS_INPUT
- Unknown order → ask for another identifier
- Missing campaign goal/dates → ask before generating the campaign brief
- No suitable employee → ESCALATED
- Missing file for a file-based workflow → controlled input request
Sample Data
The assessment references external CSV/API sources, but those external systems are not supplied with the assignment.
Therefore this repository contains deterministic sample data for all required business domains:

### WF002 — Product Price Validation Specification & Datasets
- **`data/product_catalog.csv`**: Represents our internal product catalog with standard selling prices (`unit_price`), decoupled from warehouse stock counts.
- **`data/vendor_prices.csv`**: Represents wholesale vendor catalog pricing (`vendor_price`) along with vendor names and lead times.
- **Join Key**: Joined relationally on the primary product identifier **`sku`** using the `join_on` tool with right-table prefixing (`vendor_`).
- **Our Price Column**: `unit_price` (also accepts aliases `price` or `our_price`).
- **Vendor Price Column**: `vendor_price` (becomes `vendor_vendor_price` in the joined dataset).
- **Percentage Difference Formula**:
  $$\text{pct\_diff} = \frac{|\text{our\_price} - \text{vendor\_price}|}{\text{vendor\_price}} \times 100$$
  Calculated deterministically in Step 4 via `compute_column(operation="percentage_difference")`.
- **10% Condition Threshold**: Strictly **`> 10.0%`** (strictly greater than 10%, **NOT** $\ge 10\%$).
  Products with an exact 10.00% price discrepancy are considered within normal variance and are **not** flagged. Only items with a discrepancy strictly exceeding 10.0% (e.g., 10.01%, 17.64%) are flagged as `price_exceptions`.

Project Structure
ai-agent-workflow/
├── app/
│   ├── agent/
│   │   ├── root_agent.py
│   │   ├── router_agent.py
│   │   └── llm_fallback.py
│   ├── workflow/
│   │   ├── loader.py
│   │   ├── models.py
│   │   ├── registry.py
│   │   ├── executor.py
│   │   └── conditions.py
│   ├── tools/
│   │   ├── registry.py
│   │   ├── primitives.py
│   │   └── data_sources.py
│   ├── tracing/
│   │   ├── logger.py
│   │   └── trace.py
│   ├── agent.py
│   └── main.py
├── data/
│   ├── workflows.xlsx
│   ├── workflows_working.xlsx
│   ├── inventory.csv
│   ├── product_catalog.csv
│   ├── vendor_prices.csv
│   ├── orders.csv
│   ├── employees.csv
│   └── workflow_logs.jsonl
├── examples/
├── tests/
├── .env.example
├── requirements.txt
├── README.md
└── skill.md
Setup
Final dependency names and commands should be kept synchronized with requirements.txt after implementation.

git clone <repository-url>
cd ai-agent-workflow

python -m venv .venv
source .venv/bin/activate       # macOS/Linux
# .venv\Scripts\activate      # Windows

pip install -r requirements.txt
Create .env from .env.example and configure the required Gemini/Google ADK credentials.
Never commit real API keys.
Running
The final repository should support the standard ADK development interface:
adk web
A CLI execution path should also be available so the core executor can be tested independently of the ADK UI.
Generic Automatic Input & Data Source Resolution
The system features an automated, schema-driven input and data source resolver (`GenericInputResolver`):
```text
Excel Workflow Definition
        ↓
Workflow Inputs / Data Source Metadata
        ↓
Generic Input/Data Source Resolver
        ↓
Resolved Parameters
        ↓
Generic Executor
```

### Core Architecture & Behaviors
1. **Repository Data Sources (`source_type="repository"`)**:
   - Declared in the workflow definition (e.g., `data/inventory.csv`, `data/product_catalog.csv`, `data/vendor_prices.csv`, `data/orders.csv`, `data/workflow_logs.jsonl`).
   - If the file exists on disk, it is automatically resolved without the user having to provide internal paths.
   - If the file does not exist, a controlled `NEEDS_INPUT` state is returned.
2. **User Explicit File Overrides**:
   - If the user explicitly passes an alternative file path in their prompt or parameters, the user's file is used.
3. **User-Specific Required Inputs (`source_type="user"`)**:
   - Parameters that cannot be known ahead of time (e.g., `order_id_or_email` for WF005, `campaign_inputs` for WF007, vendor files for WF003).
   - If omitted, the workflow cleanly halts with `NEEDS_INPUT`, prompting the user.
4. **Default / Optional Inputs (`source_type="default"` / `source_type="optional"`)**:
   - Constants like thresholds (e.g., default `20` for inventory restock) are automatically populated if not overridden.
5. **No Workflow-Specific If/Elif Logic**:
   - Resolution is completely driven by metadata on the `WorkflowDefinition` loaded from Excel.

Testing
Tests should cover:
1. All 10 supplied test questions.
2. Paraphrased requests.
3. Ambiguous requests.
4. No-match requests.
5. Missing required parameters.
6. Missing files.
7. Unknown order identifiers.
8. Empty datasets.
9. Tool failures.
10. Condition boundary cases.
11. Trace generation.
12. WF011 extensibility.
The executor should be unit-testable with mocked tools and mocked LLM responses.
WF011 Extensibility Demonstration
The final Loom should demonstrate the scalability requirement.
Add a new workflow definition and its step bindings to Excel.
Then demonstrate:
Excel
  -> Loader
  -> Pydantic validation
  -> Registry
  -> ADK Router
  -> Generic Executor
  -> Result
No new agent, no new executor, and no workflow-specific if/elif block should be required.
A genuinely new capability may require a new tool, but a new workflow should not require a new agent.
Loom Demonstration Checklist
The Loom should demonstrate:
- overall architecture
- Excel processing
- workflow registration
- workflow selection
- parameter extraction
- input validation
- tool calls
- conditions
- retries/error handling
- execution trace
- working workflow examples
- WF010 using execution logs
- WF011 being added through the workflow-definition layer
- technical decisions and trade-offs
Development Principle
Build the workflow engine before the UI.
The architecture should remain framework-independent where practical, with Google ADK acting as the agent/routing/tool-calling layer around the deterministic execution core.