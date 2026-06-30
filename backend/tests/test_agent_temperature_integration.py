"""Tests for agent temperature integration in chat flow."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.interface.routes.chat import _build_agent_loop
from aio_agent_platform.db.models import LLMProvider, LLMModel


class TestAgentTemperatureIntegration:
    """Test that agent temperature is properly wired into the chat flow."""

    @pytest.mark.asyncio
    async def test_build_agent_loop_accepts_agent_temperature(self):
        """_build_agent_loop accepts agent_temperature parameter."""
        import inspect
        sig = inspect.signature(_build_agent_loop)
        params = sig.parameters

        assert 'agent_temperature' in params, (
            "_build_agent_loop is missing 'agent_temperature' parameter. "
            "Agent temperature override won't work."
        )

    @pytest.mark.asyncio
    async def test_build_agent_loop_uses_agent_temperature_over_global(self, db_session: AsyncSession):
        """When agent_temperature is provided, it overrides global setting."""
        # Create a test provider and model
        provider = LLMProvider(
            id=uuid.uuid4(),
            name="test-provider",
            provider_type="openai",
            base_url="http://test",
            api_key_encrypted="test-key",
        )
        db_session.add(provider)
        await db_session.flush()

        model = LLMModel(
            id=uuid.uuid4(),
            name="test-model",
            model_name="gpt-4",
            provider_id=provider.id,
            is_active=True,
            is_default=True,
        )
        db_session.add(model)
        await db_session.flush()

        # Mock the tool executor and create_provider
        mock_tool_executor = MagicMock()
        mock_provider_instance = MagicMock()

        with patch('aio_agent_platform.interface.routes.chat.create_provider') as mock_create:
            mock_create.return_value = mock_provider_instance

            # Call with agent_temperature=0.3
            await _build_agent_loop(
                tool_executor=mock_tool_executor,
                system_prompt="test",
                db=db_session,
                agent_model_id=model.id,
                agent_temperature=0.3,
            )

            # Verify create_provider was called with agent's temperature
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs['temperature'] == 0.3, (
                f"Expected temperature=0.3, got {call_kwargs.get('temperature')}. "
                "Agent temperature override is not being used."
            )

    @pytest.mark.asyncio
    async def test_build_agent_loop_falls_back_to_global_when_none(self, db_session: AsyncSession):
        """When agent_temperature is None, falls back to global setting."""
        from aio_agent_platform.core.config import settings

        provider = LLMProvider(
            id=uuid.uuid4(),
            name="test-provider-2",
            provider_type="openai",
            base_url="http://test",
            api_key_encrypted="test-key",
        )
        db_session.add(provider)
        await db_session.flush()

        model = LLMModel(
            id=uuid.uuid4(),
            name="test-model-2",
            model_name="gpt-4",
            provider_id=provider.id,
            is_active=True,
            is_default=True,
        )
        db_session.add(model)
        await db_session.flush()

        mock_tool_executor = MagicMock()
        mock_provider_instance = MagicMock()

        with patch('aio_agent_platform.interface.routes.chat.create_provider') as mock_create:
            mock_create.return_value = mock_provider_instance

            # Call with agent_temperature=None (should use global)
            await _build_agent_loop(
                tool_executor=mock_tool_executor,
                system_prompt="test",
                db=db_session,
                agent_model_id=model.id,
                agent_temperature=None,  # No override
            )

            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs['temperature'] == settings.llm.temperature, (
                f"Expected global temperature {settings.llm.temperature}, "
                f"got {call_kwargs.get('temperature')}. "
                "Global fallback is not working."
            )
