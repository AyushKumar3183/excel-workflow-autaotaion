"""Main entry point for Google ADK web interface and CLI execution.

Exposes the root router agent for ADK discovery (e.g. `adk web`) and provides a direct
command-line execution interface.
"""

from __future__ import annotations

import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables (.env)
load_dotenv()

from app.agent import agent, root_agent, router


def main():
    """Command-line interface for running queries through the router agent."""
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f"\nUser Query: {query}\n" + "-" * 60)
        res = router.handle_message(query)
        print(res.response_text)
    else:
        print("AI Agent Workflow Automation System (ADK Router)")
        print(f"Loaded Workflows: {router.registry.count()}")
        print("Type a request or 'exit' to quit.\n")
        session_id = "cli_session"
        while True:
            try:
                user_input = input("User > ").strip()
                if not user_input or user_input.lower() in ("exit", "quit"):
                    break
                resp = router.handle_message(user_input, session_id=session_id)
                print(f"\nAgent >\n{resp.response_text}\n")
            except (KeyboardInterrupt, EOFError):
                break


if __name__ == "__main__":
    main()
