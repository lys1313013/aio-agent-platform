"""Tests for agent Pydantic schemas."""

import pytest
from pydantic import ValidationError

from aio_agent_platform.interface.routes.agents import AgentCreate, AgentUpdate, AgentOut


class TestAgentSchemas:
    """Test Pydantic schemas for agents."""

    def test_agent_create_with_temperature(self):
        """AgentCreate accepts temperature field."""
        data = {
            "name": "Test Agent",
            "temperature": 0.8,
        }
        schema = AgentCreate(**data)
        assert schema.temperature == 0.8

    def test_agent_create_with_welcome_message(self):
        """AgentCreate accepts welcome_message field."""
        data = {
            "name": "Test Agent",
            "welcome_message": "Hello!",
        }
        schema = AgentCreate(**data)
        assert schema.welcome_message == "Hello!"

    def test_agent_create_temperature_null(self):
        """AgentCreate accepts null temperature."""
        data = {
            "name": "Test Agent",
            "temperature": None,
        }
        schema = AgentCreate(**data)
        assert schema.temperature is None

    def test_agent_create_temperature_validation_too_high(self):
        """AgentCreate rejects temperature > 2.0."""
        data = {
            "name": "Test Agent",
            "temperature": 2.5,
        }
        with pytest.raises(ValidationError) as exc_info:
            AgentCreate(**data)
        assert "temperature" in str(exc_info.value).lower()

    def test_agent_create_temperature_validation_negative(self):
        """AgentCreate rejects negative temperature."""
        data = {
            "name": "Test Agent",
            "temperature": -0.1,
        }
        with pytest.raises(ValidationError) as exc_info:
            AgentCreate(**data)
        assert "temperature" in str(exc_info.value).lower()

    def test_agent_create_temperature_boundary_zero(self):
        """AgentCreate accepts temperature = 0.0."""
        data = {
            "name": "Test Agent",
            "temperature": 0.0,
        }
        schema = AgentCreate(**data)
        assert schema.temperature == 0.0

    def test_agent_create_temperature_boundary_max(self):
        """AgentCreate accepts temperature = 2.0."""
        data = {
            "name": "Test Agent",
            "temperature": 2.0,
        }
        schema = AgentCreate(**data)
        assert schema.temperature == 2.0

    def test_agent_update_with_temperature(self):
        """AgentUpdate accepts temperature field."""
        data = {"temperature": 0.6}
        schema = AgentUpdate(**data)
        assert schema.temperature == 0.6

    def test_agent_update_with_welcome_message(self):
        """AgentUpdate accepts welcome_message field."""
        data = {"welcome_message": "Updated welcome"}
        schema = AgentUpdate(**data)
        assert schema.welcome_message == "Updated welcome"

    def test_agent_update_is_set_temperature(self):
        """AgentUpdate.is_set detects explicitly set temperature."""
        schema = AgentUpdate(temperature=0.5)
        assert schema.is_set("temperature")

    def test_agent_update_is_set_temperature_not_provided(self):
        """AgentUpdate.is_set returns False when temperature not provided."""
        schema = AgentUpdate(name="Test")
        assert not schema.is_set("temperature")

    def test_agent_update_is_set_temperature_null(self):
        """AgentUpdate.is_set returns True when temperature explicitly set to null."""
        schema = AgentUpdate(temperature=None)
        assert schema.is_set("temperature")

    def test_agent_out_with_temperature(self):
        """AgentOut includes temperature field."""
        import uuid
        data = {
            "id": uuid.uuid4(),
            "name": "Test Agent",
            "temperature": 0.7,
        }
        schema = AgentOut(**data)
        assert schema.temperature == 0.7

    def test_agent_out_with_welcome_message(self):
        """AgentOut includes welcome_message field."""
        import uuid
        data = {
            "id": uuid.uuid4(),
            "name": "Test Agent",
            "welcome_message": "Welcome!",
        }
        schema = AgentOut(**data)
        assert schema.welcome_message == "Welcome!"

    def test_agent_out_defaults(self):
        """AgentOut fields default to None."""
        import uuid
        data = {
            "id": uuid.uuid4(),
            "name": "Test Agent",
        }
        schema = AgentOut(**data)
        assert schema.temperature is None
        assert schema.welcome_message is None
