# AI Agent Workflow Automation

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Google ADK](https://img.shields.io/badge/Google-ADK-4285F4.svg)](https://github.com/google/adk)
[![Gemini 2.5 Flash](https://img.shields.io/badge/Primary%20LLM-Gemini%202.5%20Flash-orange.svg)](https://ai.google.dev/)
[![Claude 3.5 Sonnet Fallback](https://img.shields.io/badge/Fallback%20LLM-Claude%203.5%20Sonnet-purple.svg)](https://www.anthropic.com/)
[![Tests Passing](https://img.shields.io/badge/pytest-122%20passed-brightgreen.svg)](tests/)
[![Type Safe](https://img.shields.io/badge/pyright-0%20errors-success.svg)](pyrightconfig.json)

An enterprise-grade, extensible AI agent workflow automation system powered by **Python**, **Google ADK**, **Google Gemini**, and **Anthropic Claude**.

This system dynamically transforms business workflows defined in Excel into an executable, observable, and deterministic AI agent workflow engine. It follows a strict architectural boundary: **LLMs understand intent and handle creative tasks, while a generic execution core and deterministic Python tools handle calculations, comparisons, joins, and business logic.**

---

## Table of Contents

- [Core Architecture](#core-architecture)
  - [Architectural Principles](#architectural-principles)
  - [End-to-End Execution Flow](#end-to-end-execution-flow)
- [Excel as the Single Source of Truth](#excel-as-the-single-source-of-truth)
- [Generic Input & Data Source Resolution](#generic-input--data-source-resolution)
- [Dual-Model Resilience & Fallback](#dual-model-resilience--fallback)
- [Supported Workflows (Catalog)](#supported-workflows-catalog)
- [Deterministic Tools & Condition Engine](#deterministic-tools--condition-engine)
- [Execution Statuses & Tracing](#execution-statuses--tracing)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Environment Configuration](#environment-configuration)
- [Running the System](#running-the-system)
  - [ADK Web Interface](#1-google-adk-web-interface)
  - [Direct Python / CLI API](#2-direct-python--cli-api)
- [Testing & Quality Assurance](#testing--quality-assurance)
- [Zero-Code Extensibility (WF011)](#zero-code-extensibility-wf011)

---

## Core Architecture

### Architectural Principles

1. **No 1-to-1 Agent Sprawl**: The system does **NOT** build separate hard-coded agents per workflow. A single root **ADK Router Agent** routes user requests to a single **Generic Workflow Executor**.
2. **Deterministic Execution over LLM Hallucination**:
   - **LLM Responsibility**: Intent routing, entity extraction, creative generation (product descriptions, campaign briefs), and semantic categorization.
   - **Deterministic Python Responsibility**: Reorder thresholds, percentage price discrepancies, table joins, duplicate scoring, ranking, and SLA metrics.
   - **Never allow an LLM to calculate math or filter datasets when deterministic tools exist.**
3. **Zero Hardcoded Workflow IDs**: No `if workflow_id == "WF001"` branches exist anywhere in routing or execution code. All workflow behavior is dynamically loaded from Excel metadata.

### End-to-End Execution Flow

```text
                                  User Request
                       ("Check which products need restocking.")
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │     Google ADK Router Agent       │
                     │  (Gemini 2.5 Flash / Claude 3.5)  │
                     └─────────────────┬─────────────────┘
                                       │
                      Identifies Workflow (WF001)
                      Extracts User Parameters
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │    GenericInputResolver           │
                     │  - Reads Excel Data_Sources       │
                     │  - Auto-resolves repository files │
                     │  - Applies default parameters     │
                     └─────────────────┬─────────────────┘
                                       │
                               All Inputs Valid
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │    GenericWorkflowExecutor        │
                     │  - Iterates Step_Bindings         │
                     │  - Evaluates Conditions           │
                     │  - Dispatches to Tool Registry    │
                     └─────────────────┬─────────────────┘
                                       │
             ┌─────────────────────────┼─────────────────────────┐
             ▼                         ▼                         ▼
   ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
   │ Tool Registry    │      │ Condition Engine │      │ Step Execution   │
   │ filter_rows      │      │ >, <, ==, in     │      │ Trace & Logging  │
   │ join_on, compute │      │ continue/skip    │      │ (latency, state) │
   └──────────────────┘      └──────────────────┘      └──────────────────┘
                                       │
                                       ▼
                        Structured Execution Result
                               (Status: SUCCESS)
                                       │
                                       ▼
                        Natural Language ADK Response
```

---

## Excel as the Single Source of Truth

Workflows are authored and maintained directly in `data/workflows_working.xlsx`. The loader parses sheets with strict Pydantic model validation:

| Sheet Name | Description | Models |
| :--- | :--- | :--- |
| **`Workflows`** | Workflow metadata, trigger descriptions, category, expected output | `WorkflowDefinition` |
| **`Inputs`** | Input parameter names, types, descriptions, and required flags | `WorkflowInput` |
| **`Steps`** | Human-readable business step descriptions | `WorkflowStepDesc` |
| **`Step_Bindings`** | Executable tool bindings, parameters, and variable references | `ExecutableStep` |
| **`Data_Sources`** | Source classification (`repository`, `user`, `default`, `optional`), candidate file paths, and default values | `DataSourceMetadata` |
| **`Decision_Logic`**| Escalation rules, branching thresholds, and fallback criteria | `DecisionRule` |

---

## Generic Input & Data Source Resolution

A major architectural achievement of this platform is **zero-prompt friction**:

1. **Repository Files (`source_type="repository"`)**:
   - Workflows operating on standard system data (e.g., `data/inventory.csv` for WF001, `data/product_catalog.csv` and `data/vendor_prices.csv` for WF002) resolve files automatically from disk.
   - The user does **not** have to supply file paths manually.
2. **Default Values (`source_type="default"`)**:
   - Business constants (e.g., restock threshold `20` for WF001) are loaded from metadata automatically unless explicitly overridden by the user.
3. **User Overrides**:
   - If the user provides a custom path (e.g. `"Check restock using custom_inventory.csv with threshold 35"`), the resolver overrides defaults dynamically.
4. **Genuinely Required User Inputs (`source_type="user"`)**:
   - When inputs cannot be known in advance (e.g. `order_id_or_email` for WF005, `campaign_inputs` for WF007), the workflow cleanly halts with `NEEDS_INPUT` and prompts for clarification.

---

## Dual-Model Resilience & Fallback

To ensure continuous operation during API rate limits (HTTP 429), quota limits, or regional outages, the system features an automated multi-model fallback chain:

```text
┌─────────────────────────┐
│   Gemini 2.5 Flash      │  ◄── Primary Router / Generator
└───────────┬─────────────┘
            │
            ▼ (On 429 / 503 / Quota Failure)
┌─────────────────────────┐
│ Anthropic Claude 3.5    │  ◄── Fallback Model
│ (claude-3-5-sonnet)     │      Header: anthropic-workspace-id
└─────────────────────────┘
```

The fallback mechanism works transparently both in the core router (`generate_with_fallback`) and in the Google ADK Web runner (`FallbackModel` wrapper).

---

## Supported Workflows (Catalog)

| ID | Name | Trigger Description | Input Source | Primary Tools |
| :--- | :--- | :--- | :--- | :--- |
| **WF001** | Inventory Restock Check | User asks which products need restocking | Repository (`inventory.csv`), Default (`20`) | `filter_rows`, `compute_column`, `rank` |
| **WF002** | Product Price Validation | Validate vendor pricing against catalog (>10%) | Repository (`product_catalog.csv`, `vendor_prices.csv`) | `load_file`, `join_on`, `compute_column`, `filter_rows` |
| **WF003** | Vendor File Processing | Validate and clean uploaded vendor catalog | User (`vendor_file`) | `load_file`, `clean_data`, `validate_data` |
| **WF004** | Product Description Generator | Generate descriptions, SEO titles, and meta tags | User (`product_attributes`) | `llm_generate`, `validate_data` |
| **WF005** | Customer Order Status | Look up order tracking and fulfillment status | User (`order_id_or_email`), Repo (`orders.csv`) | `lookup_record`, `check_condition` |
| **WF006** | Duplicate Product Detection | Detect duplicate products by title and SKU | Repository (`product_catalog.csv`) | `load_file`, `fuzzy_match`, `group_by` |
| **WF007** | Marketing Campaign Brief | Generate a comprehensive campaign brief | User (`goal`, `dates`), Repo (`product_catalog.csv`) | `load_file`, `llm_generate`, `format_report` |
| **WF008** | SEO Keyword Classification | Classify keyword search intent and priority | User (`keyword_file`), Repo (`product_catalog.csv`) | `load_file`, `llm_generate`, `group_by` |
| **WF009** | Employee Task Assignment | Match tasks with optimal employee skills & load | User (`task_description`), Repo (`employees.csv`) | `load_file`, `rank`, `filter_rows` |
| **WF010** | Workflow Performance Report | Analyze latency, error rates, and bottlenecks | Repository (`workflow_logs.jsonl`) | `load_file`, `compute_metrics`, `format_report` |
| **WF011** | Customer Churn Risk Analysis *(Extensibility Demo)* | Identify at-risk customers by activity and tickets | Repository (`customers.csv`) | `load_file`, `filter_rows`, `rank` |

---

## Deterministic Tools & Condition Engine

### Safe Condition Evaluation
All condition logic evaluated between workflow steps uses an **AST-safe engine** without `eval()`:
- **Operators**: `<`, `<=`, `>`, `>=`, `==`, `!=`, `in`, `not in`, `contains`, `is_empty`, `is_not_empty`.
- **Outcomes**:
  - `continue`: Proceed to next step.
  - `skip`: Skip the step and mark status as `SKIPPED`.
  - `stop`: Halt execution immediately.
  - `ask_user`: Return `NEEDS_INPUT`.
  - `escalate`: Return `ESCALATED` with human-review diagnostic reasoning.

### Reusable Primitives
The tool layer emphasizes reusable building blocks rather than isolated one-off scripts:
- `load_file`, `filter_rows`, `join_on`, `compute_column`
- `compare_threshold`, `group_by`, `rank`, `fuzzy_match`
- `lookup_record`, `llm_generate`, `clean_data`

---

## Execution Statuses & Tracing

Every workflow execution produces a structured `WorkflowExecutionResult` with an audit trace:

| Status | Meaning | System Behavior |
| :--- | :--- | :--- |
| **`SUCCESS`** | Workflow executed all steps to completion | Returns formatted trace and output artifacts |
| **`NEEDS_INPUT`** | Missing required user inputs or unresolvable file | Prompts user with missing parameters and preserves session state |
| **`ESCALATED`** | Business threshold exceeded or anomaly detected | Flags for supervisor review with reason trace |
| **`FAILED`** | Controlled tool error or irrecoverable validation failure | Captures error traceback and failure step |

### Trace Format Example
```json
{
  "execution_id": "exec-d09618f0",
  "workflow_id": "WF002",
  "status": "SUCCESS",
  "duration_ms": 24,
  "steps": [
    {"step": 1, "tool": "load_file", "status": "COMPLETED", "duration_ms": 3},
    {"step": 2, "tool": "load_file", "status": "COMPLETED", "duration_ms": 2},
    {"step": 3, "tool": "join_on", "status": "COMPLETED", "duration_ms": 6},
    {"step": 4, "tool": "compute_column", "status": "COMPLETED", "duration_ms": 4},
    {"step": 5, "tool": "filter_rows", "status": "COMPLETED", "duration_ms": 9}
  ]
}
```

---

## Project Structure

```text
excel-workflow-automation/
├── README.md                      # Primary project documentation
├── pyrightconfig.json             # Static type-checking configuration
├── .gitignore                     # Git exclusion rules
│
└── ai-agent-workflow/             # Application package
    ├── requirements.txt           # Python dependencies
    ├── pytest.ini                 # Pytest test configuration
    ├── .env.example               # Template environment credentials
    ├── skill.md                   # Agent system capabilities definition
    │
    ├── app/
    │   ├── agent.py               # Google ADK agent discovery root (root_agent)
    │   ├── main.py                # Standalone FastAPI / HTTP entrypoint
    │   │
    │   ├── agent/                 # Intelligent routing layer
    │   │   ├── router_agent.py    # ADK Router, intent engine, dynamic prompt builder
    │   │   └── llm_fallback.py    # Dual-model Gemini -> Claude fallback provider
    │   │
    │   ├── workflow/              # Execution & definition layer
    │   │   ├── models.py          # Pydantic schemas (WorkflowDefinition, Step, etc.)
    │   │   ├── loader.py          # Excel workbook loader & validator
    │   │   ├── registry.py        # In-memory workflow registry
    │   │   ├── resolver.py        # GenericInputResolver for repository & defaults
    │   │   ├── executor.py        # GenericWorkflowExecutor with step runner
    │   │   └── conditions.py      # AST-safe condition evaluation engine
    │   │
    │   ├── tools/                 # Deterministic tool layer
    │   │   ├── registry.py        # Tool registration decorator & lookup
    │   │   ├── primitives.py      # Core data manipulation tools
    │   │   └── data_sources.py    # Simulated domain source connectors
    │   │
    │   └── tracing/               # Observability layer
    │       ├── trace.py           # Execution trace logger and markdown formatter
    │       └── logger.py          # Rotating file & console logging
    │
    ├── data/                      # Data assets & workbook definitions
    │   ├── workflows_working.xlsx # Excel source of truth (Workflows, Steps, Data_Sources)
    │   ├── workflows.xlsx         # Original reference workbook
    │   ├── inventory.csv          # WF001 inventory stock dataset
    │   ├── product_catalog.csv    # WF002, WF006, WF007 product catalog dataset
    │   ├── vendor_prices.csv      # WF002 wholesale pricing dataset
    │   ├── orders.csv             # WF005 order tracking dataset
    │   ├── employees.csv          # WF009 employee skills dataset
    │   └── workflow_logs.jsonl    # WF010 execution telemetry logs
    │
    └── tests/                     # Comprehensive test suite (122 tests)
        ├── test_generic_input_resolution.py
        ├── test_router_agent.py
        ├── test_executor.py
        ├── test_condition_engine.py
        ├── test_workflow_layer.py
        ├── test_tool_layer.py
        ├── test_tracing.py
        └── test_llm_fallback.py
```

---

## Getting Started

### Prerequisites

- **Python 3.10+** (Tested on Python 3.10, 3.11, 3.12, 3.14)
- Google Gemini API Key ([Google AI Studio](https://aistudio.google.com/app/apikey))
- *(Optional)* Anthropic API Key for dual-model fallback ([Anthropic Console](https://console.anthropic.com/))

### Installation

```bash
# Clone the repository
git clone git@github.com:AyushKumar3183/excel-workflow-autaotaion.git
cd excel-workflow-autaotaion/ai-agent-workflow

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate       # On macOS/Linux
# .venv\Scripts\activate        # On Windows

# Install dependencies
pip install -r requirements.txt
```

### Environment Configuration

Create a `.env` file from the provided template:

```bash
cp .env.example .env
```

Edit `.env` and provide your credentials:

```ini
# Google Gemini & ADK
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# Anthropic Claude Fallback (Optional)
ANTHROPIC_API_KEY=your_anthropic_api_key_here
CLAUDE_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_WORKSPACE_ID=wrkspc_01YFjk7sHarCu1eJBpiitzFR
```

---

## Running the System

### 1. Google ADK Web Interface

Launch the interactive Google ADK Web UI:

```bash
cd ai-agent-workflow
adk web --reload_agents
```

Open your browser to:
👉 **`http://127.0.0.1:8000`**

#### Real Session Test Prompts

- **WF001 (Inventory Restock):**
  > *"Check which products need restocking."*
  >
  > ✅ **Expected Result**: WF001 automatically resolves `data/inventory.csv` and default threshold `20`, executes 5 deterministic steps, and outputs the 6 products below threshold without asking for manual files.

- **WF002 (Price Validation):**
  > *"Validate product prices and identify products where the vendor price differs from our price by more than 10%."*
  >
  > ✅ **Expected Result**: WF002 automatically resolves `product_catalog.csv` and `vendor_prices.csv`, performs relational join, computes strictly `> 10.0%` differences, and lists 6 pricing discrepancies.

- **WF005 (Order Status):**
  > *"Check status of order ORD-1001."*
  >
  > ✅ **Expected Result**: Extracts `ORD-1001`, auto-resolves `data/orders.csv`, and returns items and tracking status.

- **WF007 (Campaign Brief - Missing Input Handling):**
  > *"Launch a new marketing campaign."*
  >
  > ⚠️ **Expected Result**: Halts with `NEEDS_INPUT` and prompts for campaign goal and timeline dates.

### 2. Direct Python / CLI API

You can also execute workflows programmatically without running the web server:

```python
from app.agent.router_agent import RouterAgent
from app.workflow.registry import default_registry

router = RouterAgent(registry=default_registry)

# Natural language query
response = router.handle_message("Check which products need restocking.")

print(response.response_text)
print("Execution Status:", response.execution_result.status)
```

---

## Testing & Quality Assurance

The system maintains a comprehensive suite of 122 automated tests covering condition branches, error escalations, dynamic input resolutions, tool primitives, and ADK discovery.

### Run Full Test Suite

```bash
cd ai-agent-workflow
.venv/bin/pytest tests/ -v
```

**Expected output:**
```text
======================== 122 passed, 2 warnings in 1.44s ========================
```

### Static Type Checking

Verify strict type correctness:

```bash
.venv/bin/pyright app/ tests/
```

**Expected output:**
```text
0 errors, 0 warnings, 0 informations
```

---

## Zero-Code Extensibility (WF011)

The architecture is built for rapid scalability: **Adding a new workflow does not require creating a new agent or writing Python dispatch code.**

### How to Add a New Workflow:
1. Open `data/workflows_working.xlsx`.
2. Add a new row in the **`Workflows`** sheet (e.g. `WF011`, *"Customer Churn Risk Analysis"*).
3. Define its inputs in **`Inputs`** and data source origin in **`Data_Sources`** (e.g. `data/customers.csv`).
4. Bind executable steps in **`Step_Bindings`** using existing primitive tools (`load_file`, `filter_rows`, `rank`).
5. **Done!** The system will:
   - Load and validate the definition at boot.
   - Include `WF011` in the ADK Router's dynamic prompt catalog.
   - Route natural language requests matching the trigger directly to `GenericWorkflowExecutor`.