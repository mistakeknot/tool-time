// tool-time Community Dashboard
// All data rendered via textContent or Chart.js (no innerHTML)

const API_BASE = "/v1/api/stats";
let selectedClients = ["claude-code", "codex", "openclaw"];

// Track chart instances for cleanup on re-render
const chartInstances = {};

function getOrCreateChart(canvasId, config) {
  if (chartInstances[canvasId]) {
    chartInstances[canvasId].destroy();
  }
  const instance = new Chart(document.getElementById(canvasId), config);
  chartInstances[canvasId] = instance;
  return instance;
}

async function loadDashboard() {
  try {
    const allClients = ["claude-code", "codex", "openclaw"];
    const isAll = selectedClients.length === allClients.length;
    const url = isAll ? API_BASE : `${API_BASE}?client=${selectedClients.join(",")}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderOverview(data.overview);
    renderToolsChart(data.tools);
    renderToolsAdoptionChart(data.tools, data.overview);
    renderErrorsChart(data.tools);
    renderModelsChart(data.models);
    if (data.dimensions) {
      renderSkillsChart(data.dimensions.skills);
      renderMcpChart(data.dimensions.mcp_servers);
      renderPluginsChart(data.dimensions.plugins);
    }
  } catch (err) {
    document.getElementById("subtitle").textContent =
      "Unable to load community data. Try again later.";
  }
}

function renderOverview(overview) {
  const el = (id) => document.getElementById(id);
  el("total-submissions").textContent = (overview.total_submissions ?? 0).toLocaleString();
  el("unique-submitters").textContent = (overview.unique_submitters ?? 0).toLocaleString();
}

function renderToolsChart(tools) {
  if (!tools.length) return;
  const top = tools.slice(0, 20);
  getOrCreateChart("tools-chart", {
    type: "bar",
    data: {
      labels: top.map((t) => t.tool_name),
      datasets: [
        {
          label: "Avg Calls / User",
          data: top.map((t) => t.avg_calls_per_submitter),
          backgroundColor: "#58a6ff",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
        y: { ticks: { color: "#c9d1d9" }, grid: { display: false } },
      },
    },
  });
}

function renderToolsAdoptionChart(tools, overview) {
  const totalUsers = overview.unique_submitters || 0;
  if (!tools.length || !totalUsers) {
    document.getElementById("tools-adoption-section").style.display = "none";
    return;
  }
  document.getElementById("tools-adoption-section").dataset.hasData = "true";
  document.getElementById("tools-adoption-section").style.display = "";
  const top = tools.slice(0, 20);
  getOrCreateChart("tools-adoption-chart", {
    type: "bar",
    data: {
      labels: top.map((t) => t.tool_name),
      datasets: [
        {
          label: "% of Users",
          data: top.map((t) =>
            ((t.unique_submitters / totalUsers) * 100).toFixed(1)
          ),
          backgroundColor: "#79c0ff",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: "#8b949e", callback: (v) => v + "%" },
          grid: { color: "#21262d" },
          max: 100,
        },
        y: { ticks: { color: "#c9d1d9" }, grid: { display: false } },
      },
    },
  });
}

function renderErrorsChart(tools) {
  if (!tools.length) return;
  const withErrors = tools
    .filter((t) => t.total_calls > 0)
    .map((t) => ({
      name: t.tool_name,
      rate: (t.total_errors / t.total_calls) * 100,
    }))
    .filter((t) => t.rate > 0)
    .sort((a, b) => b.rate - a.rate)
    .slice(0, 15);

  if (!withErrors.length) return;

  getOrCreateChart("errors-chart", {
    type: "bar",
    data: {
      labels: withErrors.map((t) => t.name),
      datasets: [
        {
          label: "Error Rate %",
          data: withErrors.map((t) => t.rate.toFixed(1)),
          backgroundColor: "#f85149",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: "#8b949e", callback: (v) => v + "%" },
          grid: { color: "#21262d" },
        },
        y: { ticks: { color: "#c9d1d9" }, grid: { display: false } },
      },
    },
  });
}

function renderModelsChart(models) {
  if (!models.length) return;
  const total = models.reduce((s, m) => s + m.count, 0);
  getOrCreateChart("models-chart", {
    type: "doughnut",
    data: {
      labels: models.map((m) => m.model),
      datasets: [
        {
          data: models.map((m) => m.count),
          backgroundColor: ["#58a6ff", "#3fb950", "#d2a8ff", "#f0883e", "#f85149"],
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: "bottom", labels: { color: "#c9d1d9" } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const pct = ((ctx.raw / total) * 100).toFixed(1);
              return `${ctx.label}: ${pct}%`;
            },
          },
        },
      },
    },
  });
}

function renderSkillsChart(skills) {
  if (!skills || !skills.length) {
    document.getElementById("skills-section").dataset.hasData = "false";
    return;
  }
  document.getElementById("skills-section").dataset.hasData = "true";
  document.getElementById("skills-section").style.display = "";
  getOrCreateChart("skills-chart", {
    type: "bar",
    data: {
      labels: skills.map((s) => s.name),
      datasets: [
        {
          label: "Avg Calls / User",
          data: skills.map((s) => s.avg_calls_per_submitter),
          backgroundColor: "#3fb950",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
        y: { ticks: { color: "#c9d1d9" }, grid: { display: false } },
      },
    },
  });
}

function renderMcpChart(mcpServers) {
  if (!mcpServers || !mcpServers.length) {
    document.getElementById("mcp-section").dataset.hasData = "false";
    return;
  }
  document.getElementById("mcp-section").dataset.hasData = "true";
  document.getElementById("mcp-section").style.display = "";
  getOrCreateChart("mcp-chart", {
    type: "bar",
    data: {
      labels: mcpServers.map((m) => m.name),
      datasets: [
        {
          label: "Avg Calls / User",
          data: mcpServers.map((m) => m.avg_calls_per_submitter),
          backgroundColor: "#d2a8ff",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
        y: { ticks: { color: "#c9d1d9" }, grid: { display: false } },
      },
    },
  });
}

function renderPluginsChart(plugins) {
  if (!plugins || !plugins.length) {
    document.getElementById("plugins-section").dataset.hasData = "false";
    return;
  }
  document.getElementById("plugins-section").dataset.hasData = "true";
  document.getElementById("plugins-section").style.display = "";
  getOrCreateChart("plugins-chart", {
    type: "bar",
    data: {
      labels: plugins.map((p) => p.plugin_name),
      datasets: [
        {
          label: "Install Count",
          data: plugins.map((p) => p.install_count),
          backgroundColor: "#f0883e",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b949e" }, grid: { color: "#21262d" } },
        y: { ticks: { color: "#c9d1d9" }, grid: { display: false } },
      },
    },
  });
}

// Sidebar chart filters
const CHART_SECTION_MAP = {
  tools: "tools-section",
  "tools-adoption": "tools-adoption-section",
  errors: "errors-section",
  models: "models-section",
  skills: "skills-section",
  mcp: "mcp-section",
  plugins: "plugins-section",
};

function initFilters() {
  document.querySelectorAll("#sidebar input[data-chart]").forEach((cb) => {
    cb.addEventListener("change", () => {
      const sectionId = CHART_SECTION_MAP[cb.dataset.chart];
      const section = document.getElementById(sectionId);
      if (!section) return;
      // Only hide/show if the section has data (wasn't hidden by render logic)
      if (cb.checked) {
        section.style.display = section.dataset.hasData === "false" ? "none" : "";
      } else {
        section.style.display = "none";
      }
    });
  });
}

function initClientFilter() {
  document.querySelectorAll("#sidebar input[data-client]").forEach((cb) => {
    cb.addEventListener("change", () => {
      selectedClients = Array.from(
        document.querySelectorAll("#sidebar input[data-client]:checked")
      ).map((el) => el.dataset.client);
      loadDashboard();
    });
  });
}

initFilters();
initClientFilter();
loadDashboard();
