"""Constants for the Octopus Energy integration."""

DOMAIN = "octopus_energy"

CONF_API_KEY = "api_key"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_COMPARISON_PRODUCTS = "comparison_products"
CONF_COMPARISON_MONTHS = "comparison_months"
CONF_POSTCODE = "postcode"

DEFAULT_UPDATE_INTERVAL = 30
DEFAULT_COMPARISON_MONTHS = 6

# Curated tariffs for comparison (product codes)
DEFAULT_COMPARISON_PRODUCTS: list[str] = [
    "AGILE-24-10-01",
    "VAR-22-11-01",
    "GO-VAR-22-10-14",
    "COSY-22-12-08",
    "SILVER-24-04-03",
]
