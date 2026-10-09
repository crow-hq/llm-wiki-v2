// Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later
// Settings: which model writes the wiki, with which key. Saved to the settings file by the server.

import { $, api, esc, post } from "./util.js";

const HELP = {
  openrouter: 'One key for hundreds of models, and for the CROW classifier too. Create it at <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>.',
  openai: 'Create a key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com/api-keys</a>.',
  gemini: 'Create a key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>.',
  ollama: 'Free and local, no key: install <a href="https://ollama.com/download" target="_blank" rel="noopener">Ollama</a>, then run <code>ollama pull gemma4</code>. CROW mode also needs a classifier (Advanced): an OpenRouter or TypeSafe key, or a local server. Or choose classic there.',
  custom: "Any OpenAI-compatible server: vLLM, LM Studio, llama.cpp, a gateway. The key is optional.",
};
const TAGLINE = { openrouter: "one key, every model", openai: "GPT models", gemini: "Google AI Studio", ollama: "free · local · no key", custom: "vLLM, LM Studio…" };
const NAME = { openrouter: "OpenRouter", openai: "OpenAI", gemini: "Gemini", ollama: "Ollama", custom: "Custom" };
// Where the CROW classifier runs: its own provider, whatever the model's is.
const CLF_HELP = {
  openrouter: "Jev on OpenRouter. With OpenRouter as the provider above, its key serves the classifier too; otherwise paste an OpenRouter key.",
  typesafe: 'Jev straight from TypeSafe: paste a TypeSafe key.',
  custom: "Any server of the System One API, such as Laya on this computer (LAYA_PORT=8001 laya-serve: the wiki takes 8000): its address, and a key if it wants one. Its thresholds start from the Laya preset, and its model from typed-decisions, the Laya checkpoint that preset was measured on.",
};
const CLF_NAME = { openrouter: "OpenRouter (Jev)", typesafe: "TypeSafe (Jev)", custom: "Custom" };
// How each model is called: [field, label, what it does, input limits]. Kept per provider, like its key and model.
const LLM_PARAMS = [
  ["temperature", "Temperature", "How freely the model writes: 0 gives the same words every time. The wiki uses 0.1: filing wants consistency.", 'min="0" max="2"'],
  ["seed", "Seed", "A number sent with every request: providers that support it answer the same prompt the same way. Empty: none.", ""],
  ["pin_provider", "Providers (OpenRouter)", "Comma-separated, such as DeepInfra, Novita: only these answer, in this order, and a call fails when none is up. Empty: OpenRouter chooses.", "", "text"],
  ["timeout", "Timeout (seconds)", "How long to wait for one reply before it fails. Raise it for a slow local model.", 'min="1"'],
  ["read_chars", "Characters per call", "Most characters one request to the model may hold, text and rules together. A longer document is read in parts. Empty: automatic, by provider (100000 on OpenRouter, OpenAI and Gemini; 16000 on Ollama and custom servers).", 'min="8000"'],
];
const CLF_PARAMS = [
  ["classifier_timeout", "Timeout (seconds)", "How long to wait for one decision; a slower one is dropped and the LLM decides.", 'min="0.5"'],
  ["classifier_attempts", "Attempts", "Tries per decision before the LLM takes it.", 'min="1"'],
  ["classifier_state_chars", "Note text read", "Characters of the incoming note each decision reads: its title, summary and opening.", 'min="500"'],
  ["classifier_request_chars", "Request size", "Most characters per request; a longer list of candidates is split into several requests.", 'min="1000"'],
];
// The CROW decision thresholds: [name, label, "%" for a probability shown as a percentage, what it does, the choices if it is not a number].
const CROW = [
  ["tau_route", "Routing: sure", "%", "A routing choice at least this sure follows only its best folder; below, the best two paths are followed."],
  ["tau_path", "Routing: ask the LLM below", "%", "When the best folder path scores less than this, the LLM decides where the note goes. Higher: the LLM decides more often."],
  ["tau_ing", "Same subject", "%", "Existing notes above this are candidates to merge the new note into."],
  ["tau_cons", "Merge", "%", "The classifier must be at least this sure to merge into an existing note; otherwise a new note is written."],
  ["tau_link", "See also", "%", "Notes above this get a See-also link to each other."],
  ["tau_fold", "Search: folders", "%", "To answer a question, folders above this are opened."],
  ["tau_ret", "Search: notes", "%", "To answer a question, notes above this are read."],
  ["votes", "Routing votes", "", "Classifier calls averaged when a routing decision is close to a threshold. 1: one call, no averaging."],
  ["vote_band", "Routing: close means within", "%", "How near a threshold, or a tie between two folders, a routing decision must be to be asked again."],
  ["fallback_menu", "Routing: LLM fallback menu", "", "Folders the LLM chooses among when the classifier is not sure: the folders it visited, or those plus the whole tree.", ["visited", "tree"]],
  ["beam", "Routing beam", "", "Folder paths followed at once when a routing choice is unsure."],
  ["retrieval_beam", "Search beam", "", "Folders opened per level to answer a question."],
  ["k", "Notes read (top k)", "", "At most this many notes are read to write an answer."],
];
const CHOICES = new Set(CROW.filter(([, , , , choices]) => choices).map(([n]) => n));
const PERCENT = new Set(CROW.filter(([, , unit]) => unit).map(([n]) => n));
const shown = (n, x) => x === undefined || x === null || x === "" ? "" : PERCENT.has(n) ? String(Math.round(Number(x) * 1000) / 10) : String(x);
const PRESET_NAME = { jev: "Jev", laya: "Laya" };

// The settings as the server sees them, or null when this app has none (404).
export async function loadSettings() {
  try { return await api("/settings"); } catch { return null; }
}

// The form as it was left unsaved: kept while other pages are open, dropped by a save or "discard".
// In this page's memory only, never stored: it may hold a key being typed.
let pending = null;

// `onSaved` runs after a save, with the new settings view; `said` is shown in the status line.
export async function showSettings(onSaved, said = "") {
  const view = $("#view");
  view.className = "";
  const s = await api("/settings");
  const v = s.values, managed = new Set(s.managed), locked = (f) => !s.editable || managed.has(f);
  const lockNote = (f) => managed.has(f) ? '<div class="hint">Set by the environment or a command-line flag.</div>' : "";
  const params = (list) => `<div class="thresholds">${list.map(([id, label, hint, limits, type = "number"]) => `<label class="th"><span>${esc(label)}</span>
    <span class="num"><input id="${id}" type="${type}" step="any" ${limits} value="${esc(v[id])}" ${locked(id) ? "disabled" : ""}></span>
    <small>${esc(managed.has(id) ? "Set by the environment or a command-line flag." : hint)}</small></label>`).join("")}</div>`;
  const clfOptions = Object.keys(s.classifier_providers).map((p) =>
    `<option value="${esc(p)}"${p === v.classifier_provider ? " selected" : ""}>${esc(CLF_NAME[p] || p)}</option>`).join("");
  const tiles = Object.keys(s.providers).map((p) => `<label class="tile${p === "ollama" ? " free" : ""}">
    <input type="radio" name="provider" value="${esc(p)}"${p === v.provider ? " checked" : ""}${locked("provider") ? " disabled" : ""}>
    <b>${esc(NAME[p] || p)}</b><span>${esc(TAGLINE[p] || "")}</span></label>`).join("");
  view.innerHTML = `<article class="settings">
    <div class="crumbs">${s.ready ? "SETTINGS" : "FIRST RUN · ONE MINUTE"}</div>
    <h2>Connect a <em>model.</em></h2>
    <p class="lead">The model reads your documents and writes the wiki. Pick who runs it.</p>
    ${s.editable ? "" : '<div class="notice">Settings can be changed only from this machine, at http://localhost.</div>'}
    ${s.missing.length ? `<div class="notice">Not ready yet: ${s.missing.map(esc).join(" ")}</div>` : ""}
    <form id="settings" autocomplete="off">
      <fieldset class="field" id="provider"><legend>Provider</legend>
        <div class="tiles">${tiles}</div>
        <div class="hint" id="help"></div>${lockNote("provider")}</fieldset>
      <div class="field" id="f-base_url"><label for="base_url">Server address</label>
        <input id="base_url" placeholder="http://localhost:8000/v1" value="${esc(v.base_url)}" ${locked("base_url") ? "disabled" : ""}>
        ${lockNote("base_url")}</div>
      <div class="field" id="f-api_key"><label for="api_key">API key</label>
        <input id="api_key" type="password" ${locked("api_key") ? "disabled" : ""}>
        <div class="hint" id="key-hint">Stays on this computer, in a file only you can read.</div>${lockNote("api_key")}</div>
      <div class="field"><label for="model">Model</label>
        <input id="model" list="models-list" value="${esc(v.model)}" ${locked("model") ? "disabled" : ""}>
        <datalist id="models-list"></datalist><div class="suggest" id="suggest"></div><div class="hint" id="models-hint"></div>${lockNote("model")}</div>
      <div class="field"><label class="check"><input type="checkbox" id="reasoning"${v.reasoning ? " checked" : ""} ${locked("reasoning") ? "disabled" : ""}>
        Let the model think before it answers</label>
        <div class="hint">Off by default: thinking makes every note several times slower and costlier, and rarely files it better. Kept to a minimum on OpenRouter; other providers follow their model's default.</div>${lockNote("reasoning")}</div>
      <div class="field"><label>Model parameters</label>${params(LLM_PARAMS)}</div>
      <details class="advanced" id="advanced"><summary>Advanced: CROW mode, the classifier and its parameters, wiki folder, parallel filing</summary>
        <div class="field"><label for="mode">Who decides where notes go</label>
          <select id="mode" ${locked("mode") ? "disabled" : ""}>
            <option value="crow"${v.mode === "crow" ? " selected" : ""}>crow — a typed classifier decides (recommended: faster, fewer tokens)</option>
            <option value="classic"${v.mode === "classic" ? " selected" : ""}>classic — the model decides</option>
          </select>${lockNote("mode")}</div>
        <div id="crow-fields">
          <div class="field"><label for="classifier_provider">Classifier provider</label>
            <select id="classifier_provider" ${locked("classifier_provider") ? "disabled" : ""}>${clfOptions}</select>
            <div class="hint" id="clf-help"></div>${lockNote("classifier_provider")}</div>
          <div class="field" id="f-classifier_base_url"><label for="classifier_base_url">Classifier server address</label>
            <input id="classifier_base_url" placeholder="http://127.0.0.1:8001/v1" value="${esc(v.classifier_base_url)}" ${locked("classifier_base_url") ? "disabled" : ""}>
            <div class="hint">Where it answers POST …/systemone.</div>${lockNote("classifier_base_url")}</div>
          <div class="field"><label for="classifier_model">Classifier model</label>
            <input id="classifier_model" value="${esc(v.classifier_model)}" ${locked("classifier_model") ? "disabled" : ""}>${lockNote("classifier_model")}</div>
          <div class="field" id="f-classifier_api_key"><label for="classifier_api_key">Classifier key</label>
            <input id="classifier_api_key" type="password" ${locked("classifier_api_key") ? "disabled" : ""}>
            <div class="hint" id="clf-key-hint">Stays on this computer, in a file only you can read.</div>${lockNote("classifier_api_key")}</div>
          <div class="field"><label>Classifier parameters</label>${params(CLF_PARAMS)}</div>
          <div class="field" id="f-crow"><label>Decision thresholds</label>
            <div class="thresholds">${CROW.map(([n, label, unit, hint, choices]) => `<label class="th"><span>${esc(label)}</span>
              <span class="num">${choices ? `<select id="crow_${n}" ${locked("crow_" + n) ? "disabled" : ""}>${choices.map((c) => `<option${c === v["crow_" + n] ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`
                : `<input id="crow_${n}" type="number" step="any" min="${unit ? 0 : 1}"${unit ? ' max="100"' : ""} value="${shown(n, v["crow_" + n])}" ${locked("crow_" + n) ? "disabled" : ""}>`}${unit ? "<i>%</i>" : ""}</span>
              <small>${esc(hint)}</small></label>`).join("")}</div>
            <div class="hint" id="crow-hint"></div></div>
        </div>
        <div class="field"><label for="bundle">Wiki folder</label>
          <input id="bundle" value="${esc(v.bundle)}" ${locked("bundle") ? "disabled" : ""}>${lockNote("bundle")}</div>
        <div class="field"><label for="concurrency">Documents filed in parallel</label>
          <input id="concurrency" type="number" min="1" max="16" value="${esc(v.concurrency)}" ${locked("concurrency") ? "disabled" : ""}>
          <div class="hint">How many documents are read and summarized at once; they are still filed one at a time. 1 files them strictly in turn.</div>${lockNote("concurrency")}</div>
        <div class="field"><label for="note_chars">Note length</label>
          <input id="note_chars" type="number" min="1000" step="any" value="${esc(v.note_chars)}" ${locked("note_chars") ? "disabled" : ""}>
          <div class="hint">About how many characters a note body may have; the model is asked to stay within it, and a longer note is not cut. Empty: automatic (10000, less when Characters per call is small).</div>${lockNote("note_chars")}</div>
      </details>
      <div class="actions">
        <button class="btn" id="save" ${s.editable ? "" : "disabled"}>${s.ready ? "Save" : "Save and start →"}</button>
        <button class="btn ghost" type="button" id="test" ${s.editable ? "" : "disabled"}>Test connection</button>
        <span class="status" id="status"></span>
      </div>
    </form>
    <div class="aside-note">Prefer the terminal? <code>llmwiki2 setup</code> asks the same questions.<br>Saved in ${esc(s.file)}</div></article>`;

  const provider = { get value() { return $('#provider input:checked')?.value || v.provider; } };
  const clf = () => $("#classifier_provider").value;
  // Each provider keeps its own model, address and key: the server remembers the saved ones (`s.memory`),
  // and `drafts` what was typed for each one here, so switching back and forth loses nothing.
  const drafts = { llm: {}, classifier: {} };
  const memo = (kind, p) => s.memory[kind][p] || { model: "", base_url: "", api_key: "" };
  const crowIds = CROW.map(([n]) => "crow_" + n);
  const paramIds = { llm: LLM_PARAMS.map(([id]) => id), classifier: CLF_PARAMS.map(([id]) => id) };
  const inputs = {
    llm: ["model", "base_url", "api_key", ...paramIds.llm],
    classifier: ["classifier_model", "classifier_base_url", "classifier_api_key", ...paramIds.classifier, ...crowIds],
  };
  const local = (cp) => cp === "custom"; // a server of your own: its address shown, a key only if it wants one
  const paramName = (kind, id) => kind === "llm" ? id : id.slice("classifier_".length); // the name in s.params and s.memory
  const defaults = { llm: (p) => s.providers[p].models[0] || "", classifier: (p) => s.classifier_providers[p].models[0] || "" };
  let at = { llm: v.provider, classifier: v.classifier_provider };
  const keep = (kind) => (drafts[kind][at[kind]] = Object.fromEntries(inputs[kind].map((f) => [f, $("#" + f).value])));
  const load = (kind, p) => { // what the provider had: typed here, else saved, else its default
    const typed = drafts[kind][p], m = memo(kind, p), [model, url, key] = inputs[kind];
    const had = typed || { [model]: m.model || defaults[kind](p), [url]: m.base_url, [key]: "",
      ...Object.fromEntries(paramIds[kind].map((id) => [id, String(m[paramName(kind, id)] ?? s.params[kind][paramName(kind, id)])])),
      ...(kind === "classifier" ? Object.fromEntries(CROW.map(([n]) => ["crow_" + n, shown(n, m.crow?.[n])])) : {}) };
    for (const f of inputs[kind]) if (!locked(f)) $("#" + f).value = had[f];
    at[kind] = p;
  };

  const sync = () => { // show what the chosen provider needs, and whether a key is saved for it
    const p = provider.value, preset = s.providers[p], saved = memo("llm", p).api_key;
    $("#help").innerHTML = HELP[p] || "";
    $("#f-base_url").style.display = p === "custom" ? "" : "none";
    $("#f-api_key").style.display = preset.needs_key || p === "custom" ? "" : "none";
    $("#api_key").placeholder = saved ? `saved: ${saved} · leave empty to keep` : p === "custom" ? "its key, if it wants one" : "paste your key";
    $("#key-hint").textContent = saved ? `A key is saved for ${NAME[p] || p}: leave the field empty to keep it. Each provider keeps its own.`
      : "Stays on this computer, in a file only you can read.";
    $("#suggest").innerHTML = locked("model") ? "" : preset.models.map((m) => `<button type="button" data-model="${esc(m)}">${esc(m)}</button>`).join("");
    syncClassifier();
  };
  const syncClassifier = () => { // the classifier has its own provider, shown only in CROW mode
    const cp = clf(), preset = s.classifier_providers[cp], crow = $("#mode").value === "crow", saved = memo("classifier", cp).api_key;
    const shared = cp === "openrouter" && provider.value === "openrouter"; // one OpenRouter key serves both
    $("#crow-fields").style.display = crow ? "" : "none";
    $("#clf-help").textContent = CLF_HELP[cp] || "";
    $("#f-classifier_base_url").style.display = local(cp) ? "" : "none";
    $("#f-classifier_api_key").style.display = preset.needs_key || local(cp) ? "" : "none";
    $("#classifier_api_key").placeholder = saved ? `saved: ${saved} · leave empty to keep`
      : shared ? "empty: the OpenRouter key above serves it too" : local(cp) ? "its key, if it wants one" : `your ${CLF_NAME[cp] || cp} key`;
    $("#crow-hint").innerHTML = `Filled from the ${PRESET_NAME[preset.crow_preset] || preset.crow_preset} preset when you choose the classifier `
      + `(Laya's for Custom, Jev's for OpenRouter and TypeSafe); change them when you need to. <a href="#" id="crow-reset">Reset to the preset</a>`;
    if (crow && preset.needs_key && !saved && !shared) $("#advanced").open = true; // CROW cannot start without it
  };
  const fields = ["provider", "base_url", "api_key", "model", "mode", "reasoning",
    "classifier_provider", "classifier_base_url", "classifier_model", "classifier_api_key", "bundle", "concurrency", "note_chars", ...paramIds.llm, ...paramIds.classifier, ...crowIds];
  const number = (f) => { // a threshold as the server takes it: a fraction for a percentage, "" for the preset's; a choice as it is
    const n = f.slice(5), raw = $("#" + f).value.trim();
    return raw === "" || CHOICES.has(n) ? raw : PERCENT.has(n) ? String(Number(raw) / 100) : String(Math.round(Number(raw)));
  };
  const value = (f) => f === "provider" ? provider.value
    : f === "reasoning" ? ($("#reasoning").checked ? "true" : "") // "" is the default: off
    : f === "base_url" && provider.value !== "custom" ? "" // presets bring their own; the server remembers the custom one
    : f === "classifier_base_url" && !local(clf()) ? ""
    : f.startsWith("crow_") ? number(f)
    : f === "classifier_model" && $("#classifier_model").value.trim() === s.classifier_providers[clf()].models[0] ? "" // the pinned default stays the code's
    : $("#" + f).value.trim();
  const changes = () => Object.fromEntries(fields.filter((f) => !locked(f)).map((f) => [f, value(f)]));

  const status = (text, kind = "") => { const el = $("#status"); el.className = "status " + kind; el.innerHTML = text; };
  const snapshot = () => ({ // everything on the form, to come back to it after visiting another page
    at: { ...at }, drafts: structuredClone(drafts), reasoning: $("#reasoning").checked,
    values: Object.fromEntries(fields.filter((f) => f !== "provider" && f !== "reasoning").map((f) => [f, $("#" + f).value])),
  });
  const unsaved = () => { pending = snapshot(); status('Unsaved changes: Save keeps them · <a href="#" id="discard">discard</a>'); };
  if (pending) { // the form as it was left
    const was = pending;
    const tile = $(`#provider input[value="${was.at.llm}"]`);
    if (tile) tile.checked = true;
    Object.assign(drafts, structuredClone(was.drafts)); at = { ...was.at };
    for (const [f, val] of Object.entries(was.values)) $("#" + f).value = val;
    $("#reasoning").checked = was.reasoning;
    unsaved();
  } else if (said) status(said, "ok");

  let asked = 0; // only the latest answer fills the list
  const refreshModels = async () => { // our suggestions first, then what the provider offers today
    const ours = s.providers[provider.value].models, ticket = ++asked;
    const fill = (live) => ($("#models-list").innerHTML = [...new Set([...ours, ...live])].map((m) => `<option value="${esc(m)}">`).join(""));
    fill([]);
    if (!s.editable) return;
    $("#models-hint").textContent = "Asking the provider for its models…";
    try {
      const { models } = await post("/settings/models", changes());
      if (ticket !== asked) return;
      fill(models);
      $("#models-hint").textContent = models.length ? `${models.length} models available now: type to search.`
        : "The provider did not list its models (a key may be needed): these are our suggestions.";
    } catch { if (ticket === asked) $("#models-hint").textContent = ""; }
  };

  $("#provider").onchange = () => { keep("llm"); load("llm", provider.value); sync(); refreshModels(); unsaved(); };
  $("#classifier_provider").onchange = () => { keep("classifier"); load("classifier", clf()); syncClassifier(); unsaved(); };
  $("#suggest").onclick = (e) => { const m = e.target.dataset?.model; if (m) { $("#model").value = m; unsaved(); } };
  $("#mode").onchange = () => { syncClassifier(); unsaved(); };
  $("#settings").oninput = (e) => { if (e.target.type !== "radio") unsaved(); };
  $("#status").onclick = (e) => { if (e.target.id === "discard") { e.preventDefault(); pending = null; showSettings(onSaved); } };
  $("#crow-hint").onclick = (e) => { // back to the classifier's preset values
    if (e.target.id !== "crow-reset") return;
    e.preventDefault();
    const preset = s.crow_presets[s.classifier_providers[clf()].crow_preset];
    for (const [n] of CROW) if (!locked("crow_" + n)) $("#crow_" + n).value = shown(n, preset[n]);
    unsaved();
  };
  $("#api_key").onchange = $("#base_url").onchange = refreshModels;
  sync();
  refreshModels();

  $("#test").onclick = async () => {
    status('<span class="spin"></span> asking the model…');
    try {
      const r = await post("/settings/test", changes()), c = r.classifier; // c: only in CROW mode
      const text = (r.ok ? `✓ ${esc(r.model)} answered` : `✗ ${esc(r.error)}`)
        + (!c ? "" : c.ok ? ` · ✓ classifier ${esc(c.model)} answered` : ` · ✗ classifier: ${esc(c.error)}`)
        + (r.warning ? ` · ⚠ ${esc(r.warning)}` : "");
      status(text, r.ok && (!c || c.ok) ? "ok" : "err");
    } catch (e) { status(`✗ ${esc(e.message)}`, "err"); }
  };
  $("#settings").onsubmit = async (e) => {
    e.preventDefault();
    status('<span class="spin"></span> saving…');
    try {
      const saved = await post("/settings", changes());
      pending = null;
      await onSaved(saved);
      if (!s.ready && saved.ready) { // first run: the wiki can start now
        status("✓ saved", "ok");
        setTimeout(() => { if (location.hash === "#/settings") location.hash = ""; }, 800);
      } else showSettings(onSaved, "✓ Saved"); // the form as saved: keys shown as saved, nothing typed left behind
    } catch (err) { // the server says which key is missing and where it goes: take the user there
      status(`✗ ${esc(err.message)}`, "err");
      if (/Classifier key/.test(err.message)) { $("#advanced").open = true; $("#classifier_api_key").focus(); }
      else if (/API key/.test(err.message)) $("#api_key").focus();
    }
  };
}
