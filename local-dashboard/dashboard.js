/* tool-time local dashboard — D3.js rendering */
"use strict";

const COLORS = {
  blue: "#58a6ff",
  green: "#3fb950",
  red: "#f85149",
  purple: "#d2a8ff",
  orange: "#f0883e",
  yellow: "#e3b341",
  cyan: "#39d2c0",
  text: "#e6edf3",
  muted: "#8b949e",
  surface: "#161b22",
  border: "#30363d",
  bg: "#0d1117",
};

const CLASS_COLORS = {
  building: COLORS.blue,
  debugging: COLORS.red,
  exploring: COLORS.green,
  reviewing: COLORS.purple,
  planning: COLORS.orange,
  other: COLORS.muted,
};

const TOOL_COLORS = [COLORS.blue, COLORS.green, COLORS.purple, COLORS.orange, COLORS.cyan, COLORS.yellow, COLORS.red];

// --- Data loading ---

async function loadData() {
  try {
    const resp = await fetch("analysis.json");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return await resp.json();
  } catch (e) {
    const main = document.querySelector("main");
    const div = document.createElement("div");
    div.className = "empty-state";
    const p1 = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = "No analysis data found.";
    p1.appendChild(strong);
    div.appendChild(p1);
    const p2 = document.createElement("p");
    const code1 = document.createElement("code");
    code1.textContent = "python3 analyze.py";
    p2.textContent = "Run ";
    p2.appendChild(code1);
    p2.append(" first, then copy analysis.json here.");
    div.appendChild(p2);
    const p3 = document.createElement("p");
    p3.style.marginTop = "8px";
    p3.style.fontSize = "12px";
    p3.textContent = "Or use: ";
    const code2 = document.createElement("code");
    code2.textContent = "bash serve.sh";
    p3.appendChild(code2);
    div.appendChild(p3);
    main.textContent = "";
    main.appendChild(div);
    throw e;
  }
}

// --- Utility ---

function fmt(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}

function pct(n, d) {
  if (d === 0) return "0%";
  return (n / d * 100).toFixed(1) + "%";
}

function emptyState(container, msg) {
  const el = d3.select(container);
  el.selectAll("*").remove();
  const div = el.append("div").attr("class", "empty-state");
  div.text(msg);
}

// --- Retry Patterns ---

function renderRetries(data) {
  const el = "#retries-content";
  const retries = data.tool_chains?.retry_patterns || [];

  if (retries.length === 0) {
    emptyState(el, "No retry patterns detected \u2014 tool calls succeed on the first try.");
    return;
  }

  const table = d3.select(el).append("table");
  const thead = table.append("thead").append("tr");
  ["Tool", "Avg Retries", "Max Retries", "Sessions Affected"].forEach(function(h) {
    thead.append("th").text(h).classed("num", h !== "Tool");
  });

  const tbody = table.append("tbody");
  retries.forEach(function(r) {
    const tr = tbody.append("tr");
    tr.append("td").text(r.tool);
    tr.append("td").classed("num", true).text(r.avg_retries.toFixed(1));
    tr.append("td").classed("num", true).text(r.max_retries);
    tr.append("td").classed("num", true).text(r.sessions_with_retries);
  });
}

// --- Sankey (Tool Chains) ---

function renderSankey(data) {
  const el = "#sankey-chart";
  const bigrams = data.tool_chains?.bigrams || [];

  if (bigrams.length < 3) {
    emptyState(el, "Need more data for tool chain visualization (minimum 50 tool calls across 3+ transitions).");
    return;
  }

  // Top 20 bigrams for readability
  const top = bigrams.slice(0, 20);

  // Build nodes
  const nodeSet = new Set();
  top.forEach(function(b) { nodeSet.add(b.from); nodeSet.add(b.to); });
  const nodes = Array.from(nodeSet).map(function(n) { return { name: n }; });
  const nodeIndex = {};
  nodes.forEach(function(n, i) { nodeIndex[n.name] = i; });

  // Build links — handle self-loops by splitting into from->mid->to
  const links = [];
  top.forEach(function(b) {
    if (b.from === b.to) {
      var midName = b.from + " \u2192";
      if (!(midName in nodeIndex)) {
        nodeIndex[midName] = nodes.length;
        nodes.push({ name: midName });
      }
      links.push({ source: nodeIndex[b.from], target: nodeIndex[midName], value: b.count });
      links.push({ source: nodeIndex[midName], target: nodeIndex[b.from], value: b.count });
    } else {
      links.push({ source: nodeIndex[b.from], target: nodeIndex[b.to], value: b.count });
    }
  });

  var width = 900;
  var height = Math.max(400, nodes.length * 28);

  var container = d3.select(el).append("div").classed("chart-container", true);
  var svg = container.append("svg")
    .attr("viewBox", "0 0 " + width + " " + height)
    .attr("width", "100%");

  var sankey = d3.sankey()
    .nodeWidth(18)
    .nodePadding(12)
    .extent([[1, 5], [width - 1, height - 5]]);

  var graph = sankey({
    nodes: nodes.map(function(d) { return Object.assign({}, d); }),
    links: links.map(function(d) { return Object.assign({}, d); })
  });

  // Links
  svg.append("g")
    .selectAll("path")
    .data(graph.links)
    .join("path")
    .attr("class", "sankey-link")
    .attr("d", d3.sankeyLinkHorizontal())
    .attr("stroke", COLORS.blue)
    .attr("stroke-width", function(d) { return Math.max(1, d.width); })
    .append("title")
    .text(function(d) { return d.source.name + " \u2192 " + d.target.name + ": " + fmt(d.value); });

  // Nodes
  var node = svg.append("g")
    .selectAll("g")
    .data(graph.nodes)
    .join("g")
    .attr("class", "sankey-node");

  node.append("rect")
    .attr("x", function(d) { return d.x0; })
    .attr("y", function(d) { return d.y0; })
    .attr("height", function(d) { return d.y1 - d.y0; })
    .attr("width", function(d) { return d.x1 - d.x0; })
    .attr("fill", function(_, i) { return TOOL_COLORS[i % TOOL_COLORS.length]; });

  node.append("text")
    .attr("x", function(d) { return d.x0 < width / 2 ? d.x1 + 6 : d.x0 - 6; })
    .attr("y", function(d) { return (d.y1 + d.y0) / 2; })
    .attr("dy", "0.35em")
    .attr("text-anchor", function(d) { return d.x0 < width / 2 ? "start" : "end"; })
    .text(function(d) { return d.name.endsWith(" \u2192") ? "" : d.name; });
}

// --- Overview (KPIs + Donut) ---

function renderOverview(data) {
  var sessions = data.sessions || {};
  var grid = d3.select("#kpi-cards").append("div").classed("kpi-grid", true);

  var kpis = [
    { value: fmt(data.event_count || 0), label: "Total Events" },
    { value: sessions.total || 0, label: "Sessions" },
    { value: (sessions.avg_duration_minutes || 0).toFixed(0) + "m", label: "Avg Duration" },
    { value: sessions.avg_tools_per_session || 0, label: "Avg Tools/Session" },
    { value: data.period?.start || "\u2014", label: "From" },
    { value: data.period?.end || "\u2014", label: "To" },
  ];

  kpis.forEach(function(k) {
    var card = grid.append("div").classed("kpi-card", true);
    card.append("div").classed("value", true).text(k.value);
    card.append("div").classed("label", true).text(k.label);
  });

  // Donut chart for classifications
  renderDonut(sessions.classifications || {});
}

function renderDonut(classifications) {
  var el = "#classification-chart";
  var entries = Object.entries(classifications);
  if (entries.length === 0) {
    emptyState(el, "No session classification data available.");
    return;
  }

  var width = 320, height = 320, radius = Math.min(width, height) / 2 - 20;
  var container = d3.select(el).append("div").classed("chart-container", true);
  var svg = container.append("svg")
    .attr("viewBox", "0 0 " + width + " " + height)
    .attr("width", width)
    .append("g")
    .attr("transform", "translate(" + (width/2) + "," + (height/2) + ")");

  var pie = d3.pie().value(function(d) { return d[1]; }).sort(null);
  var arc = d3.arc().innerRadius(radius * 0.55).outerRadius(radius);

  svg.selectAll("path")
    .data(pie(entries))
    .join("path")
    .attr("d", arc)
    .attr("fill", function(d) { return CLASS_COLORS[d.data[0]] || COLORS.muted; })
    .append("title")
    .text(function(d) { return d.data[0] + ": " + d.data[1]; });

  // Legend
  var legend = container.append("div")
    .style("display", "flex").style("flex-wrap", "wrap").style("gap", "12px")
    .style("margin-top", "12px").style("justify-content", "center");

  entries.forEach(function(entry) {
    var name = entry[0], count = entry[1];
    var item = legend.append("span").style("font-size", "12px").style("display", "flex").style("align-items", "center").style("gap", "4px");
    item.append("span").style("width", "10px").style("height", "10px").style("border-radius", "2px")
      .style("background", CLASS_COLORS[name] || COLORS.muted).style("display", "inline-block");
    item.append("span").style("color", COLORS.muted).text(name + " (" + count + ")");
  });
}

// --- Projects Table ---

function renderProjects(data) {
  var el = "#projects-table";
  var projects = data.projects || {};
  var entries = Object.entries(projects);

  if (entries.length === 0) {
    emptyState(el, "No project data available.");
    return;
  }

  entries.sort(function(a, b) { return b[1].events - a[1].events; });

  var table = d3.select(el).append("table");
  var thead = table.append("thead").append("tr");
  ["Project", "Events", "Sessions", "Top Tool", "Type", "Error Rate"].forEach(function(h) {
    thead.append("th").text(h).classed("num", ["Events", "Sessions", "Error Rate"].indexOf(h) >= 0);
  });

  var tbody = table.append("tbody");
  entries.forEach(function(entry) {
    var name = entry[0], p = entry[1];
    var tr = tbody.append("tr").classed("expand-row", true);
    tr.append("td").text(name);
    tr.append("td").classed("num", true).text(fmt(p.events));
    tr.append("td").classed("num", true).text(p.sessions);
    tr.append("td").text(p.top_tools?.[0] || "\u2014");
    tr.append("td").text(p.primary_classification || "\u2014");
    var errRate = (p.error_rate * 100).toFixed(1) + "%";
    var errTd = tr.append("td").classed("num", true).text(errRate);
    if (p.error_rate > 0.05) errTd.classed("error-high", true);
    else errTd.classed("error-low", true);

    var detail = tbody.append("tr").classed("expand-detail", true);
    var detailTd = detail.append("td").attr("colspan", 6);
    detailTd.text("Tools: " + (p.top_tools || []).join(", "));

    tr.on("click", function() {
      detail.classed("visible", !detail.classed("visible"));
    });
  });
}

// --- Trends ---

function renderTrends(data) {
  var el = "#trends-chart";
  var trends = data.trends || [];

  if (trends.length < 2) {
    var days = data.period ? data.period.start + " to " + data.period.end : "unknown";
    emptyState(el, "Need at least 2 weeks of data to show trends. Current data spans " + days + ".");
    return;
  }

  var container = d3.select(el).append("div").classed("chart-container", true);
  var margin = { top: 20, right: 60, bottom: 40, left: 60 };
  var width = 860 - margin.left - margin.right;
  var height = 360 - margin.top - margin.bottom;

  var svg = container.append("svg")
    .attr("viewBox", "0 0 860 360")
    .attr("width", "100%")
    .append("g")
    .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  var x = d3.scaleBand()
    .domain(trends.map(function(t) { return t.week; }))
    .range([0, width])
    .padding(0.1);

  // Find top 5 tools across all weeks
  var toolTotals = {};
  trends.forEach(function(t) {
    Object.keys(t.tools || {}).forEach(function(k) {
      toolTotals[k] = (toolTotals[k] || 0) + t.tools[k];
    });
  });
  var topTools = Object.entries(toolTotals)
    .sort(function(a, b) { return b[1] - a[1]; })
    .slice(0, 5)
    .map(function(e) { return e[0]; });

  var maxEvents = d3.max(trends, function(t) { return t.events; }) || 1;
  var y = d3.scaleLinear().domain([0, maxEvents]).range([height, 0]);

  // Stacked bars
  var stackData = trends.map(function(t) {
    var d = { week: t.week };
    topTools.forEach(function(tool) { d[tool] = t.tools?.[tool] || 0; });
    return d;
  });

  var stack = d3.stack().keys(topTools);
  var series = stack(stackData);

  svg.selectAll("g.stack")
    .data(series)
    .join("g")
    .attr("class", "stack")
    .attr("fill", function(_, i) { return TOOL_COLORS[i % TOOL_COLORS.length]; })
    .attr("fill-opacity", 0.7)
    .selectAll("rect")
    .data(function(d) { return d; })
    .join("rect")
    .attr("x", function(d) { return x(d.data.week); })
    .attr("y", function(d) { return y(d[1]); })
    .attr("height", function(d) { return y(d[0]) - y(d[1]); })
    .attr("width", x.bandwidth());

  // Error rate line (right axis)
  var maxErr = d3.max(trends, function(t) { return t.error_rate; });
  var yErr = d3.scaleLinear()
    .domain([0, (maxErr || 0.1) * 1.2])
    .range([height, 0]);

  var line = d3.line()
    .x(function(t) { return x(t.week) + x.bandwidth() / 2; })
    .y(function(t) { return yErr(t.error_rate); });

  svg.append("path")
    .datum(trends)
    .attr("fill", "none")
    .attr("stroke", COLORS.red)
    .attr("stroke-width", 2)
    .attr("d", line);

  svg.selectAll("circle.err")
    .data(trends)
    .join("circle")
    .attr("class", "err")
    .attr("cx", function(t) { return x(t.week) + x.bandwidth() / 2; })
    .attr("cy", function(t) { return yErr(t.error_rate); })
    .attr("r", 3)
    .attr("fill", COLORS.red)
    .append("title")
    .text(function(t) { return t.week + ": " + (t.error_rate * 100).toFixed(1) + "% errors"; });

  // Axes
  var tickInterval = Math.max(1, Math.floor(trends.length / 8));
  svg.append("g").attr("class", "axis").attr("transform", "translate(0," + height + ")")
    .call(d3.axisBottom(x).tickValues(x.domain().filter(function(_, i) { return i % tickInterval === 0; })));
  svg.append("g").attr("class", "axis").call(d3.axisLeft(y).ticks(5).tickFormat(fmt));
  svg.append("g").attr("class", "axis").attr("transform", "translate(" + width + ",0)")
    .call(d3.axisRight(yErr).ticks(4).tickFormat(function(d) { return (d * 100).toFixed(0) + "%"; }));

  // Legend
  var legend = container.append("div")
    .style("display", "flex").style("flex-wrap", "wrap").style("gap", "12px")
    .style("margin-top", "8px").style("justify-content", "center");

  topTools.forEach(function(tool, i) {
    var item = legend.append("span").style("font-size", "12px").style("display", "flex").style("align-items", "center").style("gap", "4px");
    item.append("span").style("width", "10px").style("height", "10px").style("border-radius", "2px")
      .style("background", TOOL_COLORS[i % TOOL_COLORS.length]).style("display", "inline-block");
    item.append("span").style("color", COLORS.muted).text(tool);
  });
  var errItem = legend.append("span").style("font-size", "12px").style("display", "flex").style("align-items", "center").style("gap", "4px");
  errItem.append("span").style("width", "10px").style("height", "2px").style("background", COLORS.red).style("display", "inline-block");
  errItem.append("span").style("color", COLORS.muted).text("Error Rate");
}

// --- Source Comparison ---

function renderSources(data) {
  var el = "#sources-content";
  var sources = data.by_source || {};
  var entries = Object.entries(sources);

  if (entries.length <= 1) {
    var name = entries[0]?.[0] || "unknown";
    emptyState(el, "All events from " + name + ". Use multiple AI clients to compare.");
    return;
  }

  var grid = d3.select(el).append("div").classed("source-grid", true);

  entries.forEach(function(entry) {
    var name = entry[0], s = entry[1];
    var card = grid.append("div").classed("source-card", true);
    card.append("h3").text(name);

    var stats = [
      ["Events", fmt(s.events)],
      ["Sessions", s.sessions],
      ["Avg Tools/Session", s.avg_tools_per_session],
      ["Error Rate", (s.error_rate * 100).toFixed(1) + "%"],
      ["Top Tools", (s.top_tools || []).slice(0, 3).join(", ")],
    ];

    stats.forEach(function(stat) {
      var row = card.append("div").classed("stat-row", true);
      row.append("span").classed("stat-label", true).text(stat[0]);
      row.append("span").text(stat[1]);
    });

    // Classification mini-bar
    var mix = s.classification_mix || {};
    var mixEntries = Object.entries(mix);
    if (mixEntries.length > 0) {
      var total = mixEntries.reduce(function(sum, e) { return sum + e[1]; }, 0);
      var bar = card.append("div")
        .style("display", "flex").style("height", "6px").style("border-radius", "3px")
        .style("overflow", "hidden").style("margin-top", "8px");
      mixEntries.forEach(function(me) {
        bar.append("div")
          .style("width", pct(me[1], total))
          .style("background", CLASS_COLORS[me[0]] || COLORS.muted)
          .append("title").text(me[0] + ": " + me[1]);
      });
    }
  });
}

// --- Time Patterns ---

function renderHeatmap(data) {
  var el = "#heatmap-chart";
  var tp = data.time_patterns || {};
  var byHour = tp.by_hour || [];
  var byDay = tp.by_day_of_week || [];

  var totalEvents = byHour.reduce(function(s, h) { return s + h.events; }, 0);
  if (totalEvents < 100) {
    emptyState(el, "Need 100+ events for meaningful time patterns. Currently: " + totalEvents + " events.");
    return;
  }

  var container = d3.select(el).append("div").classed("chart-container", true);
  var days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  var margin = { top: 10, right: 20, bottom: 30, left: 40 };
  var width = 860 - margin.left - margin.right;
  var barHeight = 180;

  // Hour bar chart
  container.append("h3").text("Events by Hour")
    .style("font-size", "14px").style("color", COLORS.muted).style("margin-bottom", "8px");

  var svgH = container.append("svg")
    .attr("viewBox", "0 0 860 " + (barHeight + margin.top + margin.bottom))
    .attr("width", "100%")
    .append("g")
    .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  var xH = d3.scaleBand().domain(d3.range(24)).range([0, width]).padding(0.1);
  var yH = d3.scaleLinear().domain([0, d3.max(byHour, function(d) { return d.events; }) || 1]).range([barHeight, 0]);

  svgH.selectAll("rect")
    .data(byHour)
    .join("rect")
    .attr("x", function(d) { return xH(d.hour); })
    .attr("y", function(d) { return yH(d.events); })
    .attr("width", xH.bandwidth())
    .attr("height", function(d) { return barHeight - yH(d.events); })
    .attr("fill", function(d) {
      if (d.hour === tp.peak_hour) return COLORS.blue;
      if (d.hour === tp.most_error_prone_hour) return COLORS.red;
      return COLORS.muted;
    })
    .attr("fill-opacity", 0.7)
    .append("title")
    .text(function(d) { return d.hour + ":00 \u2014 " + fmt(d.events) + " events, " + (d.error_rate * 100).toFixed(1) + "% errors"; });

  svgH.append("g").attr("class", "axis").attr("transform", "translate(0," + barHeight + ")")
    .call(d3.axisBottom(xH).tickFormat(function(h) { return String(h); }));
  svgH.append("g").attr("class", "axis").call(d3.axisLeft(yH).ticks(4).tickFormat(fmt));

  // Annotations
  var tz = tp.timezone || "UTC";
  var anno = container.append("div").style("font-size", "12px").style("color", COLORS.muted).style("margin", "8px 0 16px");
  anno.text("Peak: " + tp.peak_hour + ":00 \u00b7 Most errors: " + tp.most_error_prone_hour + ":00 \u00b7 Timezone: " + tz);

  // Day bar chart
  container.append("h3").text("Events by Day")
    .style("font-size", "14px").style("color", COLORS.muted).style("margin-bottom", "8px");

  var svgD = container.append("svg")
    .attr("viewBox", "0 0 860 " + (barHeight + margin.top + margin.bottom))
    .attr("width", "100%")
    .append("g")
    .attr("transform", "translate(" + margin.left + "," + margin.top + ")");

  var xD = d3.scaleBand().domain(days).range([0, width]).padding(0.1);
  var yD = d3.scaleLinear().domain([0, d3.max(byDay, function(d) { return d.events; }) || 1]).range([barHeight, 0]);

  svgD.selectAll("rect")
    .data(byDay)
    .join("rect")
    .attr("x", function(d) { return xD(d.day); })
    .attr("y", function(d) { return yD(d.events); })
    .attr("width", xD.bandwidth())
    .attr("height", function(d) { return barHeight - yD(d.events); })
    .attr("fill", function(d) { return d.day === tp.peak_day ? COLORS.blue : COLORS.muted; })
    .attr("fill-opacity", 0.7)
    .append("title")
    .text(function(d) { return d.day + " \u2014 " + fmt(d.events) + " events, " + d.sessions + " sessions"; });

  svgD.append("g").attr("class", "axis").attr("transform", "translate(0," + barHeight + ")")
    .call(d3.axisBottom(xD));
  svgD.append("g").attr("class", "axis").call(d3.axisLeft(yD).ticks(4).tickFormat(fmt));
}

// --- Main ---

async function main() {
  var data = await loadData();

  renderRetries(data);
  renderSankey(data);
  renderOverview(data);
  renderProjects(data);
  renderTrends(data);
  renderSources(data);
  renderHeatmap(data);

  // Update subtitle with period
  if (data.period?.start) {
    document.querySelector(".subtitle").textContent =
      data.period.start + " to " + data.period.end + " \u00b7 " + fmt(data.event_count) + " events \u00b7 " + (data.sessions?.total || 0) + " sessions";
  }
}

main().catch(console.error);
