// Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later
// Brain: folders are bubbles, notes are dots, See-also links are red dashes.

import { $, api, esc } from "./util.js";

const AREA_COLORS = ["#ff4d2e", "#f5a524", "#7aa2ff", "#7fd49a", "#c792ea", "#4fd1c5", "#ff8fb1", "#d4c95a", "#ff9a5c", "#9fb4ff"];
let brain = null; // {sim, timer, nodes}

async function loadD3() {
  if (window.d3) return;
  await new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "/web/vendor/d3.min.js"; s.onload = ok; s.onerror = fail;
    document.head.append(s);
  });
}

export function leaveGraph() {
  if (!brain) return;
  brain.sim.stop(); clearInterval(brain.timer); brain = null;
}

// `onLive` runs after each live refresh (the sidebar reloads its tree and token counts).
export async function showGraph(onLive) {
  const view = $("#view");
  view.className = "brain";
  view.innerHTML = `<div class="brain-bar"><h2>Brain</h2><span class="crumbs" id="gstats"></span>
    <label><input type="checkbox" id="live"> live</label></div><div id="tip"></div><div class="legend" id="legend"></div>`;
  try { await loadD3(); } catch { view.innerHTML = '<article><p class="lead">Could not load d3 for the graph.</p></article>'; return; }
  const svg = d3.select(view).insert("svg", ":first-child");
  const zoomLayer = svg.append("g");
  const linkLayer = zoomLayer.append("g"), nodeLayer = zoomLayer.append("g");
  const zoom = d3.zoom().scaleExtent([0.15, 4]).on("zoom", (e) => zoomLayer.attr("transform", e.transform));
  svg.call(zoom);
  const { width, height } = view.getBoundingClientRect();
  const sim = d3.forceSimulation()
    .force("link", d3.forceLink().id((d) => d.id)
      .distance((l) => l.kind === "see_also" ? 90 : l.target.kind === "folder" ? 150 : 44)
      .strength((l) => l.kind === "see_also" ? 0.12 : 0.7))
    .force("charge", d3.forceManyBody().strength((d) => d.kind === "folder" ? -700 : -60))
    .force("collide", d3.forceCollide((d) => radius(d) + 3))
    .force("x", d3.forceX(width / 2).strength(0.03)).force("y", d3.forceY(height / 2).strength(0.03));
  brain = { sim, timer: null, nodes: new Map(), focus: null };
  const color = (d) => !d.area ? "#f3ead8" : AREA_COLORS[areaIndex(d.area) % AREA_COLORS.length]; // root and its notes: cream
  let areas = [];
  const areaIndex = (a) => Math.max(0, areas.indexOf(a));

  async function refresh() {
    const g = await api("/graph");
    areas = [...new Set(g.nodes.filter((n) => n.kind === "folder" && n.depth === 1).map((n) => n.area))].sort();
    if (brain.nodes.size === g.nodes.length && brain.links === g.links.length) return; // nothing new
    const nodes = g.nodes.map((n) => Object.assign(brain.nodes.get(n.id) || { x: width / 2 + (Math.random() - .5) * 60, y: height / 2 + (Math.random() - .5) * 60 }, n));
    brain.nodes = new Map(nodes.map((n) => [n.id, n])); brain.links = g.links.length;
    const links = g.links.map((l) => ({ ...l }));
    linkLayer.selectAll("line").data(links).join("line").attr("class", (l) => "link " + l.kind);
    const node = nodeLayer.selectAll("g.node").data(nodes, (d) => d.id).join((enter) => {
      const e = enter.append("g").attr("class", (d) => "node " + d.kind);
      e.append("circle");
      e.filter((d) => d.kind === "folder").append("text").attr("text-anchor", "middle");
      return e;
    });
    node.select("circle").attr("r", radius).attr("fill", color).attr("stroke", (d) => d.kind === "folder" ? color(d) : null)
      .style("--glow", (d) => d.kind === "folder" ? color(d) : null);
    node.select("text").text((d) => d.label).attr("dy", (d) => -radius(d) - 6);
    node.on("mouseenter", (e, d) => tip(e, d)).on("mousemove", (e, d) => tip(e, d)).on("mouseleave", () => ($("#tip").style.display = "none"))
      .on("click", (e, d) => d.kind === "note" ? (location.hash = "#/note/" + d.id) : focus(d, links))
      .call(d3.drag().on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = d.fy = null; }));
    const draw = () => {
      linkLayer.selectAll("line").attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y);
      node.attr("transform", (d) => `translate(${d.x},${d.y})`);
    };
    sim.nodes(nodes).on("tick", draw);
    sim.force("link").links(links);
    if (brain.started) {
      sim.alpha(0.35).restart(); // new notes arrive: let them settle in, animated
    } else { // first view: lay it out at once, already settled and fitted
      sim.stop();
      for (let i = 0; i < 300; i++) sim.tick();
      draw(); fit(); brain.started = true;
    }
    const notes = g.nodes.filter((n) => n.kind === "note").length;
    $("#gstats").textContent = `${notes} notes · ${g.nodes.length - notes - 1} folders · ${g.links.filter((l) => l.kind === "see_also").length} links`;
    $("#legend").innerHTML = areas.map((a, i) => `<span style="--c:${AREA_COLORS[i % AREA_COLORS.length]}">${esc(a)}</span>`).join("");
  }

  function fit() { // zoom so the whole brain fills the view
    const xs = [...brain.nodes.values()].map((n) => n.x), ys = [...brain.nodes.values()].map((n) => n.y);
    const [x0, x1, y0, y1] = [Math.min(...xs) - 60, Math.max(...xs) + 60, Math.min(...ys) - 70, Math.max(...ys) + 50];
    const k = Math.min(2.2, 0.9 * Math.min(width / (x1 - x0), (height - 90) / (y1 - y0)));
    const t = d3.zoomIdentity.translate(width / 2 - k * (x0 + x1) / 2, (height + 40) / 2 - k * (y0 + y1) / 2).scale(k);
    svg.call(zoom.transform, t);
  }

  function tip(e, d) {
    const t = $("#tip"), box = view.getBoundingClientRect();
    t.innerHTML = d.kind === "note" ? `<b>${esc(d.label)}</b>${esc(d.summary)}<br><small>${esc(d.id)}</small>`
      : `<b>${esc(d.id)}</b>${esc(d.description || "")}<br><small>${d.notes} notes · click to focus</small>`;
    t.style.display = "block"; t.style.left = (e.clientX - box.left + 14) + "px"; t.style.top = (e.clientY - box.top + 14) + "px";
  }

  function focus(d, links) { // click a folder: keep its branch lit; click again to clear
    brain.focus = brain.focus === d.id ? null : d.id;
    const inside = (id) => !brain.focus || brain.focus === "/" || id === brain.focus || id.startsWith(brain.focus.slice(1) + "/") || id.startsWith(brain.focus + "/");
    nodeLayer.selectAll("g.node").classed("dim", (n) => !inside(n.id));
    linkLayer.selectAll("line").classed("dim", (l) => !inside(l.source.id) || !inside(l.target.id));
  }

  await refresh();
  $("#live").onchange = (e) => {
    clearInterval(brain.timer);
    if (e.target.checked) brain.timer = setInterval(() => refresh().then(onLive).catch(() => {}), 4000);
  };
}
const radius = (d) => d.kind === "note" ? 5.5 : d.depth === 0 ? 34 : 16 + 5 * Math.sqrt(d.notes);
