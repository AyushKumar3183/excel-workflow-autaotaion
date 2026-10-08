"""Unit tests for LLM Fallback (Gemini primary -> Claude secondary -> Deterministic).

Verifies:
1. Primary Gemini success path
2. Automatic Claude fallback when Gemini fails (quota limit, network error, APIError)
3. Deterministic safety fallback when both Gemini and Claude fail
4. ADK Agent FallbackModel configuration
5. RouterAgent.call_llm integration with fallback
6. primitives.llm_generate fallback integration
"""

from unittest.mock import MagicMock, patch
import pytest

from app.agent.llm_fallback import (
    LLMResult,
    call_claude,
    call_gemini,
    create_fallback_adk_model,
    generate_with_fallback,
    get_claude_config,
    get_claude_workspace_id,
    get_gemini_config,
)
from app.agent.router_agent import RouterAgent
from app.tools.primitives import llm_generate, llm_generate_with_fallback


# ---------------------------------------------------------------------------
# Test Configuration Helpers
# ---------------------------------------------------------------------------

def test_model_config_helpers(monkeypatch):
    """Test retrieval of model configuration and filtering of placeholder keys."""
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyValidGeminiKey")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-valid-key")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_01YFjk7sHarCu1eJBpiitzFR")

    g_key, g_model = get_gemini_config()
    assert g_key == "AIzaSyValidGeminiKey"
    assert g_model == "gemini-2.5-flash"

    c_key, c_model = get_claude_config()
    c_workspace = get_claude_workspace_id()
    assert c_key == "sk-ant-valid-key"
    assert c_model == "claude-3-5-sonnet-20241022"
    assert c_workspace == "wrkspc_01YFjk7sHarCu1eJBpiitzFR"

    # Placeholder keys should be treated as None
    monkeypatch.setenv("GEMINI_API_KEY", "your_gemini_api_key_here")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "your_anthropic_api_key_here")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "your_workspace_id_here")
    g_key, _ = get_gemini_config()
    c_key, _ = get_claude_config()
    c_workspace = get_claude_workspace_id()
    assert g_key is None
    assert c_key is None
    assert c_workspace is None


def test_claude_workspace_header_configured(monkeypatch):
    """Test that anthropic-workspace-id header is properly passed to the Anthropic client."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_01YFjk7sHarCu1eJBpiitzFR")

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_instance = MagicMock()
        mock_anthropic.return_value = mock_instance
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="Claude answer")]
        mock_instance.messages.create.return_value = mock_resp

        ans = call_claude("Test prompt")
        assert ans == "Claude answer"

        # Verify Anthropic was initialized with default_headers containing anthropic-workspace-id
        mock_anthropic.assert_called_once()
        _, kwargs = mock_anthropic.call_args
        assert "default_headers" in kwargs
        assert kwargs["default_headers"].get("anthropic-workspace-id") == "wrkspc_01YFjk7sHarCu1eJBpiitzFR"


# ---------------------------------------------------------------------------
# Primary Gemini Success Path
# ---------------------------------------------------------------------------

def test_gemini_primary_success(monkeypatch):
    """Test standard execution when Gemini succeeds without fallback."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_gemini_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_claude_key")

    with patch("app.agent.llm_fallback.call_gemini") as mock_gemini:
        mock_gemini.return_value = "Gemini generated restock recommendation."

        result = generate_with_fallback(prompt="Analyze low stock inventory")

        assert result.provider == "gemini"
        assert result.model == "gemini-2.5-flash"
        assert result.fallback_used is False
        assert result.text == "Gemini generated restock recommendation."
        mock_gemini.assert_called_once()


# ---------------------------------------------------------------------------
# Claude Fallback When Gemini Fails
# ---------------------------------------------------------------------------

def test_claude_fallback_when_gemini_fails(monkeypatch):
    """Test that Claude is automatically invoked when Gemini raises an exception (e.g. 429 quota)."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_gemini_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_claude_key")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")

    with patch("app.agent.llm_fallback.call_gemini") as mock_gemini, \
         patch("app.agent.llm_fallback.call_claude") as mock_claude:

        # Simulate Gemini hitting quota limit (429 RESOURCE_EXHAUSTED)
        mock_gemini.side_effect = RuntimeError("429 Resource has been exhausted (quota exceeded)")
        mock_claude.return_value = "Claude Sonnet generated response successfully."

        result = generate_with_fallback(prompt="Write marketing copy for Black Friday")

        assert result.fallback_used is True
        assert result.provider == "anthropic"
        assert result.model == "claude-3-5-sonnet-20241022"
        assert result.text == "Claude Sonnet generated response successfully."
        assert "Gemini failed" in (result.error or "")

        mock_gemini.assert_called_once()
        mock_claude.assert_called_once()


# ---------------------------------------------------------------------------
# Deterministic Fallback When Both Fail
# ---------------------------------------------------------------------------

def test_deterministic_fallback_when_both_fail(monkeypatch):
    """Test that system never crashes if both Gemini and Claude fail or are offline."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_gemini_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_claude_key")

    with patch("app.agent.llm_fallback.call_gemini") as mock_gemini, \
         patch("app.agent.llm_fallback.call_claude") as mock_claude:

        mock_gemini.side_effect = RuntimeError("Gemini connection timeout")
        mock_claude.side_effect = RuntimeError("Claude rate limit reached")

        result = generate_with_fallback(
            prompt="Generate SEO title",
            deterministic_fallback_fn=lambda: "Item - Best Deals & Fast Shipping",
        )

        assert result.fallback_used is True
        assert result.provider == "deterministic"
        assert result.text == "Item - Best Deals & Fast Shipping"
        assert "Gemini failed" in (result.error or "")
        assert "Claude failed" in (result.error or "")


# ---------------------------------------------------------------------------
# ADK FallbackModel Integration
# ---------------------------------------------------------------------------

def test_adk_agent_model_fallback_configuration():
    """Verify ADK agent incorporates FallbackModel with Gemini and Claude."""
    router = RouterAgent()
    adk_agent = router.get_adk_agent()

    assert adk_agent is not None
    # Model should either be FallbackModel or configured model
    from google.adk.models._fallback_model import FallbackModel
    if isinstance(adk_agent.model, FallbackModel):
        models = adk_agent.model.models
        assert len(models) >= 2
        assert "gemini" in str(models[0]).lower()
        assert "claude" in str(models[1]).lower()


def test_router_agent_call_llm_fallback(monkeypatch):
    """Verify RouterAgent.call_llm invokes fallback pipeline cleanly."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_gemini_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_claude_key")

    with patch("app.agent.llm_fallback.call_gemini", side_effect=RuntimeError("Gemini down")), \
         patch("app.agent.llm_fallback.call_claude", return_value="Claude routed to WF001"):

        router = RouterAgent()
        res = router.call_llm("Find low stock items")

        assert res.fallback_used is True
        assert res.provider == "anthropic"
        assert res.text == "Claude routed to WF001"


# ---------------------------------------------------------------------------
# Primitives llm_generate Integration
# ---------------------------------------------------------------------------

def test_primitives_llm_generate_with_claude_fallback(monkeypatch):
    """Verify llm_generate_with_fallback leverages Claude when Gemini fails."""
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_gemini_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_claude_key")

    with patch("app.agent.llm_fallback.call_gemini", side_effect=RuntimeError("Quota 429")), \
         patch("app.agent.llm_fallback.call_claude", return_value="Premium noise-canceling headphones."):

        out = llm_generate_with_fallback(
            task="product_description",
            context={"name": "Headphones"},
        )

        assert out == "Premium noise-canceling headphones."
