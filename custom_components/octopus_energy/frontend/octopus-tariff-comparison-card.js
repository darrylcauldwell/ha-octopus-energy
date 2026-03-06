/**
 * Octopus Tariff Comparison Card
 *
 * Displays a grouped bar chart and summary table comparing monthly
 * electricity costs across different Octopus Energy tariffs.
 */

const CARD_VERSION = "0.2.0";

const TARIFF_COLORS = [
  "#4CAF50", // Green (current)
  "#2196F3", // Blue
  "#FF9800", // Orange
  "#9C27B0", // Purple
  "#F44336", // Red
  "#00BCD4", // Cyan
  "#795548", // Brown
  "#607D8B", // Blue Grey
];

class OctopusTariffComparisonCard extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    this._render();
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("Please define an entity");
    }
    this._config = config;
    this._rendered = false;
  }

  getCardSize() {
    return 6;
  }

  static getConfigElement() {
    return document.createElement("octopus-tariff-comparison-card-editor");
  }

  static getStubConfig() {
    return { entity: "" };
  }

  _render() {
    if (!this._hass || !this._config) return;

    const entityId = this._config.entity;
    const stateObj = this._hass.states[entityId];
    if (!stateObj) {
      this._renderError("Entity not found: " + entityId);
      return;
    }

    const attrs = stateObj.attributes;
    if (!attrs || !attrs.tariffs || !attrs.months) {
      this._renderError("No comparison data available yet");
      return;
    }

    const tariffs = attrs.tariffs.filter((t) => !t.error);
    const months = attrs.months;
    const title = this._config.title || "Tariff Comparison";

    if (tariffs.length === 0) {
      this._renderError("No valid tariff data available");
      return;
    }

    // Find max cost for chart scaling
    let maxCost = 0;
    for (const tariff of tariffs) {
      for (const month of tariff.months) {
        if (month.total_cost > maxCost) maxCost = month.total_cost;
      }
    }
    maxCost = Math.ceil(maxCost / 10) * 10; // Round up to nearest 10

    const chartHeight = 200;
    const barGroupWidth = 100 / months.length;

    this.innerHTML = `
      <ha-card header="${title}">
        <div class="card-content">
          ${this._renderChart(tariffs, months, maxCost, chartHeight, barGroupWidth)}
          ${this._renderLegend(tariffs)}
          ${this._renderSummaryTable(tariffs, attrs)}
          ${this._renderCoverageWarnings(tariffs)}
          <div class="updated-at">
            Updated: ${attrs.updated_at ? new Date(attrs.updated_at).toLocaleString() : "N/A"}
          </div>
        </div>
      </ha-card>
      <style>
        ${this._getStyles()}
      </style>
    `;
  }

  _renderChart(tariffs, months, maxCost, chartHeight, barGroupWidth) {
    if (maxCost === 0) return '<div class="chart-empty">No cost data</div>';

    const yLabels = [];
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const val = Math.round((maxCost / steps) * i);
      const pct = (i / steps) * 100;
      yLabels.push(
        `<div class="y-label" style="bottom: ${pct}%">${val}</div>`
      );
      yLabels.push(
        `<div class="y-grid" style="bottom: ${pct}%"></div>`
      );
    }

    const groups = months.map((month, mi) => {
      const bars = tariffs.map((tariff, ti) => {
        const monthData = tariff.months[mi];
        if (!monthData) return "";
        const heightPct = maxCost > 0 ? (monthData.total_cost / maxCost) * 100 : 0;
        const color = TARIFF_COLORS[ti % TARIFF_COLORS.length];
        return `<div class="bar" style="height: ${heightPct}%; background: ${color};"
                  title="${tariff.display_name}: £${monthData.total_cost.toFixed(2)}"></div>`;
      });

      const label = this._formatMonth(month);
      return `
        <div class="bar-group" style="width: ${barGroupWidth}%">
          <div class="bars">${bars.join("")}</div>
          <div class="month-label">${label}</div>
        </div>
      `;
    });

    return `
      <div class="chart-container">
        <div class="y-axis">
          <div class="y-axis-label">Cost (£)</div>
          ${yLabels.join("")}
        </div>
        <div class="chart" style="height: ${chartHeight}px">
          ${groups.join("")}
        </div>
      </div>
    `;
  }

  _renderLegend(tariffs) {
    const items = tariffs.map((t, i) => {
      const color = TARIFF_COLORS[i % TARIFF_COLORS.length];
      return `
        <div class="legend-item">
          <span class="legend-swatch" style="background: ${color}"></span>
          <span class="legend-label">${t.display_name}</span>
        </div>
      `;
    });
    return `<div class="legend">${items.join("")}</div>`;
  }

  _renderSummaryTable(tariffs, attrs) {
    // Sort by total cost
    const sorted = [...tariffs].sort((a, b) => a.total_cost - b.total_cost);
    const currentTariff = sorted.find((t) => t.is_current);
    const currentCost = currentTariff ? currentTariff.total_cost : 0;
    const cheapest = sorted[0];

    const rows = sorted.map((t, i) => {
      const savings = currentCost - t.total_cost;
      const savingsClass = savings > 0 ? "savings-positive" : savings < 0 ? "savings-negative" : "";
      const savingsText = savings === 0 ? "-" : `${savings > 0 ? "-" : "+"}£${Math.abs(savings).toFixed(2)}`;
      const highlight = t === cheapest ? 'class="cheapest"' : "";

      return `
        <tr ${highlight}>
          <td>${t.display_name}</td>
          <td class="cost">£${t.total_cost.toFixed(2)}</td>
          <td class="savings ${savingsClass}">${savingsText}</td>
        </tr>
      `;
    });

    return `
      <table class="summary-table">
        <thead>
          <tr>
            <th>Tariff</th>
            <th>Total Cost</th>
            <th>vs Current</th>
          </tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>
      <div class="consumption-note">
        Total consumption: ${attrs.total_consumption_kwh} kWh | Region: ${attrs.gsp_region}
      </div>
    `;
  }

  _renderCoverageWarnings(tariffs) {
    const warnings = [];
    for (const tariff of tariffs) {
      for (const month of tariff.months) {
        const coverage = month.days_in_month > 0 ? month.days_with_data / month.days_in_month : 0;
        if (coverage < 0.9 && coverage > 0) {
          warnings.push(
            `${this._formatMonth(month.month)}: ${month.days_with_data}/${month.days_in_month} days`
          );
        }
      }
    }
    if (warnings.length === 0) return "";

    // Deduplicate
    const unique = [...new Set(warnings)];
    return `
      <div class="coverage-warning">
        Incomplete data: ${unique.join(", ")}
      </div>
    `;
  }

  _formatMonth(monthStr) {
    const [year, month] = monthStr.split("-");
    const date = new Date(parseInt(year), parseInt(month) - 1);
    return date.toLocaleString("default", { month: "short" });
  }

  _renderError(message) {
    this.innerHTML = `
      <ha-card>
        <div class="card-content">
          <div class="error">${message}</div>
        </div>
      </ha-card>
    `;
  }

  _getStyles() {
    return `
      ha-card {
        padding: 0;
      }
      .card-content {
        padding: 0 16px 16px;
      }
      .chart-container {
        display: flex;
        margin-bottom: 12px;
      }
      .y-axis {
        width: 40px;
        position: relative;
        flex-shrink: 0;
      }
      .y-axis-label {
        font-size: 10px;
        color: var(--secondary-text-color);
        transform: rotate(-90deg);
        position: absolute;
        left: -10px;
        top: 50%;
        white-space: nowrap;
      }
      .y-label {
        position: absolute;
        right: 4px;
        font-size: 10px;
        color: var(--secondary-text-color);
        transform: translateY(50%);
      }
      .y-grid {
        position: absolute;
        left: 36px;
        right: 0;
        height: 1px;
        background: var(--divider-color);
        opacity: 0.3;
      }
      .chart {
        flex: 1;
        display: flex;
        align-items: flex-end;
        border-bottom: 1px solid var(--divider-color);
        border-left: 1px solid var(--divider-color);
        position: relative;
      }
      .chart-empty {
        text-align: center;
        color: var(--secondary-text-color);
        padding: 40px;
      }
      .bar-group {
        display: flex;
        flex-direction: column;
        align-items: center;
      }
      .bars {
        display: flex;
        align-items: flex-end;
        gap: 1px;
        width: 80%;
        height: 100%;
      }
      .bar {
        flex: 1;
        min-width: 4px;
        border-radius: 2px 2px 0 0;
        transition: opacity 0.2s;
        cursor: pointer;
      }
      .bar:hover {
        opacity: 0.8;
      }
      .month-label {
        font-size: 11px;
        color: var(--secondary-text-color);
        margin-top: 4px;
        text-align: center;
      }
      .legend {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 12px;
        justify-content: center;
      }
      .legend-item {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 11px;
      }
      .legend-swatch {
        width: 12px;
        height: 12px;
        border-radius: 2px;
        flex-shrink: 0;
      }
      .summary-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        margin-bottom: 8px;
      }
      .summary-table th {
        text-align: left;
        padding: 6px 8px;
        border-bottom: 2px solid var(--divider-color);
        color: var(--secondary-text-color);
        font-weight: 500;
      }
      .summary-table td {
        padding: 6px 8px;
        border-bottom: 1px solid var(--divider-color);
      }
      .summary-table .cost {
        font-weight: 500;
        text-align: right;
      }
      .summary-table th:nth-child(2),
      .summary-table th:nth-child(3) {
        text-align: right;
      }
      .summary-table .savings {
        text-align: right;
      }
      .savings-positive {
        color: var(--success-color, #4CAF50);
        font-weight: 500;
      }
      .savings-negative {
        color: var(--error-color, #F44336);
      }
      tr.cheapest td {
        background: var(--success-color, #4CAF50);
        background: color-mix(in srgb, var(--success-color, #4CAF50) 10%, transparent);
      }
      .consumption-note {
        font-size: 11px;
        color: var(--secondary-text-color);
        text-align: center;
        margin-top: 4px;
      }
      .coverage-warning {
        font-size: 11px;
        color: var(--warning-color, #FF9800);
        margin-top: 8px;
        padding: 4px 8px;
        border-radius: 4px;
        background: color-mix(in srgb, var(--warning-color, #FF9800) 10%, transparent);
      }
      .updated-at {
        font-size: 10px;
        color: var(--secondary-text-color);
        text-align: right;
        margin-top: 8px;
      }
      .error {
        color: var(--error-color, #F44336);
        text-align: center;
        padding: 20px;
      }
    `;
  }
}

// Editor for card configuration
class OctopusTariffComparisonCardEditor extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
  }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  _render() {
    this.innerHTML = `
      <div style="padding: 16px;">
        <label>Entity:</label><br>
        <input type="text" id="entity" value="${this._config.entity || ""}"
               style="width: 100%; margin-bottom: 8px; padding: 4px;">
        <label>Title (optional):</label><br>
        <input type="text" id="title" value="${this._config.title || ""}"
               style="width: 100%; padding: 4px;">
      </div>
    `;

    this.querySelector("#entity").addEventListener("change", (e) => {
      this._config = { ...this._config, entity: e.target.value };
      this._dispatch();
    });
    this.querySelector("#title").addEventListener("change", (e) => {
      this._config = { ...this._config, title: e.target.value };
      this._dispatch();
    });
  }

  _dispatch() {
    this.dispatchEvent(
      new CustomEvent("config-changed", { detail: { config: this._config } })
    );
  }
}

customElements.define(
  "octopus-tariff-comparison-card",
  OctopusTariffComparisonCard
);
customElements.define(
  "octopus-tariff-comparison-card-editor",
  OctopusTariffComparisonCardEditor
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "octopus-tariff-comparison-card",
  name: "Octopus Tariff Comparison",
  description: "Compare monthly electricity costs across Octopus Energy tariffs",
  preview: false,
});

console.info(
  `%c OCTOPUS-TARIFF-COMPARISON-CARD %c v${CARD_VERSION} `,
  "color: white; background: #4CAF50; font-weight: bold;",
  "color: #4CAF50; background: white; font-weight: bold;"
);
