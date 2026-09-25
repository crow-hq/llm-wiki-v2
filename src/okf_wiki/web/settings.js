// Copyright 2026 Federico Cesarini, Marco Sassarini. Licensed under the Apache License, Version 2.0.
// Settings: which model writes the wiki, with which key. Saved to the settings file by the server.

import { $, api, esc, post } from "./util.js";

const HELP = {
  openrouter: 'One key for hundreds of models, and for the CROW classifier too. Create it at <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>.',
  openai: 'Create a key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com/api-keys</a>.',
  gemini: 'Create a key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>.',
  ollama: 'Free and local, no key: install <a href="https://ollama.com/download" target="_blank" rel="noopener">Ollama</a>, then run <code>ollama pull gemma4</code>.',
  custom: "Any OpenAI-compatible server: vLLM, LM Studio, llama.cpp, a gateway. The key is optional.",
};

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
  const options = (list, now) => list.map((x) => `<option${x === now ? " selected" : ""}>${esc(x)}</option>`).join("");
  view.innerHTML = `<article class="settings">
    <div class="crumbs">settings · saved in ${esc(s.file)}</div>
    <h2>Model</h2>
    <p class="lead">Which model reads your documents and writes the wiki.</p>
    ${s.editable ? "" : '<div class="notice">Settings can be changed only from this machine, at http://localhost.</div>'}
    ${s.editable && !s.ready ? '<div class="notice">Choose a provider and paste its key to start.</div>' : ""}
    <form id="settings" autocomplete="off">
      <div class="field"><label for="provider">Provider</label>
        <select id="provider" ${locked("provider") ? "disabled" : ""}>${options(Object.keys(s.providers), v.provider)}</select>
        <div class="hint" id="help"></div>${lockNote("provider")}</div>
      <div class="field" id="f-base_url"><label for="base_url">Server address</label>
        <input id="base_url" placeholder="http://localhost:8000/v1" value="${esc(v.base_url)}" ${locked("base_url") ? "disabled" : ""}>
        ${lockNote("base_url")}</div>
      <div class="field" id="f-api_key"><label for="api_key">API key</label>
        <input id="api_key" type="password" ${locked("api_key") ? "disabled" : ""}>
        <div class="hint" id="key-hint"></div>${lockNote("api_key")}</div>
      <div class="field"><label for="model">Model</label>
        <input id="model" list="models-list" value="${esc(v.model)}" ${locked("model") ? "disabled" : ""}>
        <datalist id="models-list"></datalist><div class="hint" id="models-hint"></div>${lockNote("model")}</div>
      <details class="advanced"${v.mode === "crow" ? " open" : ""}><summary>Advanced</summary>
        <div class="field"><label for="mode">Who decides where notes go</label>
          <select id="mode" ${locked("mode") ? "disabled" : ""}>
            <option value="classic"${v.mode === "classic" ? " selected" : ""}>classic — the model decides</option>
            <option value="crow"${v.mode === "crow" ? " selected" : ""}>crow — a typed classifier decides (fewer tokens)</option>
          </select>${lockNote("mode")}</div>
        <div class="field" id="f-classifier_api_key"><label for="classifier_api_key">Classifier key (OpenRouter)</label>
          <input id="classifier_api_key" type="password" placeholder="${v.classifier_api_key ? `saved: ${esc(v.classifier_api_key)} · leave empty to keep` : "your OpenRouter key"}" ${locked("classifier_api_key") ? "disabled" : ""}>
          <div class="hint">CROW mode asks the Jev classifier on OpenRouter. With OpenRouter as provider its key is used.</div>${lockNote("classifier_api_key")}</div>
        <div class="field"><label for="bundle">Wiki folder</label>
          <input id="bundle" value="${esc(v.bundle)}" ${locked("bundle") ? "disabled" : ""}>${lockNote("bundle")}</div>
      </details>
      <div class="actions">
        <button class="btn" id="save" ${s.editable ? "" : "disabled"}>Save</button>
        <button class="btn ghost" type="button" id="test" ${s.editable ? "" : "disabled"}>Test connection</button>
        <span class="status" id="status"></span>
      </div>
    </form></article>`;

  const provider = $("#provider");
  const sync = (changed) => { // show what the chosen provider needs
    const p = s.providers[provider.value], same = provider.value === v.provider;
    $("#help").innerHTML = HELP[provider.value] || "";
    $("#f-base_url").style.display = provider.value === "custom" ? "" : "none";
    $("#f-api_key").style.display = p.needs_key || provider.value === "custom" ? "" : "none";
    $("#api_key").placeholder = same && v.api_key ? `saved: ${v.api_key} · leave empty to keep` : "paste your key";
    $("#key-hint").textContent = same || !v.api_key ? "" : "A new provider needs its own key: the saved one is not sent to it.";
    if (changed && !locked("model")) $("#model").value = p.models[0] || "";
    $("#f-classifier_api_key").style.display = $("#mode").value === "crow" && provider.value !== "openrouter" ? "" : "none";
  };
  const fields = ["provider", "base_url", "api_key", "model", "mode", "classifier_api_key", "bundle"];
  const value = (f) => f === "base_url" && provider.value !== "custom" ? "" : $("#" + f).value.trim(); // presets bring their own
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

  provider.onchange = () => { sync(true); refreshModels(); };
  $("#mode").onchange = () => sync(false);
  $("#api_key").onchange = $("#base_url").onchange = refreshModels;
  sync(false);
  refreshModels();
  const status = (text, kind = "") => { const el = $("#status"); el.className = "status " + kind; el.innerHTML = text; };

  $("#test").onclick = async () => {
    status('<span class="spin"></span> asking the model…');
    try {
      const r = await post("/settings/test", changes());
      r.ok ? status(`✓ ${esc(r.model)} answered`, "ok") : status(`✗ ${esc(r.error)}`, "err");
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
