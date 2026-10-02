// Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later
// Filing: dropped files and new notes wait in one queue, sent to the server in order, as many at once as the wiki's
// concurrency allows (four by default). The queue is kept in IndexedDB, so a reload picks it up; one tab at a time
// sends it (a Web Lock), and Stop drops whatever is still waiting.

import { $, api, esc, post } from "./util.js";

const jobs = []; // not filed yet, in order: {id, label, file} or {id, label, text, title}, each with `el`, its row
const sending = new Set(); // the jobs handed to the server: Stop leaves them, as the server files them anyway
let running = false, waiting = false, total = 0, filed = 0, failed = 0, refresh = async () => {};
let width = 1, lanes = 0, finish = null; // jobs in flight at most, senders now running, and what ends the run

// -- the stored queue: a job leaves it once sent, so a reload never sends one twice ---------------------------
let opened;
const db = () => (opened ??= new Promise((resolve, reject) => {
  const r = indexedDB.open("okf-queue", 1);
  r.onupgradeneeded = () => r.result.createObjectStore("jobs", { keyPath: "id", autoIncrement: true });
  r.onsuccess = () => resolve(r.result);
  r.onerror = () => reject(r.error);
}));
async function stored(fn) { // fn(store) in one transaction; resolves with its request's result once written
  const tx = (await db()).transaction("jobs", "readwrite"), req = fn(tx.objectStore("jobs"));
  await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = tx.onabort = () => reject(tx.error); });
  return req?.result;
}
const keep = ({ el, ...job }) => stored((s) => s.put(job)).catch(() => undefined); // its key; none without IndexedDB
const forget = (ids) => stored((s) => ids.forEach((id) => s.delete(id))).catch(() => {});
async function claim(job) { // take it out of the stored queue; false when another tab sent or stopped it meanwhile
  let mine = true;
  if (job.id !== undefined) await stored((s) => {
    s.get(job.id).onsuccess = (e) => { mine = Boolean(e.target.result); s.delete(job.id); };
  }).catch(() => {});
  return mine;
}

// -- the panel: a count, a bar and a list that scrolls, so the header's buttons stay in reach ------------------
function render() {
  const left = jobs.length, rows = $("#jobs-list").children.length;
  if (!rows) total = filed = failed = 0; // emptied: the next batch counts from one
  $("#jobs").hidden = !rows;
  $("#jobs-count").textContent = !left ? `${filed} filed` + (failed ? ` · ${failed} failed` : "")
    : waiting ? "Waiting for another tab to finish"
    : sending.size > 1 ? `Filing ${total - left + 1}–${total - left + sending.size} of ${total}` : `Filing ${total - left + 1} of ${total}`;
  $("#jobs-done").style.width = `${100 * (total - left) / (total || 1)}%`;
  $("#jobs-stop").hidden = !jobs.some((j) => !sending.has(j));
}
function say(job, kind, html) { job.el.className = "task " + kind; job.el.querySelector(".what").innerHTML = html; }
function add(job) {
  job.el = document.createElement("div");
  job.el.innerHTML = '<div class="name"></div><div class="what"></div>';
  job.el.querySelector(".name").textContent = job.label;
  say(job, "wait", "");
  $("#jobs-list").append(job.el);
  jobs.push(job); total++;
}
function drop(job) { jobs.splice(jobs.indexOf(job), 1); job.el.remove(); total--; }
function done(job, kind, html) { // the row stays a while with the result
  jobs.splice(jobs.indexOf(job), 1); sending.delete(job); say(job, kind, html);
  if (kind === "ok") filed++; else failed++;
  setTimeout(() => { job.el.remove(); render(); }, 12000);
}
function fold(folded) {
  $("#jobs").classList.toggle("folded", folded);
  $("#jobs-fold").setAttribute("aria-expanded", String(!folded));
  localStorage.setItem("okf-queue-folded", folded ? "1" : "");
}

// -- filing ---------------------------------------------------------------------------------------------------------
const answering = () => fetch("/health").then((r) => r.ok, () => false);
const concurrency = () => fetch("/health").then((r) => r.json(), () => null)
  .then((h) => Math.min(16, Math.max(1, Number(h?.concurrency) || 1)));
async function fileNext(job) {
  sending.add(job);
  if (!await claim(job)) { sending.delete(job); drop(job); render(); return; }
  say(job, "now", '<span class="spin"></span> the librarian is filing it…'); render();
  let r;
  try {
    r = await (job.file
      ? api("/upload?filename=" + encodeURIComponent(job.file.name), { method: "POST", body: job.file })
      : post("/ingest", { text: job.text, title: job.title }));
  } catch (e) {
    if (e instanceof TypeError && !await answering()) { // no server (stopped, restarting): keep the job, try again
      sending.delete(job); await keep(job);
      say(job, "now", '<span class="spin"></span> the wiki is not answering: trying again…'); render();
      return new Promise((wake) => setTimeout(wake, 5000));
    }
    const key = /\b40[13]\b|no API key/.test(e.message) ? ' · <a href="#/settings">check the API key</a>' : "";
    done(job, "err", esc(e.message) + key); render();
    return;
  }
  const where = { merged: "merged into", unchanged: "already filed as" }[r.action] || "filed as";
  const made = r.created_folders.length ? ` · new folder ${esc(r.created_folders.join(", "))}` : "";
  done(job, "ok", `${where} <a href="#/note/${esc(r.note)}">${esc(r.title)}</a>${made}`); render();
  await refresh().catch(() => {});
}
const idle = () => jobs.find((j) => !sending.has(j));
function spawn() { // senders up to `width`, each taking the next job not yet sent until none is left
  while (lanes < width && idle()) {
    lanes++;
    (async () => { for (let job; (job = idle());) await fileNext(job); })().finally(() => { if (!--lanes) finish?.(); });
  }
}
function drain() {
  if (running) return void (finish && spawn()); // still waiting for the lock: the run takes the new job when it starts
  running = true;
  const work = async () => {
    waiting = false;
    width = await concurrency();
    try { await new Promise((done) => { finish = done; spawn(); if (!lanes) done(); }); } finally { running = false; finish = null; render(); }
  };
  if (!navigator.locks) return void work(); // not a secure context (a LAN address): this tab sends alone
  navigator.locks.request("okf-queue", { ifAvailable: true }, (lock) => {
    if (lock) return work();
    waiting = true; render();
    return navigator.locks.request("okf-queue", work);
  });
}

// data: {file} or {text, title}
export async function enqueue(label, data) {
  const job = { label, ...data };
  job.id = await keep(job);
  add(job); render(); drain();
}

// `onFiled` runs after each filing (the page reloads its tree); a queue left by an earlier page goes on.
export async function startQueue(onFiled) {
  refresh = onFiled;
  fold(localStorage.getItem("okf-queue-folded") === "1");
  for (const job of await stored((s) => s.getAll()).catch(() => [])) add(job);
  render();
  if (jobs.length) drain();
}

$("#jobs-stop").onclick = () => {
  const dropped = jobs.filter((j) => !sending.has(j));
  dropped.forEach(drop); forget(dropped.map((j) => j.id)); render();
};
$("#jobs-fold").onclick = () => fold(!$("#jobs").classList.contains("folded"));
