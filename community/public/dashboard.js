// tool-time Community Dashboard
// All data rendered via textContent or Chart.js (no innerHTML)

const API_BASE = "/v1/api/stats";

async function loadDashboard() {
  try {
    const resp = await fetch(API_BASE);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderOverview(data.overview);
    renderToolsChart(data.tools);
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
  new Chart(document.getElementById("tools-chart"), {
    type: "bar",
    data: {
      labels: top.map((t) => t.tool_name),
      datasets: [
        {
          label: "Total Calls",
          data: top.map((t) => t.total_calls),
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

  new Chart(document.getElementById("errors-chart"), {
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
  new Chart(document.getElementById("models-chart"), {
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
  if (!skills || !skills.length) return;
  document.getElementById("skills-section").style.display = "";
  new Chart(document.getElementById("skills-chart"), {
    type: "bar",
    data: {
      labels: skills.map((s) => s.name),
      datasets: [
        {
          label: "Total Calls",
          data: skills.map((s) => s.total_calls),
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
  if (!mcpServers || !mcpServers.length) return;
  document.getElementById("mcp-section").style.display = "";
  new Chart(document.getElementById("mcp-chart"), {
    type: "bar",
    data: {
      labels: mcpServers.map((m) => m.name),
      datasets: [
        {
          label: "Total Calls",
          data: mcpServers.map((m) => m.total_calls),
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
  if (!plugins || !plugins.length) return;
  document.getElementById("plugins-section").style.display = "";
  new Chart(document.getElementById("plugins-chart"), {
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

loadDashboard();
