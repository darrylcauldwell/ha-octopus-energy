"""Tests for the solar estimate coordinator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiooctopusenergy import SolarEstimate

from custom_components.octopus_energy.solar_coordinator import (
    SolarEstimateCoordinator,
    SolarEstimateData,
)


MOCK_POSTCODE = "DE45 1AB"

MOCK_SOLAR_ESTIMATES = [
    SolarEstimate(date="2026-03-06", hour=8, value=0.12),
    SolarEstimate(date="2026-03-06", hour=9, value=0.35),
    SolarEstimate(date="2026-03-06", hour=10, value=0.58),
    SolarEstimate(date="2026-03-06", hour=11, value=0.72),
    SolarEstimate(date="2026-03-06", hour=12, value=0.85),
    SolarEstimate(date="2026-03-06", hour=13, value=0.78),
    SolarEstimate(date="2026-03-06", hour=14, value=0.55),
    SolarEstimate(date="2026-03-06", hour=15, value=0.30),
    SolarEstimate(date="2026-03-06", hour=16, value=0.10),
]


@pytest.fixture
def mock_graphql_client():
    """Create a mock GraphQL client."""
    client = MagicMock()
    client.get_solar_generation_estimate = AsyncMock(
        return_value=MOCK_SOLAR_ESTIMATES
    )
    return client


def _make_coordinator(graphql_client, postcode=MOCK_POSTCODE):
    """Create a SolarEstimateCoordinator with mocked base class init."""
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__"
    ):
        coordinator = SolarEstimateCoordinator.__new__(SolarEstimateCoordinator)
        coordinator._graphql_client = graphql_client
        coordinator._postcode = postcode
    return coordinator


class TestSolarEstimateCoordinator:
    @pytest.mark.asyncio
    async def test_update_fetches_estimates(self, mock_graphql_client):
        coordinator = _make_coordinator(mock_graphql_client)

        with patch(
            "custom_components.octopus_energy.solar_coordinator.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = await coordinator._async_update_data()

        assert isinstance(result, SolarEstimateData)
        assert result.today_total_kwh == round(
            sum(e.value for e in MOCK_SOLAR_ESTIMATES), 2
        )
        assert len(result.hourly_estimates) == 9
        assert result.updated_at != ""

        mock_graphql_client.get_solar_generation_estimate.assert_called_once()
        call_args = mock_graphql_client.get_solar_generation_estimate.call_args
        assert call_args.args[0] == MOCK_POSTCODE

    @pytest.mark.asyncio
    async def test_update_filters_today(self, mock_graphql_client):
        """Test that today_total_kwh only sums today's estimates."""
        mixed_estimates = [
            SolarEstimate(date="2026-03-06", hour=10, value=0.5),
            SolarEstimate(date="2026-03-06", hour=11, value=0.7),
            SolarEstimate(date="2026-03-07", hour=10, value=0.9),
        ]
        mock_graphql_client.get_solar_generation_estimate = AsyncMock(
            return_value=mixed_estimates
        )
        coordinator = _make_coordinator(mock_graphql_client)

        with patch(
            "custom_components.octopus_energy.solar_coordinator.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = await coordinator._async_update_data()

        # Only today's estimates (0.5 + 0.7 = 1.2)
        assert result.today_total_kwh == 1.2
        # But all estimates are stored
        assert len(result.hourly_estimates) == 3

    @pytest.mark.asyncio
    async def test_update_empty_estimates(self, mock_graphql_client):
        mock_graphql_client.get_solar_generation_estimate = AsyncMock(
            return_value=[]
        )
        coordinator = _make_coordinator(mock_graphql_client)

        with patch(
            "custom_components.octopus_energy.solar_coordinator.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = await coordinator._async_update_data()

        assert result.today_total_kwh == 0.0
        assert result.hourly_estimates == []

    @pytest.mark.asyncio
    async def test_update_api_error_raises(self, mock_graphql_client):
        from homeassistant.helpers.update_coordinator import UpdateFailed

        mock_graphql_client.get_solar_generation_estimate = AsyncMock(
            side_effect=Exception("API error")
        )
        coordinator = _make_coordinator(mock_graphql_client)

        with patch(
            "custom_components.octopus_energy.solar_coordinator.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            with pytest.raises(UpdateFailed, match="Failed to fetch solar estimates"):
                await coordinator._async_update_data()
