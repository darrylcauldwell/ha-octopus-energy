# Octopus Energy for Home Assistant

Home Assistant custom component for [Octopus Energy](https://octopus.energy), providing half-hourly consumption, rates, costs, standing charges, tariff comparison, carbon intensity correlation, and solar generation estimates.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Install "Octopus Energy"
3. Restart Home Assistant

### Manual

1. Copy `custom_components/octopus_energy/` to your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for "Octopus Energy"
3. Enter your API key (from [octopus.energy/dashboard/developer](https://octopus.energy/dashboard/developer/))
4. Enter your account number (e.g. `A-AAAA1111`)
5. Optionally enter your postcode for solar estimates and carbon data

## Sensors

The integration auto-discovers all meters on your account and creates sensors per meter.

### Per electricity meter (import)

| Sensor | Unit | Description |
|--------|------|-------------|
| Current rate | p/kWh | Current half-hourly unit rate (inc. VAT) |
| Next rate | p/kWh | Next half-hourly unit rate |
| Previous consumption | kWh | Total yesterday consumption |
| Previous cost | GBP | Total yesterday cost |
| Standing charge | p/day | Current daily standing charge |

### Per electricity meter (export)

Same sensors with `export_` prefix.

### Per gas meter

| Sensor | Unit | Description |
|--------|------|-------------|
| Gas current rate | p/kWh | Current gas unit rate (inc. VAT) |
| Gas previous consumption | kWh | Total yesterday gas consumption |
| Gas previous cost | GBP | Total yesterday gas cost |
| Gas standing charge | p/day | Current daily gas standing charge |

### Tariff comparison

| Sensor | Unit | Description |
|--------|------|-------------|
| Tariff comparison | GBP | Cheapest alternative tariff cost. Attributes include full comparison table with current cost and all alternative tariffs. |

Uses 6 months of historical consumption to calculate what each available tariff would have cost. Updated every 24 hours with incremental backfill to avoid API rate limits.

### Carbon intensity correlation

| Sensor | Unit | Description |
|--------|------|-------------|
| Carbon correlation | gCO2/kWh | Weighted average carbon intensity of your consumption |

Correlates your half-hourly consumption with carbon intensity data from the National Grid ESO API.

### Solar generation estimate

| Sensor | Unit | Description |
|--------|------|-------------|
| Solar estimate | kWh | Estimated solar generation today |

Hourly solar generation estimates from Octopus Energy's GraphQL API based on your postcode.

### Sensor attributes

Consumption and cost sensors include a `charges[]` attribute with half-hourly breakdown (time, kWh, rate, cost per slot).

## Rate limiting

The integration uses several strategies to avoid hitting Octopus API rate limits:

- **Chunked backfill**: Historical consumption is fetched in 14-day chunks, max 3 per cycle
- **Inter-request delays**: 3-second gaps between API calls within a cycle
- **Dynamic update interval**: 10 minutes during backfill, 30 minutes after a 429, 24 hours when fully populated
- **Startup staggering**: 10-second delays between coordinator first refreshes
- **Date-filtered rate queries**: Rate fetches use a 3-day window to avoid paginating large Agile rate histories

## Meter selection

The Octopus API may list multiple meter serials per MPAN (legacy and active meters). The integration selects:

- **Electricity**: last meter in list (`meters[-1]`) — typically the active smart meter
- **Gas**: first meter in list (`meters[0]`)

## Requirements

- Octopus Energy account with API access
- [aiooctopusenergy](https://pypi.org/project/aiooctopusenergy/) library (installed automatically)

## License

MIT
