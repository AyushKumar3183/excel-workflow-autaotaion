skill.md — AI Agent Workflow Automation Development Rules
Purpose
This file defines the engineering rules for implementing the AI Agent Workflow Automation assignment.
The project must implement a reusable workflow engine, not ten hard-coded agents.
Non-Negotiable Architecture
Use this flow:
Excel
  -> Loader + Validation
  -> Pydantic Workflow Models
  -> Workflow Registry
  -> ADK Router
  -> execute_workflow(workflow_id, params)
  -> Input Validation
  -> Generic Executor
  -> Tool Registry
  -> Condition Engine
  -> Trace + Execution Log
  -> Result Formatter
1. Workflow-as-Data
Workflows must be represented as data.
Do not create:
inventory_agent.py
pricing_agent.py
vendor_agent.py
...
for each workflow.
The executor must operate on a generic workflow definition.
2. Excel Is a Source
The supplied Excel workbook is the source for workflow definitions and test questions.
The loader must consume the actual workbook data.
Do not silently replace Excel workflow definitions with hard-coded Python dictionaries.
Because the original Steps/Decision_Logic are business-level descriptions, maintain explicit step bindings containing at least:
workflow_id
step_no
tool
params
output_variable
condition
on_error
3. Pydantic Validation
Use Pydantic models for normalized workflow definitions.
Validate:
- workflow ID
- workflow name
- required inputs
- step order
- tool names
- parameter mappings
- conditions
- error policy
- output definition
Invalid workflow definitions should fail during loading/validation, not halfway through execution.
4. ADK Router Responsibilities
The ADK router may:
- understand the user request
- select a workflow
- extract parameters
- return a confidence score
- ask for clarification when confidence is low
- call execute_workflow(workflow_id, params)
The router must not implement business calculations or manually execute workflow steps.
Build the router prompt from the loaded workflow catalog so newly added workflows become visible automatically.
5. Generic Executor Responsibilities
The executor must:
1. Load the selected workflow definition.
2. Validate required parameters.
3. Iterate through workflow steps.
4. Resolve the configured tool.
5. Resolve tool parameters from execution context.
6. Execute the tool.
7. Store the output in context.
8. Evaluate the condition.
9. Select the next action.
10. Write trace information for every step.
11. Return a structured result and status.
The executor must not contain workflow-specific if/elif branches.
6. Deterministic Logic
Use Python for deterministic operations.
Examples:
- arithmetic
- thresholds
- percentage calculations
- filtering
- joining
- grouping
- ranking
- duplicate detection
- failure-rate calculations
- execution-time calculations
Do not ask the LLM to calculate values that can be calculated reliably by Python.
7. LLM Responsibilities
Use the LLM for tasks that actually benefit from language understanding/generation:
- intent/workflow routing
- parameter extraction
- product description generation
- marketing brief generation
- keyword intent classification
Validate generated outputs before accepting them.
8. Tool Registry
Tools must be resolved by name through a registry.
Example:
TOOL_REGISTRY = {
    "load_file": load_file,
    "filter_rows": filter_rows,
    "compare_threshold": compare_threshold,
    "rank": rank,
    "llm_generate": llm_generate,
}
Prefer reusable primitives.
Do not create unnecessary workflow-specific tools when an existing primitive can perform the operation.
9. Conditions
Never use:
eval(condition)
Use a structured condition representation or a safe evaluator.
Conditions can result in:
CONTINUE
SKIP
STOP
NEEDS_INPUT
ESCALATE
Conditions are evaluated inside the executor loop, between steps.
10. Error Handling
Use controlled statuses:
SUCCESS
NEEDS_INPUT
ESCALATED
FAILED
Retry only transient failures such as:
- temporary tool failures
- timeouts
- transient LLM/API failures
Do not retry validation failures indefinitely.
After retry exhaustion:
FAILED
The failure must be recorded in the trace.
11. Trace Requirements
Write trace information at every step.
At minimum record:
execution_id
workflow_id
step_no
step_name
tool
input
output
status
duration
error
timestamp
Possible step statuses:
STARTED
COMPLETED
SKIPPED
FAILED
A skipped step is not a successful step.
12. Execution Log
Persist workflow execution records in JSONL or CSV.
WF010 must be able to read this execution history.
Seed the log with representative historical failures if required for the WF010 demonstration.
13. Missing Input
Never invent business data.
If required input is missing:
NEEDS_INPUT
Ask the user for the missing value/file and continue the workflow when the required information is supplied.
Relevant workflows include:
- WF004
- WF005
- WF007
WF009 can result in:
ESCALATED
when no suitable employee exists.
14. File Inputs
Some test requests refer to files indirectly, for example:
"this vendor spreadsheet"
"these keywords"
"this urgent task"
Support explicit file/attachment input.
If the required file is not available, return NEEDS_INPUT rather than inventing its contents.
15. Configuration Assumptions
Some workflow values are not fully defined by the assessment.
Examples:
- WF001 reorder-quantity formula
- WF006 duplicate similarity threshold
- WF010 execution-time threshold
- WF002 percentage comparison base
Put chosen values in configuration and document them in the README.
Add boundary tests for threshold behavior.
16. WF002 Percentage Rule
Document which price is the denominator for percentage difference.
For example:
difference_percent =
    abs(our_price - vendor_price) / vendor_price * 100
Do not leave the denominator ambiguous.
Test the exact 10% boundary separately from values greater than 10%.
17. WF004 Output Validation
Generated product content should be validated.
At minimum verify:
- title length is within the configured limit
- meta description length is within the configured limit
- missing product attributes are not invented
- required output fields exist
18. Testing
Every supplied Test_Question must have a test.
Also test:
- paraphrases
- ambiguous requests
- unrelated requests
- no-match requests
- missing parameters
- missing files
- unknown records
- empty datasets
- tool failures
- transient failures
- condition boundaries
- trace output
- status transitions
Mock tools and LLM calls in executor unit tests.
19. ADK Session State
Use ADK session/state capabilities for multi-turn interactions where a workflow returns NEEDS_INPUT.
Example:
User:
"Create a campaign brief for the new collection."

Agent:
NEEDS_INPUT:
- campaign goal
- campaign dates

User:
"Goal is product launch. Dates are Oct 15–30."

Agent:
Continue WF007 using the stored workflow context.
20. WF011 Requirement
Adding WF011 should not require:
- a new agent
- a new executor
- a new router branch
- a workflow-specific if/elif
The preferred demonstration is:
Add workflow + bindings to Excel
        ↓
Loader
        ↓
Registry
        ↓
Router automatically sees WF011
        ↓
Generic Executor runs it
A genuinely new capability may require a new tool. That is acceptable.
21. Code Quality
Prefer:
- type hints
- Pydantic models
- small functions
- dependency injection where useful
- explicit error types
- structured logging
- deterministic business logic
- unit-testable modules
Avoid:
- giant agent files
- hidden global state
- duplicated workflow logic
- hard-coded workflow routing
- eval()
- swallowing exceptions
- magic constants scattered through the code
22. Security
Never commit:
API keys
service-account secrets
.env
private credentials
Commit:
.env.example
with placeholder values.
23. Implementation Order
Follow this order:
1. Schema
2. Excel loader
3. Step bindings
4. Pydantic validation
5. Workflow registry
6. Tool registry + primitives
7. Generic executor
8. Condition engine
9. Trace + execution log
10. ADK router
11. Input/session handling
12. WF001–WF010
13. Tests
14. WF011
15. README
16. Loom
Do not build a custom frontend before the executor works.
The ADK Web interface can be used for the demonstration.
24. Definition of Done
The implementation is not complete until:
- all 10 workflows can be executed
- workflows are loaded from Excel
- workflow selection works through ADK
- parameters are extracted and validated
- tools execute through a registry
- conditions work
- errors are controlled
- NEEDS_INPUT, ESCALATED, FAILED, and SUCCESS are supported
- every step is traced
- WF010 reads execution logs
- tests cover the supplied test questions
- WF011 can be added without a new agent/executor
- README contains reproducible setup instructions
- no real credentials are committed