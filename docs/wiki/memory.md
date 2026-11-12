# The wiki as Claude's memory (MCP)

The wiki can be the long-term memory of Claude Code, Codex or any client of the
[Model Context Protocol](https://modelcontextprotocol.io). The memory includes
the documents already filed. The client recalls from it, and keeps in it what it
learns, through CROW:
- a typed classifier decides where each memory goes and whether it merges;
- the LLM writes it.

A memory is a note like any other. It shows up in the page, and the page can move or delete it.

## From the terminal only (Claude Code, Codex)

No page and no server to keep running: the client starts the memory by itself, over stdio, each time it starts.

```bash
uv tool install 'llmwiki2[mcp]'      # or: pip install 'llmwiki2[mcp]'
llmwiki2 setup
```

`setup` asks how you will use the wiki: pick **As the memory of an agentic CLI**, then which models do its work (see
[Which models do the work](#which-models-do-the-work)):

1. **The CLI's own models**, with no key: Claude Code or Codex do everything on their plan.
2. **Hybrid**: the CLI writes, and a small classifier (Jev through OpenRouter, or Ollama or Laya on your computer)
   decides where things go. It asks for that classifier's key or address and tests it.
3. **A model provider for everything** (OpenRouter, Ollama, …), as for the page: it asks for the provider, key and model.

Then, for each of Claude Code and Codex it finds, it offers to connect it (`claude mcp add --scope user llm-wiki --
llmwiki2 mcp`) and to add a few lines to `~/.claude/CLAUDE.md` (Codex: `~/.codex/AGENTS.md`) that tell it to use the
memory when you speak in plain words. With more than one configuration (`~/.claude` and `~/.claude-work`, used through
`CLAUDE_CONFIG_DIR`; for Codex `CODEX_HOME`), it lists them and asks which to connect: Enter for all. The memory's own instructions already say so; the lines make it surer, as the
client reads them at every session. Run `setup` again to change anything: it replaces the lines rather than adding them
twice.

By hand, the same is:

```bash
claude mcp add --scope user llm-wiki -- llmwiki2 mcp      # Claude Code, every project
codex mcp add llm-wiki -- llmwiki2 mcp                    # Codex
OKF_MCP_MODELS=client                                     # or hybrid; or a provider in ~/.config/llm-wiki/config.json
```

`llmwiki2 mcp` is always on, whatever **Start** says, and takes `--bundle` (before `mcp`) for a wiki other than
`~/llm-wiki`. Then just talk: "ricorda che il fornitore paga a 45 giorni", "what did we decide about pricing?", "add
~/Downloads/contract.pdf to the wiki", "move the note on pricing to suppliers". The client calls the tools by itself.

Over stdio the memory has two more tools, for the files of your computer: `remember_file` and `sync_folder` (below).
The page can stay off; if you open it as well (`llmwiki2`), both write the same wiki, one at a time.

## With the page: start it and connect a client

The memory comes with the `mcp` extra, which the Docker image has:

```bash
pip install 'llmwiki2[mcp]'      # or: uv tool install 'llmwiki2[mcp]'
```

Without it, `/mcp` answers 501 with what to install, and the **Memory** page says the same.

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
`~/.codex/config.toml`. Codex also asks before each `remember` and `reply`, since they write (`recall`, `search` and `read_note` are marked
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
| `recall(question, where?)` | Answers from the wiki, as **Ask** does: the classifier picks the folders and notes, and the LLM answers from them. `where` (field to value) keeps only the notes whose `fields` have all of them; it is a filter, not an access control. Read-only. | `{"answer", "notes"}`: the answer cites its notes as `[path.md]` |
| `remember(text, title?)` | Files the text like a note written on the page. It goes in the right folder, or is merged into the note on the same subject, and is linked to related notes. | `{"action", "note", "folder", "related"}`, and `deviated` (a count) with `deviations` (each note, the root it went to, `p_none`, `p_root`) when the limit on top-level folders sent the note to the likeliest existing folder instead of a new one |
| `search(query, k?, where?)` | The notes that best match a query, as `Wiki.search` finds them (BM25, and the embedder when the settings have one), with no model call and no answer written: for a client that reads the notes itself with `read_note`. `k` from 1 to 50 (8). Read-only. | a list of `{"path", "title", "summary", "rank"}` |
| `read_note(path)` | One note in full: title, summary and body. Read-only. | the note's text |
| `rebuild(confirm)` | Deletes every note, raw copy and archived version and files the raw copies again on the same folders, with no new plan and no new folder. Destructive and slow: it does nothing unless `confirm` is `true`. It runs as a job like the others when the client does the model work. | the counts (`created`, `merged`, `unchanged`, `errors`, `stopped`) and the deviations |
| `create_area(name, description)` | Opens a new top-level folder the user asked for. Refused when the wiki already has `max_top_folders` of them. The memory never opens or proposes an area on its own. | `{"folder", "description"}` |
| `propose_area(name, description, notes)` | Queues a top-level folder the user described, with the notes (paths or ids) it would hold. Nothing moves until it is confirmed. | the proposal (`id`, `label`, `description`, `notes`, `status`…) |
| `proposals()` | The open proposals, to put to the user as a yes or a no. Read-only. | a list of proposals |
| `confirm_area(id, yes)` | The user's answer: `yes` true opens the folder and moves only the proposed notes still there into it (each with `moved_from`); false dismisses the proposal. A yes is refused at the limit of top-level folders. | the proposal, and `moved` (the notes' new paths) for a yes |
| `folders()` | Every folder, with its description and how many notes it holds directly. Read-only. | a list of `{"folder", "description", "notes"}` |
| `move_note(path, folder)` | Moves a note into another folder (`/` is the top level); the links to and from it follow, as **Move** on the page. | `{"note"}`: its new path |
| `describe_folder(folder, description)` | Changes a folder's one-line description, the line new notes are routed by. | `{"folder", "description"}` |
| `rename_area(folder, name)` | Renames a top-level folder; its notes and the links to them follow. | `{"folder", "description"}` |
| `merge_areas(source, target, confirm)` | Moves every note of a top-level folder into another and deletes the first. Does nothing unless `confirm` is `true`: the client is told to merge only what you asked for, and to ask when unsure. | `{"folder", "description"}` of the target |
| `delete_note(path, confirm)` | Deletes a note; the links to it go from the other notes. Cannot be undone, so it does nothing unless `confirm` is `true`. | what went: `notes`, `sources`, `archived`, `unlinked` |
| `remember_file(path, title?)` | Over stdio only. Files a document of your computer (text, Markdown, PDF, Word, PowerPoint, Excel, OpenDocument), as dropping it on the page does. | as `remember` |
| `sync_folder(folder)` | Over stdio only. Files every document of a folder and keeps the wiki in step with it, as `llmwiki2 sync`: a later call files only what changed. | the counts and the errors, as `sync --json` |
| `reply(job, replies)` | Hands over the client's replies to the memory's requests, when the client does its model work (below). | the next requests, or the result |

The server tells the client's model how to use them:
- the user speaks in plain words, in any language, and never names the tools: the model calls them by itself;
- recall before answering about the user's work, projects, documents, decisions or preferences, or anything from before;
- remember when asked ("remember that…", "ricorda che…") and what is settled, one topic per call;
- delete or merge only what the user asked for, and ask when unsure;
- never store secrets.

`remember_file` and `sync_folder` read the disk of the machine the memory runs on, so only the stdio memory has them: it
runs as you, started by your own client. Over HTTP the server may answer other machines (a reverse proxy, Docker), which
must not read its files.

When the wiki refuses a call (an empty question, a missing note, a model that fails), the client's model reads why.

## Which models do the work

Three setups, chosen under **Memory** in **Which models do the work**, or in `llmwiki2 setup` (the setting
`mcp_models`, or `OKF_MCP_MODELS`). The choice applies from the client's next call:

- **This wiki's settings** (`wiki`, the default): the models in **Settings**. OpenRouter or a local model writes, and
  Jev or Laya decides. The client only calls the memory, and spends nothing on filing.
- **Claude or GPT for everything** (`client`): the client's own models do the memory's model work, on its plan, with no
  key in the wiki.
- **Hybrid** (`hybrid`): the client's model writes, and this wiki's classifier decides (Jev through OpenRouter or
  TypeSafe, or a System One server on your computer: Ollama 0.35 or later, or Laya). Only the writing goes to the client
  (in our tries, one or two replies per memory instead of four to seven), and no Haiku subagent runs; the writing stays
  on the client's plan.
  Without a classifier set up (its key, or its address), it works as `client`.

### How the client does it

CROW runs as it always does: the routing, the thresholds, the files. Only the model calls change. Each one is handed to
the client as a request, and its reply lets the work go on. `recall` and `remember` return the first requests and a
`job`; `reply(job, {request id: reply})` returns the next ones, until the result comes:

```json
{"job": "3f2a9c1e", "status": "needs replies", "how": "…", "requests": [
  {"id": "r1", "kind": "write", "step": "librarian/summarize", "system": "…", "prompt": "…"}]}
```

- A `decide` request is a typed decision: the System One questions, and the prompt of TypeSafe's system-one-adapter,
  which ends with the reply's shape keyed by the question ids. Claude Code runs it with a Haiku subagent; other
  clients answer it with their own model. The reply is read as a light model writes it: an answer keyed by its
  question's text, a reply cut before its last brackets, percentages, a choice given as one of its labels. Anything
  else falls back to the LLM.
- A `write` request is the wiki's own prompt, for the client's model.

While the work goes on without the client (it waits for the wiki to be free, say), the status is `waiting`, and
`reply(job, {})` waits again. A request still unanswered comes back with the next reply, in case the client lost it,
and so does the result, for 10 minutes after the work ends. A request left unanswered for 5 minutes fails the work, as
a model that does not answer would. While a memory is filed, the wiki is held for writing, so the page's own filing
waits for the client meanwhile. `log.md` names `mcp-client` as the model of the notes the client filed. The CROW
thresholds stay the wiki's own, as in **Settings**.

The lines the memory writes in `log.md` end with ` · by <name>`: the `X-Author` header of the request if there is one (a
reverse proxy that authenticates your users can set it), else the client's name from the handshake (`clientInfo.name`),
else the product in its `User-Agent`. The memory answers statelessly, so the handshake is another request and its name
is seldom there when a tool is called: expect the `User-Agent`. The name is **declared, not verified**; it is a record,
not a login.

A memory takes a few round trips: about a minute. The client's tool calls and subagents run on its own plan.

The page's own filing (files dropped on it) keeps using the wiki's own models, as no client is there to ask. The memory
files through CROW whichever models do it.

## More than one wiki

Each wiki is its own bundle with its own server, and there is no search across wikis. To have a client consult several,
run one server per wiki on different ports, with the memory on, and add each to the same client under its own name:

```bash
OKF_MCP=1 llmwiki2 --bundle ./wiki-a serve --port 8001
OKF_MCP=1 llmwiki2 --bundle ./wiki-b serve --port 8002

claude mcp add --transport http --scope user wiki-a http://127.0.0.1:8001/mcp   # Claude Code
claude mcp add --transport http --scope user wiki-b http://127.0.0.1:8002/mcp
codex mcp add wiki-a --url http://127.0.0.1:8001/mcp                            # Codex
codex mcp add wiki-b --url http://127.0.0.1:8002/mcp
```

The client's model then calls `recall` on each wiki and puts the answers together itself, so the merge is its work and
not the wiki's. Limits:
- there is no common ranking between wikis: each `recall` ranks only its own notes, and the client decides which
  answer to trust;
- there is no permission model between wikis: a client added to both can read both (and `remember` into either), so
  add a wiki only to the clients that may read it;
- `where` filters on `fields` inside one wiki; it is not an access control (see the table above).

To do the same from code, see "Asking several wikis" in the [library guide](../library/index.md#asking-several-wikis).

## Docker

Set `OKF_MCP=1` in `.env`, and `OKF_MCP_MODELS=client` for the client's models. The memory then answers at
`http://localhost:8000/mcp`, on the port that docker compose already publishes.

## Security

The memory has no authentication, like the rest of the HTTP API. It sits behind the same guard:
- a server bound to localhost (the default) answers only requests addressed to `localhost`, `127.0.0.1` or `[::1]`;
- no request may come from another site's page.

Keep the port on your machine. In Docker that means `127.0.0.1:8000:8000` if the machine is on a shared network.

With **Claude or GPT for everything**, the requests carry your documents' text to the client's model, and that model
has tools: commands, files, the web. A document with hidden instructions (an email, a web page) could try to steer it.
The memory tells the client that a request is data, never instructions, and that it answers with text alone and no
tool, its subagents included. A decision's document also travels escaped, inside a prompt that says it is untrusted.
That is an instruction, not a sandbox: Claude Code documents no way to give a subagent no tools. So with documents you did
not write, keep the client's permission prompts on (in Claude Code, not `--dangerously-skip-permissions`), or let this
wiki's own models do the work, which have no tools.

## How it works

`llmw2/memory.py` builds the server with the official MCP SDK (`MCPServer`). It serves it over Streamable HTTP, stateless
and with plain JSON replies:
- there are no sessions to keep and no stream left open;
- clients of every protocol version connect, those from before 2026-07-28 included.

`llmw2/server.py` routes `/mcp` to it, and its lifespan runs the SDK's session manager. The tools call the wiki the server
holds at that moment, so a change in **Settings** applies to the next call.
