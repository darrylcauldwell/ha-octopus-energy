"""Tests for Octopus Energy coordinator."""

from __future__ import annotations

import pytest

from custom_components.octopus_energy.coordinator import (
    _extract_product_code,
    _get_active_agreement,
)

from .conftest import MOCK_ACCOUNT


class TestExtractProductCode:
    def test_agile_tariff(self):
        assert _extract_product_code("E-1R-AGILE-24-10-01-C") == "AGILE-24-10-01"

    def test_variable_tariff(self):
        assert _extract_product_code("G-1R-VAR-22-11-01-C") == "VAR-22-11-01"

    def test_export_tariff(self):
        result = _extract_product_code("E-1R-OUTGOING-FIX-12M-19-05-13-C")
        assert result == "OUTGOING-FIX-12M-19-05-13"

    def test_short_code_passthrough(self):
        assert _extract_product_code("ABC") == "ABC"


class TestGetActiveAgreement:
    def test_active_agreement(self):
        agreements = MOCK_ACCOUNT.properties[0].electricity_meter_points[0].agreements
        agreement = _get_active_agreement(agreements)
        assert agreement is not None
        assert agreement.tariff_code == "E-1R-AGILE-24-10-01-C"

    def test_empty_agreements(self):
        assert _get_active_agreement([]) is None
