"""Tests for Octopus Energy config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiooctopusenergy import (
    OctopusEnergyAuthenticationError,
    OctopusEnergyConnectionError,
    OctopusEnergyNotFoundError,
)

from custom_components.octopus_energy.config_flow import _validate_credentials

from .conftest import MOCK_ACCOUNT, MOCK_ACCOUNT_NUMBER, MOCK_API_KEY


@pytest.fixture
def mock_validate_client():
    """Mock both the client and the session helper for config flow validation."""
    with (
        patch(
            "custom_components.octopus_energy.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.octopus_energy.config_flow.OctopusEnergyClient"
        ) as mock_cls,
    ):
        client = mock_cls.return_value
        client.get_account = AsyncMock(return_value=MOCK_ACCOUNT)
        yield client


class TestValidateCredentials:
    @pytest.mark.asyncio
    async def test_valid_credentials(self, mock_validate_client):
        mock_hass = AsyncMock()
        errors = await _validate_credentials(
            mock_hass, MOCK_API_KEY, MOCK_ACCOUNT_NUMBER
        )
        assert errors == {}

    @pytest.mark.asyncio
    async def test_invalid_auth(self, mock_validate_client):
        mock_validate_client.get_account = AsyncMock(
            side_effect=OctopusEnergyAuthenticationError("Invalid key")
        )
        mock_hass = AsyncMock()
        errors = await _validate_credentials(
            mock_hass, "bad_key", MOCK_ACCOUNT_NUMBER
        )
        assert errors == {"base": "invalid_auth"}

    @pytest.mark.asyncio
    async def test_account_not_found(self, mock_validate_client):
        mock_validate_client.get_account = AsyncMock(
            side_effect=OctopusEnergyNotFoundError("Not found")
        )
        mock_hass = AsyncMock()
        errors = await _validate_credentials(
            mock_hass, MOCK_API_KEY, "A-XXXX0000"
        )
        assert errors == {"base": "account_not_found"}

    @pytest.mark.asyncio
    async def test_cannot_connect(self, mock_validate_client):
        mock_validate_client.get_account = AsyncMock(
            side_effect=OctopusEnergyConnectionError("Connection refused")
        )
        mock_hass = AsyncMock()
        errors = await _validate_credentials(
            mock_hass, MOCK_API_KEY, MOCK_ACCOUNT_NUMBER
        )
        assert errors == {"base": "cannot_connect"}

    @pytest.mark.asyncio
    async def test_unknown_error(self, mock_validate_client):
        mock_validate_client.get_account = AsyncMock(
            side_effect=RuntimeError("Unexpected")
        )
        mock_hass = AsyncMock()
        errors = await _validate_credentials(
            mock_hass, MOCK_API_KEY, MOCK_ACCOUNT_NUMBER
        )
        assert errors == {"base": "unknown"}
