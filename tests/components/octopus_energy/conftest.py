"""Test fixtures for Octopus Energy integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiooctopusenergy import (
    Account,
    Agreement,
    Consumption,
    ElectricityMeterPoint,
    GasMeterPoint,
    Meter,
    Property,
    Rate,
    StandingCharge,
)

MOCK_API_KEY = "sk_live_test_key_123"
MOCK_ACCOUNT_NUMBER = "A-AAAA1111"

MOCK_ACCOUNT = Account(
    number=MOCK_ACCOUNT_NUMBER,
    properties=[
        Property(
            id=12345,
            electricity_meter_points=[
                ElectricityMeterPoint(
                    mpan="1100009640372",
                    meters=[Meter(serial_number="22L4344979")],
                    agreements=[
                        Agreement(
                            tariff_code="E-1R-AGILE-24-10-01-C",
                            valid_from=datetime(2024, 10, 1, tzinfo=UTC),
                        ),
                    ],
                    is_export=False,
                ),
                ElectricityMeterPoint(
                    mpan="1170001806920",
                    meters=[Meter(serial_number="22L4344979")],
                    agreements=[
                        Agreement(
                            tariff_code="E-1R-OUTGOING-FIX-12M-19-05-13-C",
                            valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                        ),
                    ],
                    is_export=True,
                ),
            ],
            gas_meter_points=[
                GasMeterPoint(
                    mprn="2112316000",
                    meters=[Meter(serial_number="E6E07422582221")],
                    agreements=[
                        Agreement(
                            tariff_code="G-1R-VAR-22-11-01-C",
                            valid_from=datetime(2022, 11, 1, tzinfo=UTC),
                        ),
                    ],
                ),
            ],
        ),
    ],
)

NOW = datetime.now(UTC)
YESTERDAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)

MOCK_RATES = [
    Rate(
        value_exc_vat=19.54,
        value_inc_vat=20.517,
        valid_from=NOW - timedelta(minutes=15),
        valid_to=NOW + timedelta(minutes=15),
    ),
    Rate(
        value_exc_vat=21.0,
        value_inc_vat=22.05,
        valid_from=NOW + timedelta(minutes=15),
        valid_to=NOW + timedelta(minutes=45),
    ),
]

MOCK_CONSUMPTION = [
    Consumption(
        consumption=0.5,
        interval_start=YESTERDAY,
        interval_end=YESTERDAY + timedelta(minutes=30),
    ),
    Consumption(
        consumption=0.3,
        interval_start=YESTERDAY + timedelta(minutes=30),
        interval_end=YESTERDAY + timedelta(hours=1),
    ),
]

MOCK_STANDING_CHARGES = [
    StandingCharge(
        value_exc_vat=37.65,
        value_inc_vat=39.53,
        valid_from=datetime(2024, 10, 1, tzinfo=UTC),
    ),
]


@pytest.fixture
def mock_client():
    """Create a mock OctopusEnergyClient."""
    with patch(
        "custom_components.octopus_energy.OctopusEnergyClient"
    ) as mock_cls:
        client = mock_cls.return_value
        client.get_account = AsyncMock(return_value=MOCK_ACCOUNT)
        client.get_electricity_rates = AsyncMock(return_value=MOCK_RATES)
        client.get_electricity_consumption = AsyncMock(return_value=MOCK_CONSUMPTION)
        client.get_electricity_standing_charges = AsyncMock(
            return_value=MOCK_STANDING_CHARGES
        )
        client.get_gas_rates = AsyncMock(return_value=MOCK_RATES)
        client.get_gas_consumption = AsyncMock(return_value=MOCK_CONSUMPTION)
        client.get_gas_standing_charges = AsyncMock(
            return_value=MOCK_STANDING_CHARGES
        )
        yield client


@pytest.fixture
def mock_config_flow_client():
    """Create a mock client for config flow tests."""
    with patch(
        "custom_components.octopus_energy.config_flow.OctopusEnergyClient"
    ) as mock_cls:
        client = mock_cls.return_value
        client.get_account = AsyncMock(return_value=MOCK_ACCOUNT)
        yield client


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        "api_key": MOCK_API_KEY,
        "account_number": MOCK_ACCOUNT_NUMBER,
    }
    entry.options = {}
    entry.as_dict.return_value = {
        "data": entry.data,
        "options": entry.options,
    }
    return entry
