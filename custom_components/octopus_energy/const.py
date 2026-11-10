"""Constants for the Octopus Energy integration."""

DOMAIN = "octopus_energy"

CONF_API_KEY = "api_key"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_COMPARISON_PRODUCTS = "comparison_products"
CONF_COMPARISON_MONTHS = "comparison_months"
CONF_POSTCODE = "postcode"
CONF_GAS_METER_UNITS = "gas_meter_units"
CONF_GAS_CALORIFIC_VALUE = "gas_calorific_value"

GAS_UNITS_M3 = "m3"
GAS_UNITS_KWH = "kwh"

DEFAULT_UPDATE_INTERVAL = 30
DEFAULT_COMPARISON_MONTHS = 6
# SMETS2, the usual smart gas meter, reports m³; SMETS1 reports kWh.
DEFAULT_GAS_METER_UNITS = GAS_UNITS_M3
# MJ/m³. The real value varies by region and month; bills print the one used.
DEFAULT_GAS_CALORIFIC_VALUE = 39.5
# Standard temperature and pressure correction applied on every UK gas bill.
GAS_VOLUME_CORRECTION = 1.02264

CARBON_INTENSITY_API_URL = "https://api.carbonintensity.org.uk/intensity/date/{date}"

# Curated tariffs for comparison (product codes)
DEFAULT_COMPARISON_PRODUCTS: list[str] = [
    "AGILE-24-10-01",
    "VAR-22-11-01",
    "GO-VAR-22-10-14",
    "COSY-22-12-08",
    "SILVER-24-04-03",
]
