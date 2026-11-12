// Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later
// Memory: the wiki as long-term memory of Claude Code, Codex or any MCP client. Start or stop it, choose its models, connect a client.

import { $, api, esc, post } from "./util.js";

// `onSaved` runs after a change, with the new settings view; `said` maps a status line's id to what it shows.
export async function showMemory(onSaved, said = {}) {
  const view = $("#view");
  view.className = "";
  const s = await api("/settings");
  const v = s.values, on = Boolean(v.mcp), who0 = ["client", "hybrid"].includes(v.mcp_models) ? v.mcp_models : "wiki", byClient = who0 === "client", installed = s.mcp_installed !== false;
  const lockedOn = !installed || !s.editable || s.managed.includes("mcp");
  const lockedWho = !installed || !s.editable || s.managed.includes("mcp_models");
  const url = `${location.origin}/mcp`;
  const clients = [
    ["Claude Code", `claude mcp add --transport http --scope user llm-wiki ${url}`],
    ["Codex", `codex mcp add llm-wiki --url ${url}`],
    ["Claude Code from the terminal only, with no page to keep open: it starts the memory by itself", "claude mcp add --scope user llm-wiki -- llmwiki2 mcp"],
    ["Cursor, VS Code and other clients (mcp.json)", JSON.stringify({ mcpServers: { "llm-wiki": { type: "http", url } } }, null, 2)],
  ];
  const own = v.mode === "crow" ? `${v.model} writes, ${v.classifier_model} decides` : `${v.model} writes and decides`;
  const work = who0 === "hybrid" // what the chosen models do
    ? `Your client's model writes and ${esc(v.classifier_model)} decides where things go: only the writing comes back to the client, a few calls per memory instead of a dozen. Without a classifier set up it works as Claude or GPT for everything.`
    : byClient
    ? "The memory hands each model call to your client, and CROW does the rest: Claude Code decides with Haiku subagents and writes with its own model; Codex does both with its own. It runs on the client's plan, and a memory takes about a minute."
    : "The client only calls the memory: the models in Settings recall and file, as for the documents you drop on this page. A memory takes 10–30 s.";
  view.innerHTML = `<article class="settings">
    <div class="crumbs">MEMORY · MCP</div>
    <h2>Claude's <em>memory.</em></h2>
    <p class="lead">Your wiki as long-term memory for Claude Code, Codex or any MCP client: they recall from your notes and documents, and keep what they learn in it.</p>
    ${installed ? "" : `<div class="notice">The memory needs the mcp extra: <code>pip install 'llmwiki2[mcp]'</code> (or <code>uv tool install 'llmwiki2[mcp]'</code>), then start llmwiki2 again.</div>`}
    ${s.editable ? "" : '<div class="notice">The memory can be changed only from this machine, at http://localhost, or with OKF_MCP and OKF_MCP_MODELS where the server runs.</div>'}
    <div class="field"><label>Status</label>
      <div class="actions">
        <button class="btn${on ? " ghost" : ""}" type="button" id="toggle" ${lockedOn ? "disabled" : ""}>${on ? "Stop" : "Start →"}</button>
        <span class="status${on ? " ok" : ""}" id="mcp-status">${on ? `● On at ${esc(url)}` : "Off: the tools answer that the memory is off"}</span>
      </div>
      <div class="hint">${s.managed.includes("mcp") ? "Set by the environment (OKF_MCP)." : "It answers while llmwiki2 runs, at the address of this page."}</div></div>
    <div class="field"><label for="who">Which models do the work</label>
      <select id="who" ${lockedWho ? "disabled" : ""}>
        <option value="wiki"${byClient ? "" : " selected"}>This wiki's settings — ${esc(own)}</option>
        <option value="client"${byClient ? " selected" : ""}>Claude or GPT for everything — your MCP client's own models, no key</option>
        <option value="hybrid"${who0 === "hybrid" ? " selected" : ""}>Hybrid — your client writes, ${esc(v.classifier_model)} decides</option>
      </select>
      <div class="hint">${work}${s.managed.includes("mcp_models") ? " Set by the environment (OKF_MCP_MODELS)." : ""}</div>
      <div class="actions"><span class="status" id="who-status"></span></div></div>
    <div class="field"><label>Connect a client</label>
      ${clients.map(([name, cmd], i) => `<div class="hint">${esc(name)}</div><pre class="cmd"><code>${esc(cmd)}</code><button class="mini" type="button" data-copy="${i}">Copy</button></pre>`).join("")}
      <div class="hint">Filing a memory takes 10–60 s. If Codex gives up first, raise <code>tool_timeout_sec</code> under <code>[mcp_servers.llm-wiki]</code> in ~/.codex/config.toml.
        Codex asks before each <code>remember</code> and <code>reply</code>, which write; to let it go on without asking (<code>codex exec</code> cannot ask), add <code>approval_mode = "approve"</code> under <code>[mcp_servers.llm-wiki.tools.remember]</code> and <code>[mcp_servers.llm-wiki.tools.reply]</code>.</div></div>
  </article>`;

  const status = (id, text, kind = "") => { const el = $("#" + id); el.className = "status " + kind; el.innerHTML = text; };
  const change = async (id, changes, done, undo = () => {}) => { // save, rebuild the page, or put it back and say what went wrong
    try {
      const saved = await post("/settings/memory", changes);
      await onSaved(saved);
      await showMemory(onSaved, { [id]: done });
    } catch (e) { undo(); status(id, `✗ ${esc(e.message)}`, "err"); }
  };
  for (const [id, text] of Object.entries(said)) status(id, `✓ ${esc(text)}`, "ok");

  $("#toggle").onclick = () => change("mcp-status", { mcp: on ? "" : "true" }, on ? "Stopped" : `Started: clients can connect at ${url}`);
  const who = $("#who");
  const whoSaid = { wiki: "This wiki's settings do the work", client: "Claude or GPT for everything: your MCP client does the work",
    hybrid: "Hybrid: your client writes, the classifier decides" };
  who.onchange = () => change("who-status", { mcp_models: who.value === "wiki" ? "" : who.value }, whoSaid[who.value], () => (who.value = who0));
  view.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => {
    await navigator.clipboard.writeText(clients[Number(b.dataset.copy)][1]);
    b.textContent = "Copied";
    setTimeout(() => (b.textContent = "Copy"), 1500);
  }));
}
