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
  custom: "A local Laya server, or any other server of the System One API: its address, and a key if it wants one.",
};
const CLF_NAME = { openrouter: "OpenRouter", typesafe: "TypeSafe", custom: "Custom (local Laya server)" };

// The settings as the server sees them, or null when this app has none (404).
export async function loadSettings() {
  try { return await api("/settings"); } catch { return null; }
}

// `onSaved` runs after a save, with the new settings view.
export async function showSettings(onSaved) {
  const view = $("#view");
  view.className = "";
  const s = await api("/settings");
  const v = s.values, managed = new Set(s.managed), locked = (f) => !s.editable || managed.has(f);
  const lockNote = (f) => managed.has(f) ? '<div class="hint">Set by the environment or a command-line flag.</div>' : "";
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
      <details class="advanced" id="advanced"><summary>Advanced: CROW mode and its classifier, wiki folder</summary>
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
            <input id="classifier_base_url" placeholder="http://localhost:9000/v1" value="${esc(v.classifier_base_url)}" ${locked("classifier_base_url") ? "disabled" : ""}>
            <div class="hint">Where it answers POST …/systemone.</div>${lockNote("classifier_base_url")}</div>
          <div class="field"><label for="classifier_model">Classifier model</label>
            <input id="classifier_model" value="${esc(v.classifier_model)}" ${locked("classifier_model") ? "disabled" : ""}>${lockNote("classifier_model")}</div>
          <div class="field" id="f-classifier_api_key"><label for="classifier_api_key">Classifier key</label>
            <input id="classifier_api_key" type="password" ${locked("classifier_api_key") ? "disabled" : ""}>
            <div class="hint" id="clf-key-hint">Stays on this computer, in a file only you can read.</div>${lockNote("classifier_api_key")}</div>
        </div>
        <div class="field"><label for="bundle">Wiki folder</label>
          <input id="bundle" value="${esc(v.bundle)}" ${locked("bundle") ? "disabled" : ""}>${lockNote("bundle")}</div>
      </details>
      <div class="actions">
        <button class="btn" id="save" ${s.editable ? "" : "disabled"}>${s.ready ? "Save" : "Save and start →"}</button>
        <button class="btn ghost" type="button" id="test" ${s.editable ? "" : "disabled"}>Test connection</button>
        <span class="status" id="status"></span>
      </div>
    </form>
    <div class="aside-note">Prefer the terminal? <code>okf-wiki setup</code> asks the same questions.<br>Saved in ${esc(s.file)}</div></article>`;

  const provider = { get value() { return $('#provider input:checked')?.value || v.provider; } };
  const sync = (changed) => { // show what the chosen provider needs
    const p = s.providers[provider.value], same = provider.value === v.provider;
    $("#help").innerHTML = HELP[provider.value] || "";
    $("#f-base_url").style.display = provider.value === "custom" ? "" : "none";
    $("#f-api_key").style.display = p.needs_key || provider.value === "custom" ? "" : "none";
    $("#api_key").placeholder = same && v.api_key ? `saved: ${v.api_key} · leave empty to keep` : "paste your key";
    $("#key-hint").textContent = same || !v.api_key ? "Stays on this computer, in a file only you can read."
      : "A new provider needs its own key: the saved one is not sent to it.";
    if (changed && !locked("model")) $("#model").value = p.models[0] || "";
    $("#suggest").innerHTML = locked("model") ? "" : p.models.map((m) => `<button type="button" data-model="${esc(m)}">${esc(m)}</button>`).join("");
    syncClassifier(false);
  };
  const clf = () => $("#classifier_provider").value;
  const syncClassifier = (changed) => { // the classifier has its own provider, shown only in CROW mode
    const cp = s.classifier_providers[clf()], crow = $("#mode").value === "crow";
    const shared = clf() === "openrouter" && provider.value === "openrouter"; // one OpenRouter key serves both
    const own = v.classifier_api_key && clf() === v.classifier_provider && !(v.provider === "openrouter" && v.classifier_provider === "openrouter");
    $("#crow-fields").style.display = crow ? "" : "none";
    $("#clf-help").textContent = CLF_HELP[clf()] || "";
    $("#f-classifier_base_url").style.display = clf() === "custom" ? "" : "none";
    $("#f-classifier_api_key").style.display = cp.needs_key || clf() === "custom" ? "" : "none";
    $("#classifier_api_key").placeholder = own ? `saved: ${v.classifier_api_key} · leave empty to keep`
      : shared ? "empty: the OpenRouter key above serves it too" : clf() === "custom" ? "its key, if it wants one" : `your ${CLF_NAME[clf()] || clf()} key`;
    $("#clf-key-hint").textContent = clf() === v.classifier_provider || !v.classifier_api_key ? "Stays on this computer, in a file only you can read."
      : "A new provider needs its own key: the saved one is not sent to it.";
    if (changed && !locked("classifier_model")) $("#classifier_model").value = cp.models[0] || "";
    if (crow && cp.needs_key && !shared && !own) $("#advanced").open = true; // CROW cannot start without it
  };
  const fields = ["provider", "base_url", "api_key", "model", "mode", "reasoning",
    "classifier_provider", "classifier_base_url", "classifier_model", "classifier_api_key", "bundle"];
  const value = (f) => f === "provider" ? provider.value
    : f === "reasoning" ? ($("#reasoning").checked ? "true" : "") // "" is the default: off
    : f === "base_url" && provider.value !== "custom" ? "" // presets bring their own
    : f === "classifier_base_url" && clf() !== "custom" ? ""
    : f === "classifier_model" && $("#classifier_model").value.trim() === s.classifier_providers[clf()].models[0] ? "" // the pinned default stays the code's
    : $("#" + f).value.trim();
  const changes = () => Object.fromEntries(fields.filter((f) => !locked(f)).map((f) => [f, value(f)]));

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

  $("#provider").onchange = () => { sync(true); refreshModels(); };
  $("#suggest").onclick = (e) => { const m = e.target.dataset?.model; if (m) $("#model").value = m; };
  $("#mode").onchange = () => syncClassifier(false);
  $("#classifier_provider").onchange = () => syncClassifier(true);
  $("#api_key").onchange = $("#base_url").onchange = refreshModels;
  sync(false);
  refreshModels();
  const status = (text, kind = "") => { const el = $("#status"); el.className = "status " + kind; el.innerHTML = text; };

  $("#test").onclick = async () => {
    status('<span class="spin"></span> asking the model…');
    try {
      const r = await post("/settings/test", changes()), c = r.classifier; // c: only in CROW mode
      const text = (r.ok ? `✓ ${esc(r.model)} answered` : `✗ ${esc(r.error)}`)
        + (!c ? "" : c.ok ? ` · ✓ classifier ${esc(c.model)} answered` : ` · ✗ classifier: ${esc(c.error)}`);
      status(text, r.ok && (!c || c.ok) ? "ok" : "err");
    } catch (e) { status(`✗ ${esc(e.message)}`, "err"); }
  };
  $("#settings").onsubmit = async (e) => {
    e.preventDefault();
    status('<span class="spin"></span> saving…');
    try {
      const saved = await post("/settings", changes());
      status("✓ saved", "ok");
      onSaved(saved);
    } catch (err) { status(`✗ ${esc(err.message)}`, "err"); }
  };
}
