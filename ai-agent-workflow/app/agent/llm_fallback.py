"""LLM Fallback Module: Google Gemini as primary, Anthropic Claude as fallback.

Provides high-availability LLM orchestration:
1. Primary: Google Gemini (default: gemini-2.5-flash)
2. Fallback: Anthropic Claude (default: claude-3-5-sonnet-20241022)
3. Heuristic / Deterministic Fallback: Domain logic if both providers are offline/unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

logger = logging.getLogger("ai_workflow.llm_fallback")


class LLMResult(BaseModel):
    """Encapsulates generated text with provider provenance and fallback status."""
    text: str = Field(description="Generated output content")
    provider: str = Field(description="Provider used: 'gemini', 'anthropic', or 'deterministic'")
    model: str = Field(description="Model identifier")
    fallback_used: bool = Field(default=False, description="True if primary Gemini failed and fallback took over")
    error: Optional[str] = Field(default=None, description="Diagnostic error message if a fallback occurred")


def get_gemini_config() -> Tuple[Optional[str], str]:
    """Retrieve Gemini API key and model name from environment."""
    key = os.getenv("GEMINI_API_KEY")
    # Treat empty or placeholder strings as unconfigured
    if key and (key.strip() == "" or "your_" in key.lower()):
        key = None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return key, model


def get_claude_config() -> Tuple[Optional[str], str]:
    """Retrieve Anthropic API key and model name from environment."""
    key = os.getenv("ANTHROPIC_API_KEY")
    # Treat empty or placeholder strings as unconfigured
    if key and (key.strip() == "" or "your_" in key.lower()):
        key = None
    model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
    return key, model


def get_claude_workspace_id() -> Optional[str]:
    """Retrieve Anthropic Workspace ID from environment."""
    workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_01YFjk7sHarCu1eJBpiitzFR")
    if workspace_id and (workspace_id.strip() == "" or "your_" in workspace_id.lower()):
        workspace_id = None
    return workspace_id


def call_gemini(
    prompt: str,
    system_instruction: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """Invoke Google Gemini model via google-genai SDK.
    
    Raises:
        Exception on API failure, quota limit, invalid key, or network issue.
    """
    key, default_model = get_gemini_config()
    target_key = api_key or key
    target_model = model or default_model

    if not target_key:
        raise ValueError("GEMINI_API_KEY is not set or empty.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=target_key)
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    if system_instruction:
        config.system_instruction = system_instruction

    response = client.models.generate_content(
        model=target_model,
        contents=prompt,
        config=config,
    )

    if not response.text:
        raise ValueError(f"Gemini returned empty text for prompt.")

    return response.text.strip()


def call_claude(
    prompt: str,
    system_instruction: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    workspace_id: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """Invoke Anthropic Claude model via anthropic SDK with workspace header.
    
    Raises:
        Exception on API failure, quota limit, invalid key, or network issue.
    """
    key, default_model = get_claude_config()
    default_workspace = get_claude_workspace_id()
    target_key = api_key or key
    target_model = model or default_model
    target_workspace = workspace_id or default_workspace

    if not target_key:
        raise ValueError("ANTHROPIC_API_KEY is not set or empty.")

    import anthropic

    headers: Dict[str, str] = {}
    if target_workspace:
        headers["anthropic-workspace-id"] = target_workspace

    client = anthropic.Anthropic(
        api_key=target_key,
        default_headers=headers if headers else None,
    )

    kwargs: Dict[str, Any] = {
        "model": target_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_instruction:
        kwargs["system"] = system_instruction

    message = client.messages.create(**kwargs)
    
    parts: List[str] = []
    for block in message.content:
        if hasattr(block, "text") and block.text:
            parts.append(block.text)

    result_text = "".join(parts).strip()
    if not result_text:
        raise ValueError(f"Claude returned empty text for prompt.")

    return result_text


def generate_with_fallback(
    prompt: str,
    system_instruction: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    deterministic_fallback_fn: Optional[Callable[[], str]] = None,
) -> LLMResult:
    """Primary entry point for LLM generation with automated Claude fallback.
    
    1. Attempts Gemini (primary).
    2. If Gemini fails, automatically falls back to Claude (secondary).
    3. If Claude fails, falls back to deterministic/heuristic fallback.
    """
    gemini_key, gemini_model = get_gemini_config()
    claude_key, claude_model = get_claude_config()
    claude_workspace = get_claude_workspace_id()

    gemini_error: Optional[str] = None

    # Step 1: Attempt Gemini
    if gemini_key:
        try:
            text = call_gemini(
                prompt=prompt,
                system_instruction=system_instruction,
                model=gemini_model,
                api_key=gemini_key,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return LLMResult(
                text=text,
                provider="gemini",
                model=gemini_model,
                fallback_used=False,
            )
        except Exception as exc:
            gemini_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Gemini (%s) failed: %s. Initiating Claude (%s) fallback...",
                gemini_model,
                gemini_error,
                claude_model,
            )
    else:
        gemini_error = "GEMINI_API_KEY not configured"

    # Step 2: Attempt Claude Fallback
    claude_error: Optional[str] = None
    if claude_key:
        try:
            text = call_claude(
                prompt=prompt,
                system_instruction=system_instruction,
                model=claude_model,
                api_key=claude_key,
                workspace_id=claude_workspace,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return LLMResult(
                text=text,
                provider="anthropic",
                model=claude_model,
                fallback_used=True,
                error=f"Gemini failed: {gemini_error}",
            )
        except Exception as exc:
            claude_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Claude fallback (%s) failed: %s.",
                claude_model,
                claude_error,
            )
    else:
        claude_error = "ANTHROPIC_API_KEY not configured"

    # Step 3: Heuristic / Deterministic Fallback
    fallback_text = deterministic_fallback_fn() if deterministic_fallback_fn else ""
    return LLMResult(
        text=fallback_text,
        provider="deterministic",
        model="offline_fallback",
        fallback_used=True,
        error=f"Gemini failed ({gemini_error}); Claude failed ({claude_error})",
    )


def create_fallback_adk_model(
    gemini_model: Optional[str] = None,
    claude_model: Optional[str] = None,
    workspace_id: Optional[str] = None,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Create a Google ADK FallbackModel configured with Gemini and Claude (with workspace header).
    
    Used by Google ADK Agent discovery and adk web.
    """
    _, g_default = get_gemini_config()
    key, c_default = get_claude_config()
    default_workspace = get_claude_workspace_id()
    target_gemini = gemini_model or g_default
    target_claude = claude_model or c_default
    target_workspace = workspace_id or default_workspace

    try:
        from google.adk.models._fallback_model import FallbackModel
        from google.adk.models.anthropic_llm import AnthropicLlm
        import anthropic

        headers: Dict[str, str] = {}
        if target_workspace:
            headers["anthropic-workspace-id"] = target_workspace

        async_client = None
        if key:
            async_client = anthropic.AsyncAnthropic(
                api_key=key,
                default_headers=headers if headers else None,
            )

        claude_llm = AnthropicLlm(
            model=target_claude,
            client=async_client,
        )
        return FallbackModel(models=[target_gemini, claude_llm])
    except Exception as exc:
        logger.warning(
            "Could not instantiate ADK FallbackModel: %s. Using %s.",
            exc,
            target_gemini,
        )
        return target_gemini
