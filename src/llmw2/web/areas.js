// Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later
// Areas: the top-level folders. Make one, confirm or dismiss the ones people propose, rename one, merge one into another.
// The wiki never opens or proposes an area itself; proposals are added through the API, the CLI or an MCP client.

import { $, api, esc, post } from "./util.js";

// `onChanged` runs after a change, to reload the tree.
export async function showAreas(onChanged, said = {}) {
  const [tree, proposals] = await Promise.all([api("/tree"), api("/proposals")]);
  const roots = tree.subfolders.map((f) => f.path);
  const options = roots.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
  const list = proposals.map((p) => `<div class="proposal">
      <b>/${esc(p.label)}</b> <span class="status">${p.notes.length} note${p.notes.length === 1 ? "" : "s"}</span>
      <div class="hint">${esc(p.description)}</div>
      <div class="actions"><button class="btn small" type="button" data-confirm="${esc(p.id)}">Confirm</button>
        <button class="btn ghost small" type="button" data-dismiss="${esc(p.id)}">Dismiss</button>
        <span class="status" id="p-${esc(p.id)}"></span></div></div>`).join("");
  const view = $("#view");
  view.className = "";
  view.innerHTML = `<article class="settings">
    <div class="crumbs">AREAS · TOP-LEVEL FOLDERS</div>
    <h2>Your own <em>areas.</em></h2>
    <p class="lead">The wiki never opens or proposes an area by itself: you do. Notes the limit of top-level folders sent to another area are listed as deviations in the log, and help to compose a proposal.</p>
    <div class="field"><label for="area-name">New area</label>
      <input id="area-name" maxlength="40" autocomplete="off" spellcheck="false" placeholder="Name, e.g. suppliers">
      <input id="area-desc" maxlength="150" autocomplete="off" placeholder="One line: what belongs here" style="margin-top:8px">
      <div class="actions"><button class="btn" type="button" id="area-make">Create area</button><span class="status" id="area-status"></span></div></div>
    <div class="field"><label>Open proposals</label><span class="status" id="proposals-status"></span>
      ${list || '<div class="hint">None. Add one with <code>llmwiki2 proposals add</code>, <code>POST /proposals</code> or the <code>propose_area</code> memory tool.</div>'}</div>
    <div class="field"><label for="rename-from">Rename an area</label>
      <select id="rename-from">${options}</select>
      <input id="rename-to" maxlength="40" autocomplete="off" spellcheck="false" placeholder="New name" style="margin-top:8px">
      <div class="actions"><button class="btn ghost" type="button" id="rename-go" ${roots.length ? "" : "disabled"}>Rename</button><span class="status" id="rename-status"></span></div></div>
    <div class="field"><label for="merge-from">Merge an area into another</label>
      <select id="merge-from">${options}</select>
      <select id="merge-to" style="margin-top:8px">${options}</select>
      <div class="hint">Every note of the first goes into the second, with its subfolders; the first is deleted, links follow.</div>
      <div class="actions"><button class="btn ghost" type="button" id="merge-go" ${roots.length > 1 ? "" : "disabled"}>Merge</button><span class="status" id="merge-status"></span></div></div>
  </article>`;

  const status = (id, text, kind = "") => { const el = $("#" + id); if (el) { el.className = "status " + kind; el.textContent = text; } };
  for (const [id, text] of Object.entries(said)) status(id, `✓ ${text}`, "ok");
  if (roots.length > 1) $("#merge-to").selectedIndex = 1;
  // do it, reload the tree and the page, or say what went wrong; a proposal done leaves the list, so it is said above it
  const run = async (id, request, done, saidAt = id) => {
    try {
      const result = await request();
      await onChanged();
      await showAreas(onChanged, { [saidAt]: done(result) });
    } catch (e) { status(id, `✗ ${e.message}`, "err"); }
  };
  // over the limit of top-level folders (max_top_folders) the server says so, and the person decides
  const overLimit = async (request, message) => {
    try { return await request(false); } catch (e) {
      if (!/max_top_folders/.test(e.message) || !window.confirm(`${e.message}\n\n${message}`)) throw e;
      return request(true);
    }
  };
  $("#area-make").onclick = () => run("area-status", () => overLimit((over) => post("/areas", { name: $("#area-name").value, description: $("#area-desc").value, over_limit: over }), "Create it anyway?"), (f) => `Created ${f.path}`);
  $("#rename-go").onclick = () => run("rename-status", () => post("/folder/rename", { path: $("#rename-from").value, name: $("#rename-to").value }), (f) => `Renamed to ${f.path}`);
  $("#merge-go").onclick = () => run("merge-status", () => post("/folder/merge", { source: $("#merge-from").value, target: $("#merge-to").value }), (f) => `Merged into ${f.path}`);
  view.querySelectorAll("[data-confirm]").forEach((b) => (b.onclick = () => {
    const id = b.dataset.confirm;
    const confirm = (over) => post(`/proposals/${id}/confirm`, { over_limit: over });
    run("p-" + id, () => overLimit(confirm, "Open it anyway?"), (p) => `Opened /${p.label} with ${p.moved.length} note${p.moved.length === 1 ? "" : "s"}`, "proposals-status");
  }));
  view.querySelectorAll("[data-dismiss]").forEach((b) => (b.onclick = () => run("p-" + b.dataset.dismiss, () => post(`/proposals/${b.dataset.dismiss}/dismiss`, {}), (p) => `Dismissed /${p.label}`, "proposals-status")));
}
