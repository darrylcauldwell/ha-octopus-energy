# Octopus Energy for Home Assistant

Home Assistant custom component for [Octopus Energy](https://octopus.energy), providing half-hourly consumption, rates, costs, and standing charges for electricity and gas meters.

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

## Sensors

The integration auto-discovers all meters on your account and creates sensors per meter:

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

Same consumption, cost, and standing charge sensors with `gas_` prefix.

### Attributes

Consumption and cost sensors include `charges[]` attribute with half-hourly breakdown (time, kWh, rate, cost per slot).

## Requirements

- Octopus Energy account with API access
- [aiooctopusenergy](https://github.com/darrylcauldwell/aiooctopusenergy) library (installed automatically)

## License

MIT
