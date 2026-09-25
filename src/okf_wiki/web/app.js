// Copyright 2026 Federico Cesarini, Marco Sassarini. Licensed under the Apache License, Version 2.0.
// The page: sidebar tree, notes, questions, filing by drop/pick/paste, and routing.

import { $, api, esc, fmt, md, post } from "./util.js";
import { leaveGraph, showGraph } from "./graph.js";
import { loadSettings, showSettings } from "./settings.js";

const open = new Set(JSON.parse(localStorage.getItem("okf-open") || "[]"));
let tree = null, current = "", noModel = false; // noModel: no key yet, so the wiki can be read but not asked


// -- sidebar -----------------------------------------------------------------
async function loadTree() {
  tree = await api("/tree");
  $("#tree").replaceChildren(renderFolder(tree, true));
  highlight();
}
function renderFolder(f, isRoot) {
  const box = document.createElement("div");
  const kids = document.createElement("div");
  kids.className = "kids";
  for (const sub of f.subfolders) kids.append(renderFolder(sub, false));
  for (const n of f.notes) {
    const b = document.createElement("button");
    b.className = "note-link"; b.textContent = n.title; b.title = n.summary; b.dataset.path = n.path;
    b.onclick = () => (location.hash = "#/note/" + n.path);
    kids.append(b);
  }
  if (isRoot) {
    if (!f.subfolders.length && !f.notes.length) kids.innerHTML = '<div class="empty">Empty — drop a file to start.</div>';
    kids.style.display = "block"; kids.style.border = "0"; kids.style.marginLeft = "0";
    return kids;
  }
  box.className = "folder" + (open.has(f.path) ? " open" : "");
  const row = document.createElement("button");
  row.className = "row";
  row.innerHTML = `<span class="tri">▶</span><span></span><span class="count">${countNotes(f)}</span>`;
  row.children[1].textContent = f.path.split("/").pop();
  row.onclick = () => {
    box.classList.toggle("open");
    box.classList.contains("open") ? open.add(f.path) : open.delete(f.path);
    localStorage.setItem("okf-open", JSON.stringify([...open]));
  };
  box.append(row);
  if (f.description) { const d = document.createElement("div"); d.className = "desc"; d.textContent = f.description; box.append(d); }
  box.append(kids);
  return box;
}
const countNotes = (f) => f.notes.length + f.subfolders.reduce((s, x) => s + countNotes(x), 0);
const countFolders = (f) => f.subfolders.length + f.subfolders.reduce((s, x) => s + countFolders(x), 0);
function highlight() {
  document.querySelectorAll(".note-link").forEach((b) => b.classList.toggle("active", b.dataset.path === current));
}
function reveal(path) { // open every folder above a note
  const parts = path.split("/").slice(0, -1);
  parts.forEach((_, i) => open.add("/" + parts.slice(0, i + 1).join("/")));
  localStorage.setItem("okf-open", JSON.stringify([...open]));
}

async function loadUsage() {
  const u = await api("/usage");
  const total = Math.max(u.llm.total_tokens, u.classifier.total_tokens, 1);
  for (const [k, l] of [["llm", u.llm], ["clf", u.classifier]]) {
    $("#bar-" + k).style.width = (100 * l.total_tokens / total) + "%";
    $("#num-" + k).innerHTML = `<em>${fmt(l.total_tokens)}</em> · ${l.calls} calls · ${fmt(l.input_tokens)} in / ${fmt(l.output_tokens)} out`;
  }
}

// -- views -------------------------------------------------------------------
function show(html, wide = false) { $("#view").className = wide ? "wide" : ""; $("#view").innerHTML = `<article>${html}</article>`; $("#view").scrollTop = 0; }

const ICONS = {
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="#f5a524" stroke-width="1.6"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  note: '<svg viewBox="0 0 24 24" fill="none" stroke="#ff4d2e" stroke-width="1.6"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="#7aa2ff" stroke-width="1.6"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>',
};

function home() {
  current = ""; highlight();
  const notes = countNotes(tree), folders = countFolders(tree);
  const title = notes ? `${notes} note${notes === 1 ? "" : "s"},<br><em>all in their place.</em>` : `An empty wiki,<br><em>ready to grow.</em>`;
  const nudge = noModel ? `<div class="notice">No model yet: you can browse the notes and the brain. To ask questions and add notes, <a href="#/settings">choose a model</a>.</div>` : "";
  show(`${nudge}<div class="hero">
      <div class="words">
        <div class="crumbs">YOUR WIKI</div>
        <h2>${title}</h2>
        <p>Drop documents anywhere on this page: the librarian reads each one, picks its folder (or makes a new one), merges it with what the wiki already knows and links related notes.</p>
        <div class="actions"><button class="btn" type="button" data-new-note>+ Write a note</button><label class="btn ghost" for="files">↑ Add files</label><a class="btn ghost" href="#/graph">Open the brain</a></div>
      </div>
      <div class="art" role="img" aria-label="A pixel-art crow under a red moon, beside a glowing graph of notes"></div>
    </div>
    <div class="cards">
      <div class="card">${ICONS.folder}<div><b>${folders} folder${folders === 1 ? "" : "s"}</b><span>New ones appear when nothing fits. Open them in the sidebar.</span></div></div>
      <div class="card">${ICONS.note}<div><b>Notes and files</b><span>Write a note with + Note, or drop .txt, .md and .pdf files. PDFs need a text layer.</span></div></div>
      <div class="card">${ICONS.search}<div><b>Ask anything</b><span>Answers cite the notes they come from: click one to read it.</span></div></div>
    </div>`, true);
}

async function showNote(path) {
  current = path; reveal(path); await loadTree();
  show(`<span class="spin"></span>`);
  try {
    const n = await api("/note?path=" + encodeURIComponent(path));
    const fm = n.frontmatter, gen = fm.generated || {};
    const sources = (fm.sources || []).map((s) =>
      `<div>[${esc(s.id)}] <a href="#/note/${esc(String(s.resource || "").replace(/^\//, ""))}">${esc(s.title || s.resource)}</a></div>`).join("");
    const kind = fm.type === "Source" ? "raw source · " : "";
    show(`<div class="crumbs">${kind}/${esc(n.path)}</div>
      <h2 class="title">${esc(n.title)}</h2>
      ${n.summary ? `<p class="lead">${esc(n.summary)}</p>` : ""}
      <div class="meta">${n.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
        ${gen.at ? `<span>updated ${esc(String(gen.at).slice(0, 10))}</span>` : ""}
        ${gen.by ? `<span>by ${esc(gen.by)}</span>` : ""}
        ${fm.resource ? `<span>from <a href="${esc(fm.resource)}" target="_blank" rel="noopener">${esc(fm.resource)}</a></span>` : ""}</div>
      <div class="md" id="body">${md(n.body)}</div>
      ${sources ? `<div class="sources"><div class="label" style="padding:0">Sources</div>${sources}</div>` : ""}`);
    wireLinks($("#body"), n.path);
  } catch (e) { show(`<p class="lead">${esc(e.message)}</p>`); }
}

function wireLinks(el, notePath) { // relative .md links open inside the UI
  const dir = notePath.split("/").slice(0, -1);
  el.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href");
    if (/^[a-z]+:/i.test(href)) { a.target = "_blank"; a.rel = "noopener"; return; }
    if (!href.endsWith(".md")) return;
    const parts = href.startsWith("/") ? [] : [...dir];
    for (const p of href.replace(/^\//, "").split("/")) p === ".." ? parts.pop() : p !== "." && parts.push(p);
    a.href = "#/note/" + parts.join("/");
  });
}

async function ask(question) {
  current = ""; highlight();
  history.pushState(null, "", "#/ask"); // leaving the note, so clicking it again reopens it
  show(`<div class="crumbs">question</div><div class="question">${esc(question)}</div><span class="spin"></span> <span class="crumbs">the researcher is reading…</span>`);
  try {
    const a = await post("/ask", { question });
    const label = (p) => { const s = p.split("/").pop().replace(/\.md$/, ""); return s.length > 32 ? s.slice(0, 31) + "…" : s; };
    const text = a.text.replace(/\[([^\[\]\s]+\.md)\]/g, (_, p) => `[${label(p)}](#/note/${p} "${p}")`);
    const u = a.usage;
    show(`<div class="crumbs">question</div><div class="question">${esc(question)}</div>
      <div class="md" id="answer">${md(text)}</div>
      <div class="read">${a.notes.length ? "read: " + a.notes.map((p) => `<a href="#/note/${esc(p)}">${esc(p)}</a>`).join(" · ") : "no notes read"}<br>
      <span style="color:var(--llm)">llm ${u.llm.calls} calls · ${fmt(u.llm.total_tokens)} tokens</span> ·
      <span style="color:var(--clf)">classifier ${u.classifier.calls} calls · ${fmt(u.classifier.total_tokens)} tokens</span></div>`);
    $("#answer").querySelectorAll('a[href^="#/note/"]').forEach((a) => a.classList.add("cite"));
  } catch (e) { show(`<div class="question">${esc(question)}</div><p class="lead">${esc(e.message)}</p>`); }
  loadUsage();
}

function route() {
  const h = decodeURIComponent(location.hash);
  if (h !== "#/graph") leaveGraph();
  if (h.startsWith("#/note/")) showNote(h.slice(7));
  else if (h === "#/graph") { current = ""; highlight(); showGraph(() => loadTree().then(loadUsage)); }
  else if (h === "#/settings") { current = ""; highlight(); showSettings(saved); }
  else if (h !== "#/ask") home();
}

// -- filing: drop, pick, paste ------------------------------------------------
const jobs = [];
let running = false;
function enqueue(label, send) {
  const el = document.createElement("div");
  el.className = "job"; el.innerHTML = `<div class="name"></div><div class="what"><span class="spin"></span> waiting…</div>`;
  el.querySelector(".name").textContent = label;
  $("#queue").append(el);
  jobs.push({ el, send });
  if (!running) drain();
}
async function drain() {
  running = true;
  while (jobs.length) {
    const { el, send } = jobs.shift();
    el.querySelector(".what").innerHTML = `<span class="spin"></span> the librarian is filing it…`;
    try {
      const r = await send();
      const where = r.action === "merged" ? "merged into" : "filed as";
      const made = r.created_folders.length ? ` · new folder ${esc(r.created_folders.join(", "))}` : "";
      el.classList.add("ok");
      el.querySelector(".what").innerHTML = `${where} <a href="#/note/${esc(r.note)}">${esc(r.title)}</a>${made}`;
      await loadTree(); loadUsage();
      if (!location.hash) home();
    } catch (e) {
      el.classList.add("err"); el.querySelector(".what").textContent = e.message;
      if (/\b40[13]\b/.test(e.message)) el.querySelector(".what").insertAdjacentHTML("beforeend", ' · <a href="#/settings">check the API key</a>');
    }
    setTimeout(() => el.remove(), 12000);
  }
  running = false;
}
const upload = (file) => enqueue(file.name, () =>
  api("/upload?filename=" + encodeURIComponent(file.name), { method: "POST", body: file }));

let depth = 0;
addEventListener("dragenter", (e) => { if (e.dataTransfer.types.includes("Files")) { depth++; $("#overlay").classList.add("on"); } });
addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; $("#overlay").classList.remove("on"); } });
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => {
  e.preventDefault(); depth = 0; $("#overlay").classList.remove("on");
  [...e.dataTransfer.files].forEach(upload);
});
$("#pick").onclick = () => $("#files").click();
$("#files").onchange = (e) => { [...e.target.files].forEach(upload); e.target.value = ""; };
function newNote() {
  $("#dlg").returnValue = ""; // Escape keeps the last value: never file twice
  $("#dlg").showModal();
  $("#txt").focus();
}
$("#add").onclick = newNote;
$("#view").addEventListener("click", (e) => { if (e.target.closest("[data-new-note]")) newNote(); });
$("#txt").onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#addform").requestSubmit($("#addform [value=ok]")); };
$("#brain").onclick = () => (location.hash = "#/graph");
$("#dlg").onclose = () => {
  if ($("#dlg").returnValue !== "ok") return;
  const text = $("#txt").value, title = $("#t").value.trim() || null;
  if (!text.trim()) return;
  enqueue(title || text.slice(0, 40) + "…", () =>
    post("/ingest", { text, title }));
  $("#txt").value = ""; $("#t").value = "";
};
$("#ask").onsubmit = (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q) ask(q); };

$("#gear").onclick = () => (location.hash = "#/settings");

// -- start -------------------------------------------------------------------
async function loadModels() {
  const h = await api("/health");
  $("#models").innerHTML = `<span class="chip" title="LLM">${esc(h.llm)}</span><span class="chip mode">${esc(h.mode)}</span>` +
    (h.classifier ? `<span class="chip" title="classifier">${esc(h.classifier)}</span>` : "");
}

async function saved(settings) { // the server rebuilt the wiki: show its models and (maybe new) folder
  await loadModels(); await loadTree(); loadUsage();
  noModel = !settings.ready;
  if (settings.ready) setTimeout(() => { if (location.hash === "#/settings") location.hash = ""; }, 800);
}

(async () => {
  const [settings] = await Promise.all([loadSettings(), loadModels()]);
  $("#gear").hidden = !settings;
  await loadTree(); loadUsage();
  noModel = Boolean(settings && !settings.ready);
  if (noModel && settings.editable && !countNotes(tree) && !location.hash) history.replaceState(null, "", "#/settings"); // first run: ask for a key
  route();
  addEventListener("hashchange", route);
})();
