# The wiki as Claude's memory (MCP)

The wiki can be the long-term memory of Claude Code, Codex or any client of the
[Model Context Protocol](https://modelcontextprotocol.io). The memory includes
the documents already filed. The client recalls from it, and keeps in it what it
learns, through CROW:
- a typed classifier decides where each memory goes and whether it merges;
- the LLM writes it.

A memory is a note like any other. It shows up in the page, and the page can move or delete it.

## Start it and connect a client

On the page, open **Memory** and press **Start**. Without the page, set `OKF_MCP=1` where the server runs.
The memory answers at the server's own address, `http://127.0.0.1:8000/mcp`
by default, while `llmwiki2` runs. **Stop** turns its tools off: they answer that the memory is off, and how to start it.
A client added before **Start** still connects and finds the tools, and they work from its next call after **Start**.

```bash
claude mcp add --transport http --scope user llm-wiki http://127.0.0.1:8000/mcp   # Claude Code, every project
codex mcp add llm-wiki --url http://127.0.0.1:8000/mcp                            # Codex
```

Cursor, VS Code and other clients take the same URL in their `mcp.json`:
`{"mcpServers": {"llm-wiki": {"type": "http", "url": "http://127.0.0.1:8000/mcp"}}}`.
The **Memory** page shows all three, with your own address, and a button to copy each.

Filing a memory takes 10 to 60 seconds: one call to summarize, a few classifier decisions, and one call to write or merge.
Codex gives up on a tool after 60 seconds by default. If that happens, raise `tool_timeout_sec` under `[mcp_servers.llm-wiki]` in
`~/.codex/config.toml`. Codex also asks before each `remember` and `reply`, since they write (`recall` and `read_note` are marked
read-only). To let it go on without asking, as `codex exec` must, since it can't ask, add this:

```toml
[mcp_servers.llm-wiki.tools.remember]
approval_mode = "approve"

[mcp_servers.llm-wiki.tools.reply]
approval_mode = "approve"
```

## The tools

| Tool | What it does | Returns |
|---|---|---|
| `recall(question)` | Answers from the wiki, as **Ask** does: the classifier picks the folders and notes, and the LLM answers from them. Read-only. | `{"answer", "notes"}`: the answer cites its notes as `[path.md]` |
| `remember(text, title?)` | Files the text like a note written on the page. It goes in the right folder, or is merged into the note on the same subject, and is linked to related notes. | `{"action", "note", "folder", "related"}` |
| `read_note(path)` | One note in full: title, summary and body. Read-only. | the note's text |
| `reply(job, replies)` | Hands over the client's replies to the memory's requests, when the client does its model work (below). | the next requests, or the result |

The server tells the client's model how to use them:
- recall before answering about the user's work, projects, documents, decisions or preferences;
- remember what is settled, one topic per call;
- never store secrets.

When the wiki refuses a call (an empty question, a missing note, a model that fails), the client's model reads why.

## Which models do the work

Two setups, chosen under **Memory** in **Which models do the work** (the setting `mcp_models`, or `OKF_MCP_MODELS`).
The choice applies from the client's next call:

- **This wiki's settings** (`wiki`, the default): the models in **Settings**. OpenRouter or a local model writes, and
  Jev or Laya decides. The client only calls the memory, and spends nothing on filing.
- **Claude or GPT for everything** (`client`): the client's own models do the memory's model work, on its plan, with no
  key in the wiki.

### How the client does it

CROW runs as it always does: the routing, the thresholds, the files. Only the model calls change. Each one is handed to
the client as a request, and its reply lets the work go on. `recall` and `remember` return the first requests and a
`job`; `reply(job, {request id: reply})` returns the next ones, until the result comes:

```json
{"job": "3f2a9c1e", "status": "needs replies", "how": "…", "requests": [
  {"id": "r1", "kind": "write", "step": "librarian/summarize", "system": "…", "prompt": "…"}]}
```

- A `decide` request is a typed decision: the System One questions, and the prompt of TypeSafe's system-one-adapter.
  Claude Code runs it with a Haiku subagent; other clients answer it with their own model.
- A `write` request is the wiki's own prompt, for the client's model.

While the work goes on without the client (it waits for the wiki to be free, say), the status is `waiting`, and
`reply(job, {})` waits again. A request still unanswered comes back with the next reply, in case the client lost it,
and so does the result, for 10 minutes after the work ends. A request left unanswered for 5 minutes fails the work, as
a model that does not answer would. While a memory is filed, the wiki is held for writing, so the page's own filing
waits for the client meanwhile. `log.md` names `mcp-client` as the model of the notes the client filed. The CROW
thresholds stay the wiki's own, as in **Settings**.

A memory takes a few round trips, from a minute or so. The client's tool calls and subagents run on its own plan.
Measured on 2026-10-26:
- Claude Code (Sonnet, with Haiku subagents deciding) filed a memory and recalled it in 45 s.
- Codex filed and recalled one in 57 s.

The page's own filing (files dropped on it) keeps using the wiki's own models, as no client is there to ask. The memory
files through CROW whichever models do it.

## Docker

Set `OKF_MCP=1` in `.env`, and `OKF_MCP_MODELS=client` for the client's models. The memory then answers at
`http://localhost:8000/mcp`, on the port that docker compose already publishes.

## Security

The memory has no authentication, like the rest of the HTTP API. It sits behind the same guard:
- a server bound to localhost (the default) answers only requests addressed to `localhost`, `127.0.0.1` or `[::1]`;
- no request may come from another site's page.

Keep the port on your machine. In Docker that means `127.0.0.1:8000:8000` if the machine is on a shared network.

## How it works

`llmw2/memory.py` builds the server with the official MCP SDK (`MCPServer`). It serves it over Streamable HTTP, stateless
and with plain JSON replies:
- there are no sessions to keep and no stream left open;
- clients of every protocol version connect, those from before 2026-07-28 included.

`llmw2/server.py` routes `/mcp` to it, and its lifespan runs the SDK's session manager. The tools call the wiki the server
holds at that moment, so a change in **Settings** applies to the next call.
