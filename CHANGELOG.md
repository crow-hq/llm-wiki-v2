# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Upgrading from 1.x

- Before opening an upgraded wiki to many users, run `llmwiki2 index` (or `Wiki.build_index()`): the search index changed format, so
  without it the first search builds it in the background and, on a large wiki, the searches made meanwhile wait up to 4.75 seconds
  and then answer partial. The step returns, with exit code 0, once the index is whole; from then on no search is partial. The same
  holds for a fresh copy of a wiki without `.llmw2/cache`.

### Added

- `llmwiki2 index` and `Wiki.build_index()`: build the search index and return once it is whole,
  however long that takes. It is the build in the background, not a second one: it starts it when no process of the machine builds
  the file, waits on the lease of the one that does, and goes on from the lots of one that stopped. The searches of the process go on
  meanwhile; with the index already whole it returns at once. When the index file cannot be written (a folder that cannot be
  written, a full disk), it raises `OSError` and the command exits with 2: an index kept in memory is not reported as built. Not in
  the HTTP API or the MCP tools: it is a step for whoever runs
  the wiki, before its users come, and a call that may last minutes does not fit a request or a tool call (`status` says how far a
  build got).
- `crow.fallback_reply_tokens` (`OKF_FALLBACK_REPLY_TOKENS`, or `crow_fallback_reply_tokens` in the settings): the output cap,
  in tokens, of the reply of CROW's routing fallback, the call where the writer picks a folder when the classifier is unsure.
  Unset by default, which keeps the cap of a decision and the same requests as before for every provider. A small local
  writer that rambles in the reply's `reasoning` stops sooner with a cap such as 400, under the floor of 1024 that every other
  call keeps. A reply cut at it is asked once more, shorter; cut again, on Ollama the classifier's best path decides, while
  with any other provider the ingest stops, so leave it unset with a hosted writer. `StepContext.ask_json` takes `max_tokens`,
  the output cap itself, in place of `reply_chars`; giving both raises `ValueError`.

### Changed

- With the client's models (`mcp_models` "client" or "hybrid", what `llmwiki2 setup` picks for Claude Code and Codex), the MCP
  `remember` answers in milliseconds, as with the wiki's own models: the text goes to the journal, with the same receipt (`saved`,
  `id`, `key`, `state`) and the same refusal when the queue is full, and `search`, `recall` and `read_note` find it at once as
  `pending:<id>`. It filed the text before it returned, and the client's model answered four to seven requests first, about a
  minute. The receipt now also holds the server's filing `job`, with `"status": "to file"` and a `how` that says to file at a
  natural pause, not now. A `reply` to that job starts a run while the queue is not empty, which files the whole journal with the
  client's models, earlier sessions' memories included. `status` gives the job while the queue is not empty, after a restart
  too, and so does a refusal for a full queue. Until the client replies, nothing waits on it; once a run goes on, the page and
  other processes wait for the client on the section it files, and a `forget` or a `delete_note` stops the run rather than wait
  for the client. A reply that is not what was asked (one that cannot be read, or a write that lacks what its step needs) costs
  the memory a retry, not one of its three attempts, for its first six such replies in a server. Incompatible for a caller that
  reads the result from code: the result of `remember` is the receipt (`note` empty until filed), and its job ends with
  `{"status": "done", "queue", "failed", "warnings"}` in place of the note. The other jobs of the client's models (`recall`,
  `forget`, `rebuild`, `remember_file`, `sync_folder`) are unchanged, and their wiki is closed when they end, which completes the
  journal's part of a `forget`.
- With the client's models, the client answers the `decide` requests itself, as the `write` ones, with no tool and no subagent.
  The `how` asked Claude Code to hand each one to a Haiku subagent, and also to use no tool, and Claude Code mostly answered them
  itself; when it did start the subagents, a filing took longer and cost more, since a subagent's own context outweighs a short
  decision.

### Fixed

- Threads that make the first `remember`, `status` or `drain` of a new `Wiki` at the same time take one reference to the journal
  of the folder, not one each. Before, `close` gave back only one: the journal stayed open in the process, and a new `Wiki` on the
  same folder got it back, so after a restore of the folder in the same process a `remember` returned its receipt and the memory
  was never filed.
- While the search index is being built, a memory filed by another process before this one looked at the journal is found:
  `search`, `ask` and `read_note` show it as `pending:<id>` until the index has its note, as they show one still queued. It was
  found by neither: the entry was closed and its text dropped, and the index being built did not hold its note yet. A memory whose
  note was deleted (`delete_note`, `delete_folder`, `clear`) or forgotten (`purge`) is never shown again.

## [2.0.0rc1] - 2026-11-23

### Upgrading from 1.x

- From 1.x there is nothing to do: this version opens a 1.x wiki as it is, and makes its journal the first time `remember` (or a purge)
  needs it. A major version, because the wiki's folder changes in a way 1.x does not know: from then on `.llmw2/format` says `2`.
- To open the same wiki with a 1.x again (going back, or two versions on one wiki), first let the queue of `remember` empty: `status`
  with `queue` and `failed` at 0 (it says so in its warnings while entries wait). Otherwise a 1.x does not know the journal: its
  `purge` does not take out the entries still queued, which this version files when it opens the wiki again (a memory forgotten comes
  back), and its `delete_note` leaves their text in the journal's segments. A note a 1.x deletes stays in this version's search index
  until its next sync (a look at the files that changed, at the next search). Locks are shared, so a 1.x and this version on one wiki
  at the same time still exclude each other; the rest of what 1.x does not know (the undo log, the trash) it leaves alone, and the
  restore of an interrupted section here compares the files with what it saved and leaves alone what a 1.x changed meanwhile. Going
  back also leaves one error in the `check` of the 1.x, on the form of the log alone: this version writes `log.md` oldest first, and
  a 1.x adds its new day at the top, so its rule "log days are not newest first" fails (nothing is lost; this version reads that log
  with warnings: days out of order, one day under two headings).
- `.llmw2/format` holds the format of the wiki's folder, one integer, written when the journal is born. A version opens a folder whose
  format is at most its own (no file: format 1, a 1.x folder) and refuses one that says a newer format, or whose file holds no format
  number (`BundleFormatError`, nothing changed): from this version on, an older release never writes over a newer wiki. A read of the
  file that another program holds (an antivirus, an indexer) is tried again for a moment, then its system error is raised as it is.
  The format is looked at when a `Wiki` opens: a process already open does not see a format written after it. 1.x does not read the
  file, so for it the step above stays.
- MCP clients: while the search index is being built (once per wiki: its first search by this version, or after the index file
  is lost), the `search` tool adds after its hits one more text that is not a hit ("search index being built (N of M notes): the
  results may be partial") and a `partial` field beside `result` in its structured content. A client that reads every text of the
  result as a hit in JSON skips that one, or reads the structured content.
- The entries of the journal carry one more field, `boot` (the machine's boot and the seconds since it began, see `oldest_s` below):
  an addition, which this version reads when it is there and does without when it is not (an entry written before it); the format
  of the folder stays `2`.

### Added

- Every write section of the bundle is a transaction (`llmw2.bundle.txn`): the image of each file is saved in an undo log, made durable,
  before the file changes; when the section ends the files are flushed, a commit record is made durable and the log is emptied. A
  section that raises, or a process that dies in it, leaves the wiki as it was before the section; the next one to open the wiki puts it
  back. What somebody else wrote after the crash (an older version of the library, a hand edit, git) is not overwritten, except in the
  paths a filing declares in one record (a new file, a file appended to, such as `log.md`): there the undo removes the file or cuts to the
  length it had, so lines an older version of the library appended after the crash, before this one's first lock, are lost with the
  section's own. In every other path it is listed by `check`. `.llmw2/journal/` has its own `.gitignore`. A section that moves or deletes whole folders (`rename_root`, `merge_roots`,
  `delete_folder`, `clear`, a batch's tidy) is not covered and writes as it did: a process that dies in one leaves it half done, as in
  the release before, and `check` lists what it finds. Each document filed now costs a few more flushes of the
  disk (about +45 ms on NTFS in the library's own timing).
- A note's delete declares, before its first write, the images of every file it rewrites or deletes and the length of `log.md` in one
  record of the undo log (as a filing declares what it makes): one flush for them all instead of one for each, the same files and
  folders durable before the commit. What somebody else wrote after a crash is still left alone and listed by `check`.
- `Wiki.remember(text, *, title, key, fields, author, at, timeout) -> Receipt`: the text is kept in the journal the moment it is asked, and a
  thread of the process (the filer, started by the first `remember`, `drain` or `_start_filer`; `Wiki()` starts none) files it with the
  same steps as `ingest`, with the models of the `Wiki` of the process that asked last (each `remember` tells the filer before its
  entry is written, so an idle filer started by another `Wiki` on the same folder does not file that entry with the other one's models). The `Receipt` (`id`, `key`, `at`, `state`, `note`, `wait(timeout)`) is read from the journal at each access;
  `status()` is a dict with a fixed set of keys and no `check()`; `drain(timeout)` returns when what was written before the call is done
  (`TimeoutError`, `FilerError`); `close()` stops the filer: it waits for the section the filer is in (with real models, the routing
  and the writing of a note happen there), not for an entry it is still preparing (the summary, before the wiki's lock): nothing of
  that one is written, not even when its model call ends after the close, and it stays first for the next holder of the lease, which
  files it once; the filer's thread ends with that call, holding the lease until then. So in a process that lives on after the close
  (a wiki opened again, a server that swaps its wiki) the queue does not move until that call ends or times out, and a summary that
  comes back after the close is thrown away: its cost is spent for nothing. `QueueFull` and `DiskFull` refuse an entry before anything is written. The key
  is the one given, or `text:<sha256 of the text>` (so a retry of the same text is the same memory, and a retry after a crash finds the text
  already filed: this is the one difference from `ingest`, which does not deduplicate a text with no origin). `at` (a datetime with a
  time zone) is kept as `event_at`, a top-level key of the raw copy's front matter, in UTC with microseconds; it is outside the hash, a
  raw copy without it is byte for byte what it was, and the release before reads and checks a copy that has it. The same text with another `at` is
  "unchanged": no model call, the new time in the journal and in a "Seen again" log line. A process that dies after a section committed and
  before the journal was told is recovered by the next one: the commit carries the entry's id and what was done, and recovery writes the
  `F` record before the undo log is emptied.
- `llmw2.bundle.filelock.FileLock`: an exclusive lock on a file across threads and processes that waits in the kernel, with a timeout.
- `llmw2.bundle.journal.Journal`: the journal where `remember` keeps an entry the moment it is asked. Segments of 16 MiB in
  `.llmw2/journal/`, filled with zeros that were written, so that an append is one write inside blocks that exist and the ack,
  `fdatasync` (`os.fsync` on Windows) of the segment, is one device block; a header with a version, the number of the segment, a new
  salt at each reuse and the id of the node; a CRC for each record over the salt, the order number and the content (a record of an
  earlier use of the segment is never read as new); reading stops at the first record that is not valid, and a torn tail after a crash
  is told from a fault in the middle (a record not valid before a flush limit that a valid record after it declares), which `check`
  reports and nothing is appended over. Processes share the flush (group commit across processes). A record is never rewritten: a
  segment is reused only from the oldest, after the entries it holds that are not done (open, or given up) were copied forward with
  their state in one record each, and its used bytes are written over with zeros first. The reuse reads the journal once for the
  lot, and again before a copy only when another writer moved the shared tail or the last copy did not start where the read or the
  copy before it ended; a copy that a tombstone written meanwhile made pointless (by a writer that does not share the tail hint too:
  another Windows session, another `/dev/shm`) is named on that tombstone (an `F` record with the ids it forgot) before the segment
  of the original is zeroed, by whichever process reuses it (the journal says which copies came after their tombstone, so a crash
  before the record loses nothing), and no reader takes it up; a tombstone copied forward before its purge completed keeps the segment of the original
  (`seg`), so that a reader of the copy alone does not forget an entry of the same key remembered after it. The release before this
  one leaves both alone with the rest of the journal. An entry and the records of its state are admitted only while the entries not
  done fit in half a segment (`QueueFull`, `FailedBacklog` for entries given up; `InputError` for one entry larger than that, which
  goes in as a file). A journal of a newer format is read and written by no older version of the library (`JournalVersionError`).
- On macOS the journal's acknowledgement is `F_FULLFSYNC` (plain `fsync` there hands the data to the drive, which may keep it in
  its cache), so an acknowledged entry survives a power loss there too; the same for a segment that is made, zeroed or numbered,
  and falling back to `fsync` on a file system that does not know it. The records of the undo log go through the same flush, so on
  macOS they are `F_FULLFSYNC` too. Linux and Windows flush as before. The latency of the acknowledgement on macOS is not measured.
- `Wiki.log(limit)`: the last lines of `log.md`, newest first.
- `Wiki.purge` forgets what `remember` kept under the key too: the entries of the journal with that key (queued, filed or given up:
  a purge is also the call that frees the place of an entry given up) close for good, and the receipt says `forgotten`. The key of
  a `remember` is `Receipt.key`: the key it was given (its raw copy's origin, "remember::<key>", is taken as well) or, without one,
  "text:<sha256 of the text>", which now also takes out the raw copies of that text saved with no origin. Before anything is deleted
  a tombstone is made durable in the journal, with the hash of the key and never the key; after the files, the undo log is zeroed
  whole (`txn.wipe`), and the vectors of texts no longer in the wiki go in the same purge (their pages zeroed). The journal's part,
  the seal of the segment in use, the reuse of every segment before the next one (zeroed, what is not done in it copied forward
  first) and the `O` record that completes the tombstone, is never done in the call: the filer of the process that holds the
  journal's lease does it at its next look, and a process with no filer (a script, the command line) does it when its `Wiki`
  closes, if the lease is free. So the call does not pay the seal, nor the segment made in its place (16 MiB of zeros on Windows,
  the first time), and the text stays in the journal until then (a moment with a filer running; until the close without one; with a
  process that dies first, until the next filer). `Purged.entries` counts the entries forgotten and `Purged.pending` names what
  still holds some of it: always `journal`, and `index` when a reader of another process holds the WAL of the search index for
  the whole `FORGET_WAIT` (30 s, one wait in all). The filer completes at a later look what it could not (its delay doubles, as
  for an entry that failed), a purge cut by a crash is taken up the same way, from the hash (the raw copies still there say which
  key it was; only when the tombstone does not say its files were done, so an ordinary purge pays no scan of the raw copies), and
  a purge of a key whose files are done already does nothing more and returns None. `llmwiki2 purge` completes the journal
  before it prints (a one-off call), prints the entries forgotten and exits with 3 when something is still pending. After a purge
  whose journal is completed, the text of the document is in no file of the wiki's folder; what a disk keeps of blocks written before is out of reach of a file and is not promised. The first purge of a wiki that
  never used `remember` makes its journal (three segments, 48 MiB of disk, once: space, not wear that comes again). The release
  before this one does not know the journal: it leaves `.llmw2/journal/` alone, and its `purge` does not forget the entries queued
  there, which this version files later (see "Upgrading from 1.x"); a tombstone left open is completed by the next purge or filer of
  this version.

- `Problem.severity` (`"error"` or `"warning"`). `check` now also reports, as errors, a raw copy that no note (nor archived version of one) cites and a
  temporary file an interrupted write left anywhere in the bundle, and, as warnings, a See-also entry the note it points to does not have back and a
  note that no line of `log.md` names. A wiki written before may have such warnings (a wiki made by hand or by an older version): `llmwiki2 check`
  reports them and exits 0. The one-sided See-also entries are said one by one up to 10, then in one warning that counts the others
  (`check.SEE_ALSO_SHOWN`): a 1.x wiki of 50,000 notes, upgraded, has tens of thousands of them, harmless links that go one way.

- `search` finds what `remember` keeps before it is filed, in every process on the wiki: an entry of the journal whose note the index
  does not have yet is a hit of path `pending:<id>`, titled by its title or its first line, with the start of its text for summary,
  scored by its words as a note of the index would be (the notes keep their scores; at the same score the note comes first, then the
  entries in the order they were acknowledged, the same in every process), and `where` holds for its `fields`. It goes once the index
  has its note, or when a purge forgets its key. `read_note` reads it (MCP, the HTTP API and `Wiki.read_note`); `move_note` and
  `delete_note` refuse it (it is forgotten with its key). Nothing of it is written in the index. In CROW mode `ask` and `recall` read
  it too, as the search finds it and as long as a note may be, and may cite it: `Answer.citations` and the `notes` of `recall` can
  hold `pending:<id>`, which `read_note` reads whole (once the entry is filed, it gives the note it went in, from the journal; for an
  entry forgotten or not known, the same "no note" as a path with none); in classic mode the answer navigates the folders and does
  not see it. A search in a wiki with no journal makes none. A search reads the journal when the tail hint the processes of the
  machine share says it moved, else at most once a moment (as it lists the files): an entry of another process is found at the next
  search, or after that moment when the hint cannot say. Limits: between the moment a filing puts a note in the index and the moment
  the searching process reads that it did, the entry and its note may both be hits; when the processes do not share the tail hint, a
  search in another process may still show an entry for up to that moment (`LISTING_LOOK`, about a second) after a purge forgot it,
  and no more after it (no byte of the entry stays on disk); `pending:<id>` of an entry filed leads to the note at the path its
  filing gave, the one there now, also when that note was deleted and another one took its path since.
- The memory has two more tools: `status`, what `remember` kept that is still waiting to be filed (the dict of `Wiki.status`, read-only),
  and `forget(key | id, confirm)`, which takes out for good a memory `remember` kept, by its key or by the id of its receipt (also as
  `pending:<id>`), queued or filed, every version of it, with the notes written from it, as `Wiki.purge` does; it refuses without
  `confirm=true`, with neither or both, for an id the journal no longer holds (forget it by the key) and for a key nothing was kept
  under, and returns what `purge` reports, `pending` included. Limit: an id is known while the journal holds its entry, that is
  while it is queued, and once filed until its segment is reused (a purge reuses them; the process that remembered it knows it
  longer); the key always works. The same holds for `read_note("pending:<id>")` once the entry is filed: later, the note is read by
  its path. `pending` is about that key: the same text kept under another key stays. `remember` takes a `key`, which names the memory
  (the same key with a new text is a new version of it). `Wiki.receipt(id)` gives the receipt of an entry back by its id. With the
  client's models `forget` runs as a job like the other tools, the `key` of `remember` is the origin of the text, and its result has
  the `key` it was filed under (the one given, or `text:<sha256 of the text>`), so that a memory given no key can be forgotten too.
- `status` (`Wiki.status` and the memory's tool) names, in `warnings`, the notes of the filings the process has read that the limit on
  top-level folders sent to an existing root: with the wiki's models the memory's `remember` answers before the filing, and this is
  where its deviation is told.

### Changed

- With the wiki's own models, the memory's `remember` keeps the text in the journal and answers as soon as it is on the disk, before any
  model is called, as `Wiki.remember` does: it no longer waits for the wiki to be free nor for the filing. The result keeps the keys of
  the release before and their types, with the values of "nothing filed yet" (`action: "queued"`, `note: ""`, `folder: ""`, `related:
  []`), and adds the receipt's `id`, `key` and `state`: an addition, which a client of the release before reads as it did. The same text again (or the same
  key and text) returns the first one's receipt; once it is filed, with `action: "unchanged"` and the note and folder it went in, as the
  release before said for a text the wiki held. `search` finds the memory at once, `recall` reads it and `read_note` reads
  `pending:<id>`, then the note it went in; its deviation, when the limit on top-level folders sends its note elsewhere, is in the note
  and in `log.md`, no longer in the result but in the `warnings` of `status` (`remember_file`, `sync_folder` and `rebuild`, filed before
  they return, still report theirs). A filing that fails is retried, then given up, in the background: `status` says so. When the
  server ends (the client closes `llmwiki2 mcp`, or `llmwiki2 serve` stops) the server waits up to `CLOSE_DRAIN` seconds (1.5) for what
  its `remember` queued to be filed, unless another process holds the lease and files it. That bounds the wait, not the exit: the filer
  then ends the entry it is on (with real models it may take longer), and a stdio client such as the SDK's ends the server 2 s after it
  closed it. What is left, or cut, stays in the journal for the next process on the wiki: a server killed after it answered loses
  nothing. With the client's models (`mcp_models` "client" or "hybrid") `remember` still files before it returns, as a job.
- `delete_note`, `delete_folder` and `clear` take the text of the entries `remember` kept out of the journal too, when a raw copy they
  delete may hold one (an origin of `remember`, or none, as a text given with no origin) and the journal has entries done: the journal is
  sealed and the segments before are reused, as a purge completes (what is not filed yet is carried forward), so no file of the wiki's
  folder keeps the text, as with the release before, where the memory's `remember` did not go through the journal. Raw copies never
  move, so a note moved or split since its filing is found all the same. The tombstone that asks for it is made durable inside the
  section that deletes: at its end, from what it deleted, for `delete_note` (a process that dies before the commit has it undone with
  the section), before it deletes for `delete_folder` and `clear`, which have no undo log; a process that dies once that section
  committed leaves it to the next one,
  which seals and reuses the journal (one that dies before costs a seal, never a text kept). It costs a flush of the journal inside
  the wiki's lock at each delete of a note filed from the journal: milliseconds on an SSD, more on a slow disk. The seal and the
  reuse are never done in the call: the result says so in a new key, `pending: ["journal"]`, as `Purged.pending` does (the
  memory's `delete_note` returns it too); the filer of the process that holds the lease does them, and a process with no filer when
  its `Wiki` closes. The text stays in the journal up to about a second after the answer with a filer running (`FORGET_GROUP`
  below), until the close without one. Effects: after such a
  delete the ids of the entries already filed are gone with their segments, so `read_note("pending:<id>")` and `forget(id)` no longer
  find them (`forget` by key always does); each seal takes a new segment (16 MiB of zeros really written, on every system),
  and a reuse zeroes the old ones: count it with the purges in the wear of the disk. The deletes of a run share a seal: the filer lets
  a delete's tombstone wait `filer.FORGET_GROUP` (1 s) and completes with it every other delete's tombstone then open, and it
  completes them with the journal alone (their section is done: no zeroing of the undo log, no look at the index), so the deletes of
  other processes do not queue behind it for the wiki's lock and the index; the text stays on the journal's disk up to that second
  longer. Limit, as before: the index is emptied of the note by the delete itself (the hook of its section; for `delete_folder` and
  `clear`, the sync after the lock); if that fails, the text stays in the index until its next sync, and no `pending: ["index"]` says
  so (a purge does). While the journal is sealed, a `remember` may wait for its admission up to its timeout. A note filed from a
  document with another origin costs the journal nothing.
- The memory's `delete_note` of `pending:<id>` (a memory not filed yet, as `search` shows it) forgets it as `forget` by that id does, for
  the clients of the release before that search and delete the first hit; it refused before. The result has the keys of a delete
  (`notes`, `folders`, `sources`, `archived`, `unlinked`, `pending`), those of `forget` (`entries`, ...), and `forgot`: the
  `pending:<id>` forgotten. Like `forget`, it takes out every version kept under the memory's key.
- The memory's `remember` waits for room in a full queue for 1 s (`REMEMBER_WAIT` of `llmw2.memory`), no longer up to 50 s (`WAIT`):
  a tool call that hangs looks like a stuck assistant, and an agent expects a tool to answer within a second. Then it fails as an error
  of the tool (`isError: true`), so that a client that judges the outcome by it never counts it as done. Its text is one line that
  begins with "NOT saved" and tells the assistant what to do: tell the user now that it is not saved yet and when to try again, and call
  `remember` at most once more in the turn (the description says the same). Its structured content has the keys of the release before
  (`action: "not_saved"`, `note: ""`, `folder: ""`, `related: []`) with `saved: false`, `retry_after_s`, `queue`, `queue_limit`,
  `oldest_s` and that `message`; nothing is written. A result that kept the text says `saved: true` ("filed", in its note, is the
  `state` that comes after). `forget` never waits for the queue. The memory's `status` names no call of the library in its warnings:
  where `Wiki.status` says that no filer runs and `drain()` starts one, it says that filing resumes automatically and nothing needs
  doing.
- The filer of `remember` prepares ahead: while it files an entry, the model calls the next ones need before the wiki's lock (the
  extraction, the summary) run, up to `concurrency` at once with it (1: one at a time, as before), each in a thread of its own; the
  sections stay one at a time, in the journal's order, whoever wrote the entries. A close does not wait for those calls and nothing of
  them is written (the entries stay first for the next holder of the lease); an entry forgotten while it is prepared is not filed; an
  entry that failed is not prepared ahead. What is kept meanwhile: the text and the summary of up to `2 × concurrency` entries.
  Measured with real models (a GLM writer, the Jev classifier), 8 MCP sessions that remember 40 entries together: the release before,
  which filed in each session's process, filed them all in 96 to 121 s (19.9 to 25.0 entries a minute); this version with one filer
  and no preparation ahead in 425 to 459 s (5.2 to 5.6); with it and `concurrency` 4 in 181 to 195 s (12.3 to 13.3); with 8 in 113 s
  (21.2), with no error and no refusal of the provider. Hence the default of `concurrency` is now 8, where it was 4: a batch and the
  server prepare 8 documents at once too. With local models, which take the calls one at a time anyway, it can stay at 4 in the
  settings. What still bounds the filing is its sections, one at a time: the classifier's calls inside them (routing, `match`,
  `relate`, the fallback of routing, a merge, a split) took 65 to 106 s of the 40 entries in that measure.
- The filer keeps its slots busy and asks the route of the next entry while it files one. A preparation starts as soon as a slot frees,
  in the journal's order, the entry being filed first, also while the filer waits for a slow summary at the head of the queue; up to
  `2 × concurrency` entries are kept prepared. While an entry's section runs, the route of the entry after it (the classifier's
  routing, its LLM fallback, `match`, the consolidation) is asked in a thread of its own on the tree that section read; the next
  section takes an answer only for a request it makes the same, byte for byte, each answer once, and asks again what differs (the
  section before opened a folder, or added a note the match reads). So every decision is the one the filer makes without it; what it
  costs is the requests that turn out different. In CROW only, with `concurrency` above 1. Measured with real models (a GLM writer,
  the Jev classifier, 8 MCP sessions, 40 entries), three times each, alternated with the release before in the same window: all
  filed in a median of 89 s against 108 s for the release before (0.82 times) and 110 s without this step, 1.21 times the entries
  per minute of the release before; a slow merge inside a section still holds the filing up.
- With a hosted writer at its own address (OpenRouter, OpenAI, Gemini) the parts of a source too long for one call are summarized
  at once instead of one after the other (same prompts; the notes reach the call that writes the note in the order of the parts).
  They take no place of their own: each document being prepared holds one place of `concurrency` (a set shared by `ingest`, the
  server and the filer with what it prepares ahead; a batch has a set of its own), and a part beside the document's own call
  borrows a free place of the same set, without waiting, for that call only; with none free the parts go in turn. So the parts
  never add a call beyond the places of that set; what stays outside it is as before: the calls of a section, and a batch running
  beside the filer. A writer on Ollama, a custom server or a provider's name at another address, which may run one call at a time
  and queue the others without a byte, asks the parts in turn as before: queued behind its siblings, a part could wait past the
  client's `timeout` and be sent again. A `Librarian` used on its own asks the parts in turn. A failed part stops the parts not
  started, and the error raised is the one of the first failed part in the order of the parts (not the first to fail in time): the
  error asking them in turn would raise.
- The filer of `remember` files consecutive entries in one session of the store, up to the 50 documents of a plan's batch: the tree is
  listed whole once for the lot, then only the folders that changed, instead of twice an entry; the session ends when the queue is
  empty, while a failed entry waits to be tried again, and before a purge is completed. After a document filed alone, the split of its folder looks first at the folder's own
  listing, and loads the tree only when the folder may hold enough notes to split. A note edited by hand in place, leaving its folder's
  time as it was, is seen by the next lot. The first lot of a process that takes the filing over keeps what it parses of the tree,
  so that its second lot does not parse every note again (a session of the store outside the filer, such as a CLI call's, still keeps
  nothing in its first). From one lot to the next the filer keeps what it read of the raw copies and of the copies
  each note cites (`OriginIndex`), where it read them all again: the first look of a lot checks every file by modification time, size,
  inode and change time and parses only those that changed, so a copy or a note changed by hand or by another process between two lots
  is seen by the next one, as before. The gain is in the lots that ask which notes cite a copy: a new version of a key (states and
  preferences kept under one key), or a text seen before; a caller that never passes a key gains little. The index stays in the memory
  of the process that files for as long as it lives, packed as one object a note (its signature and the copies it cites, about 250
  bytes); with 50,000 notes the working set between two lots is about 40 MB higher, the peak is the same. Any other session of the
  store still leaves the index empty. The index, in and out of a session, no longer trusts the stamp of a file written in the last two
  seconds (two writes in one clock tick can leave the same time and size: it is read again the next time), and compares the change
  time too, as the parse cache of `WikiStore.load` does. Outside a session it lists every folder for the notes, as before, and keeps
  the listing, so a note added and then deleted between two sessions is no longer taken as citing its copies. Any process that opens
  the wiki may become the filer, and the one that takes the filing over loads the tree first: with processes that live briefly (CI, a
  process for each task, crashes over and over) filing does less than with one that stays, and a long-lived shared server
  (`llmwiki2 serve`, or an MCP server that stays up) is the better choice (README, "Many agents, short processes"); what the first lot
  keeps and the index of the raw copies make that takeover cheaper.
- A search that another process writes the index under while it reads (a fast filer at every step) reads again inside one state of
  the file, whatever is written meanwhile, instead of failing after a few attempts with "the search index cannot be rebuilt".
- A search never waits for the write lock of the search index: a process that writes notes puts them in the index before the call
  returns, and before the journal is told an entry is filed (it looks only at the files its write changed); a search that finds the
  index behind the notes writes it only when the lock is free, and otherwise answers from what the index holds, leaving out the
  notes gone from the folder meanwhile. A search looks at the files of the wiki at most once a moment on the machine: a process takes
  the listing another process of the machine made a moment ago, when what that listing found is in the index as it holds it (the
  processes share a few numbers in memory, never on disk; a process of another Windows session, or of another time namespace on
  Linux, lists for itself, and so does one that finds the name in `/dev/shm` taken by a file of another user, which the tail hint of
  the journal no longer uses either). A note changed
  by hand, not through the library, is found by the searches after that moment. A note filed by any process is found at the next
  search, by its words and, with an embedder, by meaning: the notes looked at by meaning are those of the index, not those of the last
  listing. The index file keeps which notes its last writes changed,
  so a search that finds it written by another process reads those rows only, not the state of every note. A batch (`ingest_many`,
  `rebuild`) puts its notes in once, at its end.
- An index never built whole (a wiki searched for the first time, a file deleted, damaged or of an older schema) is built in the
  background, by one process of the machine at a time (a lease, `.llmw2/cache/search-v6.sqlite.build`), in a thread of its own; its
  lease also says when it began. Every search made meanwhile, in any process of the machine, waits for the build until 4.75 seconds
  after it began, and never past 4.75 seconds after the search itself began (one that began before the build waits no longer), so a
  small wiki is answered whole, as by release 1.0.5, and the rest of the five seconds a first search may take is left to its answer;
  the searches of one process run one at a time, so one queued behind others adds their answers to its time. The bound is on the
  wait, not on the answer: with many processes starting at once on one machine the answer after the wait can take longer (30
  processes opening a 5,000-note wiki together on a laptop: one first search of 6.6 s in one run of three), and while the build
  runs the searches are slower than once it is done (30 sessions on a 5,000-note wiki: search p95 1 to 2.8 s, against tens of
  milliseconds after): run `llmwiki2 index` first where many users open a wiki that has no index yet. It waits on the lease,
  never on the write lock of the file, and one whose lease does not say when it began is not waited for. `close` waits for the build
  it stops 4.75 seconds at most. Past that wait, until the file is built whole, a search answers at once, without reading the notes
  or waiting for the build, from the notes its lots committed (each lot is durable), the notes the process wrote since its first
  search (a note it changed or deleted leaves the lot's row) and the entries not filed yet; a hit whose file is gone is left out.
  Such results say so: `Wiki.search` returns `Hits`, a list of `Hit` as before with `partial` (`Partial`: notes indexed so far, of the
  notes listed; None once the file is complete); a caller that does not look at `partial` takes such a list for a whole one. The MCP
  tool `search` adds, after the hits, one more text, "search index being built (N of M notes): the results may be partial", and
  `partial` (`indexed`, `notes`, `message`) beside `result` in its structured content: a client that reads every text of the result
  as a hit in JSON (release 1.0.5 gave hits only) meets a text that is not one while the index is being built. The HTTP `/search`
  adds a `partial` key; `ask` and `recall` end the answer with a line that says the notes read may be incomplete, and `Answer.partial`
  (and the `partial` key of `recall`) gives the sentence. A CROW `ask` or `recall` whose search finds no note during the build does
  not walk the folders, as it does once the index is whole: the walk loads the whole tree, the notes the build is reading (on a fresh
  copy of a 50,000-note wiki with a local model, 128 s before the first model call), so the answer says at once that no note was found
  and that the index is being built. A search that opens the file while the build makes its tables reads them once made, instead of
  waiting for the write lock the build keeps for its first lot (it could wait up to 180 s and fail with "database is locked"). Notes
  changed by hand during the build are seen once the build reaches them.
  Once the file is marked built (from then on the writers put their notes in themselves) the build lists the wiki once more, waiting
  for the writers' locks, so the notes written while the file was not built are in it; only then is the file marked complete (`meta`
  "complete") and the answers are those of the index whole again. A file built whole by an earlier version is completed by one such
  listing at its first search. A build cut by a crash goes on from the lots it committed at the next
  search of any process; a file that cannot be written is built by the searches, in memory, as before. `status` says in `warnings`
  how far the build got.
- `close` waits for the thread of the build also once the file is marked complete, while the build still closes its connection: a
  search that read the file complete in that moment no longer lets the build go without it, so no connection of the build stays
  open after `close` (on Windows the folder of the wiki could not be deleted then, `WinError 32`). The searches answer as before.
- A search looks at the file of the search index once, at its first step (a `stat`: is it the file its connection was opened on?),
  instead of at each of its steps: a file replaced by another process (a rebuild), or a fork, is still seen before the search reads.
- A search whose query quotes an expression or holds a code no longer reads again the notes it looks for them in: a process keeps
  the texts of the last candidates it read (at most 512), by the hash the index holds for them, and reads only a note whose text
  changed; a purge drops them in its own process. Limit: another process that had read a purged note keeps its text in memory
  until newer texts push it out or the process ends; it never serves it, since no note of the index has that hash any more.
- `search` and `recall` (`ask`) leave out a note another process deletes or moves between the listing of its folder and the
  reading of its file, and a folder removed the same way, instead of failing.
- In CROW mode `ask` (`recall`) no longer loads the tree of the wiki: it takes only the notes the search offers, as a load takes
  them (`WikiStore.note_at`: from what the process parsed of a note, if its file did not change), and loads the tree only when the
  search finds none and the answer navigates the folders. With `where`, or a `select` step of one's own, it loads the tree as before.
  What the models are asked, the answer and its citations are the same. What changes is the moment the wiki is read: before, the
  tree was loaded and then searched, so a note written or moved by another process after the load was left out, and a note merged
  meanwhile was read as it was at the load; now each note is read as it is when the search gives it. A note another process deletes
  between the search and its reading is left out (before, it was read as the tree had it), and the reordering may then let in the
  ninth note. A process that only asks keeps in memory the notes it was offered, no longer every note of the wiki.
- The order of `search` and `ask`, and the choices CROW's filing makes by words, no longer depend on the process: the scores of a
  query's words are added up in the order the query gives them, so notes with close scores come in the same order in every process
  (before, the order of a set, which changes from one process to the next, decided the last bits of the sums). Notes with the same
  score keep the order of the tree, a note another process filed since the last listing too (before, it came after the notes listed).
- `WikiStore.load` keeps what it parsed of each note (by modification time, size, inode and change time) between the sessions of a
  process, from its second session on (as it already did outside a session): a purge, a batch of documents or a filing no longer
  parses the whole tree again, only the notes whose file changed. Three things to know: the first session of a process (the first
  purge, every call of the CLI) keeps nothing and pays nothing for it, and the second fills the cache; the process keeps the front matter and text of every note it has loaded, memory that grows with the wiki; and where the
  system gives no change time (Windows) a note rewritten in place with the same size and its old modification time put back by hand
  (`cp -p`, `rsync -t`) is not seen until its size or time changes, the limit `scan_notes` has always had.
- The record of a filing in the journal (`F`, what `Receipt.wait()` returns) names the notes it links to by their ids (`related`),
  never by title or path, so that a purge does not leave the title of a note in the records of other entries.
- A purge no longer writes `log.md` whole: each line that names what was purged is overwritten where it stands by a line of the same
  length in bytes (`* **Verb**: [purged]`, blanks, and ` · by <author>` when the line had one), so the file keeps its size and every
  other line its place, and the cost of a purge no longer grows with the log. A line that links, by the same title, a note that stays
  (two notes of one topic in two folders) is not taken: its note keeps a line that names it. The undo log keeps the bytes of those
  lines only, in a file of the trash that is zeroed and flushed before it is deleted (a copy the trash holds of a file a section overwrote is zeroed
  too, and a trash a crash left with no record is emptied at the next lock). A line too short to hold the marker makes the file be
  written whole, as before: `Purged.log_rewritten` says so.
- The cache of the vectors keeps its rollback journal between writes (`journal_mode=PERSIST`): the file is no longer made and deleted
  at every write of vectors. It holds hashes and vectors, never a text, and a purge cuts it to nothing, with the vectors of the texts
  no longer in the wiki.
- The undo log of a section holds no text: a rewritten file's image is kept as a hard link in `.llmw2/journal/trash/` (a copy where the file
  system has no hard links), emptied when the section ends; a record of appended lines holds their length and hash, not the lines. A filing
  declares in one record the raw copy and note it makes and the index and log it appends to (`WikiStore.declare_filing`), and so pays one
  flush for them instead of four. On POSIX the log is a preallocated segment of the journal's format (a new salt for each section, a CRC for
  each record, `fdatasync`, never truncated); on Windows it stays a file of lines.
- `rebuild` opens no undo log for each document: a durable mark in `.llmw2/journal/` says it is running, it is taken away when the rebuild has
  run to the end, and `check` reports a rebuild that did not finish (it is run again from the start). The mark is made before anything is
  deleted, and what the rebuild wrote is flushed (`os.sync()` on POSIX, every file elsewhere) before the mark is taken away.
- `WikiStore.lock()` is a `FileLock`: on Windows the wait is in the kernel and no longer a poll every 10 ms (an older version of the library
  locks the same byte, so the two still exclude each other).
- `log.md` is written in the order the changes came (oldest first, titled `# Wiki log (oldest first)`), and a line is appended at the end
  instead of the log being read and written whole: the cost of a line no longer grows with the log. The heading of a day is the real
  date; a clock that jumped back leaves headings out of order, which `check` reports as a warning, not an error. A log of an older version
  is turned around once when the wiki is opened. The OKF §9 example shows the newest first (§9 gives no rule on the order); the README says so. An older version of llmw2 that
  writes into a wiki already turned around adds its lines at the top.
- `llmwiki2 check` and `GET /check` tell warnings apart: the command prints them with `warning:` and exits with 1 only for errors.
- A split's moves and its "Split" log line are one section: if the split fails half way nothing is moved and the line says 0 opened.
- A folder's `index.md` gets the entry of a new note appended instead of being written whole: the cost of a note no longer grows with the folder.
  The new entries stand last until the next full write, which any other change of the index (an entry that changed, a note that left, a
  subfolder) does, in order, as before.
- `search` and `ask` keep the BM25 index of the wiki in a file of the bundle (`.llmw2/cache/search-v6.sqlite`, a cache: delete it and
  the next search builds it again), shared by the processes on the wiki: a search lists the files (their time and size, nothing read)
  and reads only the notes new or changed since, by this process or another one, so a process no longer reads every note at its first
  search; `where` filters on the `fields` the index keeps, with no note read (the notes are grouped by their `fields` once, until the
  file changes, so that any `where` judges only the distinct ones). The scores and the ranking are exactly those of an index built
  whole. A build writes the notes in large lots, so that it rewrites each page of the postings fewer times; `import llmw2` does not
  load the index, the first `Wiki` does. After the upgrade the first search of a wiki builds the file once: on a large wiki it is
  slower than the first search of the release before, which kept the index in memory, and it writes the file; after it, every
  process opens the index without reading the notes again. Each process keeps one connection to the file (WAL, `synchronous=NORMAL`, a
  checkpoint every 16,000 pages), tied to the file and to the process; deleted rows are zeroed (`secure_delete`), and a purge cuts the
  WAL to nothing and deletes the files put aside as damaged (`.bad`). A damaged file is put aside and built again, and a file with views
  or triggers (a wiki received from
  someone) is not trusted and built again. The release before ignores the file. `where` is a filter, not an access control.
  On Windows the folder of a wiki cannot be deleted while a `Wiki` that searched it is open: `close()` it first.
- A purge waits `FORGET_WAIT` (30 s) in all, whatever it waits for: the wiki's lock in each of its sections and in the filing again of
  the documents of a fused note, the locks of the search index and of the vectors, the readers of the index's WAL (the journal is not
  waited for: the filer completes it); the work does not count. What is not done in that time is said, not waited for: `Purged.pending` also names `files` (another
  writer held the wiki all along: only the tombstone is written, and the filer or the next purge does the rest from its hash, as for a
  purge cut right after it) and `undo` (the undo log is not zeroed yet); the documents not filed again in time are in `stopped` and keep
  their raw copies, as when a model fails. On Windows a `FileLock` wait with a limit is in the kernel's queue with the waits with no
  limit (an overlapped `LockFileEx`), so writers that keep waiting for the wiki do not pass over the purge. The index's lots wait for
  its lock until one deadline. The sections of a purge no longer make the index look at every file: its own look, within
  the wait, takes in what it changed. `dry_run` waits as before. A batch that made no folder no longer takes the lock at its end.
- `delete_note`, and the section of a purge that deletes, no longer read the wiki whole once its search index is complete: the index
  file keeps the paths each note links to and the raw copies it cites (schema 6: `search-v6.sqlite`, built once in the background as
  any new index), so under the wiki's lock the wiki is listed (each file's time and size, nothing read) and only the note's folder,
  the notes the index holds as linking to it or citing its raw copies, and the notes whose file the index has not read as it is now
  (new, changed, or written in the last two seconds) are read; the same notes change, the same way, a note edited by hand a moment
  before included. On a wiki of 5,000 notes, measured in one window with the release before on a loaded machine, a `delete_note` in a
  process already started took about 260 ms against 630, and the first call of a new process about 440 ms against 6.8 s; a purge
  about 360 ms against 5.9 s, and 470 ms against 5.3 s in a new process; at 50,000 notes a `delete_note` about 620 ms against 5.9 s, and 1.1 s against about 3 minutes for the first call. On a small wiki (157 notes), where the tree costs little,
  the writes that make the delete safe show: about 140 against 100 ms (the first call about the same, 190 against 200); they are
  some eight flushes of the disk (the records of the undo log, the files at the commit, the tombstone of the journal), the price of
  the recovery after a crash and of forgetting without traces. A delete's process now holds the index's state of the notes, as a
  search does (about 29 MB at 50,000 notes). While the index is not complete (its first build), or cannot answer, a delete reads the
  wiki as before: before the lock, the section listing again only the folders changed meanwhile, and the store deleting on that copy
  (a delete loads the tree once and looks at it again under the lock; `delete_folder` once). A note edited in place by hand meanwhile
  is read again before it is written; a link to the deleted note added that way in that moment stays, as in a batch. How many
  processes of the machine read the wiki outside its lock at once is bounded by the memory: each takes one of N places
  (`.llmw2/read-<i>.lock`), N being half the physical memory free when it starts (`READ_SHARE`: the other half stays to the rest of
  the machine, so the reads never take all that is free) over the cost of a read, the notes of the search index times
  `READ_BYTES_PER_NOTE` (7 KiB, from the memory a read took at 5,000 and at 50,000 notes); a process with no place reads under the
  lock, as before.
- The restore of an interrupted filing tries again for a moment, too, the deletion of a note the filing had made, as a deletion does:
  a note another program held open (or a process of a release whose reads did not share deleting) was left there, and the entry it was
  filing went, when filed again, into a second note; the wait is under the wiki's lock, at most about 0.3 s a file.
- On Windows a note that another process is reading no longer makes a purge or a deletion fail ("the file is used by another
  process"): the library opens the files of the wiki for reading shared for deleting too (`FILE_SHARE_DELETE`, as any open file is on
  POSIX), and a deletion that finds a file another program holds open is tried again for a moment, as a replace already was. A replace
  of a note another process holds open still waits that moment and then fails, as before (Windows does not replace an open file).
  That moment (about 0.31 s a file at most) is waited under the wiki's lock and outside a purge's wait: with N files held open by other
  programs the lock is held up to N times that. Where a deletion is the old kind (FAT/exFAT, SMB, Windows before 1903) a file deleted
  while open stays listed until it is closed: reading the wiki takes it as gone, as a note deleted since the listing.
- `Wiki.remember` waits for room in a full queue 0.2 s by default (`timeout=REMEMBER_WAIT`), then raises `QueueFull`; it waited with no
  bound. A loop that counted on that wait to slow down to the filer's pace (an import made of `remember` calls) now gets `QueueFull`
  instead: pass `timeout=None` to wait as before, or give the documents to `remember_file` or `ingest`, which do not go through the
  queue. 0.2 s is the p99 the acknowledgement is held to: a caller under the rate the filer keeps up with never waits that long, and one
  over it hears so at once instead of hanging (a dialogue turn, a node's callback, a tool call). `QueueFull` begins with "NOT saved" and
  carries `queue`, `queue_limit`, `oldest_s` and `retry_after_s`: the queue over the rate this process saw entries filed at in the last
  minute once it saw 5 filed or, in a process that saw fewer (a new MCP session), over the rate the last 20 filings in the journal took
  (their `ms`); with neither (the first refusals of a flood), one entry each as long as the oldest open one has waited, since the
  sections file one at a time; None only when nobody files (`reason` "stall") or no entry is open. `oldest_s`, there and in `status`,
  does not follow the wall clock: each entry records the boot of
  the machine it was acknowledged in and the seconds since that boot, by a clock the wall clock does not move (Windows: the boot
  counter and `GetTickCount64`; Linux: `boot_id` and `CLOCK_BOOTTIME`, sleep counted), and its age in the same boot is that clock's
  difference, in any process. So a clock set back or forward (a robot that finds the time again, a laptop's clock corrected) no longer
  gives an age of hours nor the warning "the oldest entry has waited". In another boot, on a system with neither clock, or for an
  entry written without it, the age trusts the wall clock once (its age when the process first read it, then the monotonic time
  since). The journal keeps `at` as the wall clock gave it. A caller waiting for room looks at the journal's shared
  tail hint every 50 ms, with no system call, and reads the journal only when it moved (and once a second for a writer the hint does not
  show), where it read it every 50 ms whatever happened, scanning the segments' headers each time. The room is checked again under a
  lock of the journal that only these entries take (`admit.lock`; the filer's records never wait for it), against the tail: with no
  read when the shared tail hint says nothing, or too little to fill the queue, was written since, else after reading to the tail. So
  callers of several processes no longer pass the limits together: 50 processes kept 275 entries for a limit of 256, and the limit in
  bytes (half a segment) could be passed in the same way. That lock is waited for only until the wait for room ends, and 50 ms more
  (`ADMIT_MARGIN`): a caller behind a long line of others is refused then, nothing written, with the true reason: `QueueFull.reason`
  "busy" and "the queue is not full: the journal's admission was held ... by other writers (other entries being saved, or the journal
  being sealed after a delete or a purge)"; over MCP, "the memory was busy saving other memories", never "still filing N", which reads
  as a full queue. `reason` is "full", "share", "busy" or "stall". The flush of the acknowledgement is not bounded (it is the
  durability), so a call can still last a little longer than `timeout`: under overload (30 to 50 sessions writing with no pause),
  6 to 34 calls in about 600 took over 1.1 s, 2.5 s at most, where the release before made a caller wait up to 3 minutes in the same
  case. A purge does not go through the queue's limits: a full queue never refuses or holds it.
- The last places of `remember`'s queue are kept for the writers that keep few entries waiting: a writer that keeps `QUEUE_LIMIT // 8`
  entries of its own not yet filed (32 of 256) does not get the last `QUEUE_LIMIT // 8` places (it stops at 224 of 256); one that keeps
  fewer may fill the queue to the limit. A writer is a process and, in it, the author a call declares: `author=`, else the one in force
  (`X-Author` of `llmwiki2 serve`; over MCP `X-Author`, else the client's name from the handshake, else its `User-Agent`), else the
  `Wiki`'s; the calls of a process that name none are one writer. So a job that writes faster than the filer (an agent, a script,
  another MCP client, an import), or several already busy, leave 32 places to a person, who needs a few, in another process or in the
  same one (a server, an app) when the two name different authors; a writer alone (a robot) still has 224. A process counts the entries
  each of its writers wrote that are open in what it read of the journal, in memory: nothing new is written in the journal. A job alone
  now stops at 224: with the default wait it gets `QueueFull`, whose message names the writer that keeps that many, and whose
  `retry_after_s` is the time until that writer gets a place again (the queue under its 224, or its own entries filed down under 32,
  whichever comes first, at the filer's pace), not the time to empty the whole queue; `timeout=None` waits for its place. Limits: the author is declared, not verified, so the share guards against a writer that fills the queue by
  mistake, not against one that means to: a job that names another author for each entry stays a writer that keeps few and fills the
  queue to the limit. A person and a job of one process that name none, or the same one, are not told apart, and so are HTTP clients
  that name none and send the same `User-Agent` (over MCP the name falls back to it when the session kept no client name, the usual
  case with `llmwiki2 serve`); the same author in two processes is two writers; after a restart a process counts from zero, so a job gains
  up to 31 places more; a new job's first 31 entries count as few, and may take places of the 32 kept until it keeps more. Under a
  limit of 8 there is no share.
- What a process keeps of the folders between two looks at the files of the wiki (the listing a search does at most once a moment)
  holds no object per note but its name, its path and three numbers: about half the memory it took, in every process that searches.
- The flat routing Choice is asked once per document: when the limit of top-level folders (`max_top_folders`), or a rebuild, sends a
  note to the likeliest top-level folder, that folder comes from the answer `route` already got for the same request (the same
  note, instructions and folders) instead of a second classifier call. No decision changes; a request that differs (the tree
  changed in between) or one that failed is asked again. The answers are kept for the document being filed only (for the note
  being looked at, in a routing audit). In the `remember` path of a wiki at the limit this is one call less for each note the
  limit sends elsewhere (3 to 4% of the classifier's calls on a corpus that fills the limit).
- CROW asks the match and the See-also questions of a new note in one classifier request (`crow.joint_match_relate`, env
  `OKF_JOINT_MATCH_RELATE`, on by default): `match` sends, with its own questions, the relate questions of the note the document
  becomes if nothing is merged (the notes `relate` will be given, found as it finds them: the notes of the match and of the folder,
  cut as it cuts them, and the notes sharing rare values with it, counted as they will be once it is written), and `relate` takes
  those answers when it reads the same state (a new note's title, summary and body are the document's). A note the request did
  not cover is asked in a request of its own, as is the whole `relate` of a merged note. One classifier call less for a new note;
  with a hosted classifier its cost per document falls to about 0.85 of what it was (the state is sent once instead of twice). With
  a small local classifier it saves calls but not time, so it can be turned off: `false` asks match and relate apart, as before,
  with the same decisions. A request of the two kinds has op `match+relate` in the usage log, with the match questions under keys
  `m*` and the relate questions under `r*`. A classifier that answers a question the same in any request takes the same decisions;
  custom steps whose `match` or `relate` is not CROW's own ask as before. The setting is not part of the decision hash.
- A hosted classifier that takes few questions per request ("questions must be an object of 1..16 questions") gets a longer
  batch of Noul questions in halves until each part is taken, as the local server's "1-64 fields" refusal already did; before,
  such a refusal sent the step to the LLM.

## [1.0.5] - 2026-11-17

Tool descriptions an MCP client's model can choose by, the listing on Glama, and a CI that only reads the repository.

### Added

- `glama.json` names the maintainers of the memory's listing on Glama, so they can claim it and have Glama build and
  check it.

### Changed

- Every tool of the memory says first what it does, then when to call it and when to call another one instead, what it
  returns, what it changes and what it refuses, as an MCP client's model reads it to choose; the descriptions reach the
  client as plain paragraphs, with no indentation of the source left in them. Every tool is also marked as acting on a
  closed world (`openWorldHint: false`): the wiki, and over stdio the files the user points at.

### Security

- The CI asks GitHub only to read the repository, as code scanning advises, instead of the repository's default
  permissions.

## [1.0.4] - 2026-11-17

The memory answers from an empty wiki the first time it is used, and a test of the spending cap that failed now and then
on slow machines.

### Fixed

- `llmwiki2 mcp` makes the wiki when it starts, as `llmwiki2` does for its page: on a computer where the memory was never
  used, a first `recall` failed with "wiki not found" and `search` with no reason given, until something was remembered.
  They now answer from an empty wiki. A wiki folder that cannot be made is said on stderr, and the memory does not start.
- The test of the spending cap counted every call a lane made once the cap was passed, but the document being filed ends
  with all its calls (`UsageTracker.filing`), so where the cap fell decided whether it passed. It now checks that each
  lane stops at its next call and that the document being filed makes at most the calls of one filing, at caps that fall
  on each step of it.

## [1.0.3] - 2026-11-17

The memory in the official MCP Registry, working with no setup on the client's models; DeepSeek as the default writer
on OpenRouter; a README that says first that everything can run on your computer.

### Added

- The memory is listed in the official MCP Registry as `io.github.crow-hq/llm-wiki-v2`, so an MCP client that reads the
  registry finds it and starts it with `uvx --with "mcp>=2.3,<3" llmwiki2 mcp`, on what `llmwiki2 setup` saved or,
  never set up, on the client's models with no key. `server.json` describes it; the release publishes it after PyPI,
  and a test keeps its version, its command and the README's `mcp-name:` line in step with the package.

### Changed

- On OpenRouter the default model is `deepseek/deepseek-v4.1-flash`, the writer CROW's thresholds and benchmarks are
  calibrated with; `google/gemini-3.8-flash` is still suggested. A wiki that names its model keeps it.
- The README says first that everything can run on your computer, with no key: the desktop app out of the box, the
  library with the writer and the classifier behind Ollama.

### Fixed

- `llmwiki2 mcp` started by a client on a computer where the wiki was never set up (no settings file, no
  `OKF_MCP_MODELS`) works on the client's models, the first choice `llmwiki2 setup` offers: every `remember` and
  `recall` failed asking for a classifier key. Once set up, for the memory or for the page, the settings decide as before.

## [1.0.2] - 2026-11-13

Answers in the language of the question with small local writers, Markdown front matter kept out of notes, and the
licence that survives compiling.

### Added

- Every module of the package carries its copyright and licence as a value, `__copyright__`, and the package says to AI
  agents and assistants what the licence asks of them (`llmw2.NOTICE_FOR_AI_AGENTS`): an application that bundles llmw2
  ships it compiled, and compiling drops the header comments.

### Fixed

- With `llm.note_language` (small local writers), an answer (`ask`, the memory's `recall`) is written in the language
  of the question: the prompt's "answer in the language of the question" was ignored and the answer came in English;
  the language is now named, as for notes, and read from the question alone, not from the hints added after it (notes
  that disagree, earlier versions).
- A Markdown file that opens with YAML front matter (an Obsidian note, a page of a static site) no longer brings it
  into its note: with `llm.note_steps` the note copied a short file as it was, front matter included, and showed it as
  text. The file, its raw copy and its hash are unchanged, so nothing already filed is filed again.

## [1.0.1] - 2026-11-12

The wiki as the memory of Claude Code or Codex from the terminal alone, with no page and no server to keep running.

### Added

- `llmwiki2 mcp`: the memory over stdio, started by the client itself (`claude mcp add --scope user llm-wiki -- llmwiki2 mcp`, `codex mcp add llm-wiki -- llmwiki2 mcp`). It is always on, whatever **Start** says, and writes the same wiki as the page, one at a time.
- `llmwiki2 setup` first asks how the wiki will be used. **As the memory of an agentic CLI** asks which models do its work, saves the memory as on, connects Claude Code and Codex when it finds them (over stdio), asking which configurations when there are several (`~/.claude`, `~/.claude-work`, … through `CLAUDE_CONFIG_DIR`; `CODEX_HOME` for Codex), and offers to add to `~/.claude/CLAUDE.md` (Codex: `~/.codex/AGENTS.md`) a few lines, between markers that a later `setup` replaces, that make the client use the memory when you speak in plain words. With neither on the PATH it prints the commands.
- Hybrid models for the memory (`mcp_models = "hybrid"`, `OKF_MCP_MODELS=hybrid`, in `setup` and on the page): the client's model writes and the wiki's classifier decides (Jev through OpenRouter or TypeSafe, or Ollama or Laya on your computer), so only the writing goes to the client and no Haiku subagent runs. Without a classifier set up it works as `client`. `setup` tests the classifier before saving.
- MCP tools for the edits by hand: `folders`, `move_note`, `describe_folder`, `rename_area`, `merge_areas` and `delete_note`. The last two do nothing without `confirm=true`, and the client is told to delete or merge only what you asked for.
- `python -m llmwiki2`, the command line by the name the package installs as (`python -m llmw2` still works).
- Over stdio only, `remember_file(path)` and `sync_folder(folder)` file documents from your disk. Over HTTP they are not offered, as the server may answer other machines, which must not read its files.

### Changed

- The memory's instructions tell the client that the user speaks in plain words, in any language, and never names the tools, with examples in English and Italian ("ricorda che…", "ti ricordi…"); `recall` and `remember` say the same in their descriptions.
- The README and the memory guide start the memory section with the terminal-only setup, in three steps with the answer to each question of `setup`; "Something wrong?" says what to do when Windows application control blocks `llmwiki2.exe`: run it through Python. `setup` registers the memory with the Python that runs it, never a launcher found on the PATH, so it works there too.
- In `setup`, a first memory starts from the CLI's own models, which need no key: Enter at every question after the first gives a working memory.

### Fixed

- Testing a classifier on this computer (Ollama, Laya), in `setup` and with **Test connection** on the page, failed every time while its model was not loaded: the test gave up after the 8 seconds of a decision, before the model had loaded, and the dropped request left it unloaded. A test now waits up to 2 minutes; the decisions keep their timeout.

## [1.0.0] - 2026-11-10

The first stable release: from here on the library follows semantic versioning. What it covers (the API, the settings, the command line, the HTTP API, the MCP tools and the wiki on disk) and what it does not (prompt texts, models, thresholds, caches) is in [Compatibility](docs/library/index.md#compatibility).

### Upgrading from 0.8.0

A wiki written by 0.8.0 opens as it is: notes, raw copies, sync cursors and proposals are read unchanged, and nothing needs converting.

**Breaking changes**

- **Removed settings.** `source_chars` (in a settings file or a mapping): use `llm.read_chars` (`OKF_LLM_READ_CHARS`). `crow.tau_fold`, `crow.tau_ret` and `crow.retrieval_beam` (`OKF_TAU_FOLD`, `OKF_TAU_RET`, `OKF_RETRIEVAL_BEAM`, their keys in the settings file and in `POST /settings`). In the environment, a settings file or a mapping they are ignored with a `DeprecationWarning`, so an old setup keeps starting: delete them. Code that reads them from the configuration (`cfg.source_chars`, `cfg.crow.tau_fold`) raises `AttributeError`.
- **Removed prompt and decision.** In a folder of prompt overrides (`OKF_PROMPTS_DIR`), `classifier/retrieval_folder.md` is no longer read; `classifier/retrieval_note.md` can use `{{passage}}`, the part of the note that best matches the question (an old override still works, without it). The trace of decisions has no `retrieve_folder` any more, and gains `retrieve_note` for the reordering, `match_cut`, `relate_cut`, `quote_unquoted`, `note_retry`, `note_source`, and `select` when the LLM walks the folders. Code that reads the trace must adapt.
- **`over_limit` is keyword-only** in `create_area` and `confirm_proposal`: `create_area("name", "description", over_limit=True)`.
- **The settings page** has no controls for the three removed retrieval settings and has one for `crow.rerank`.
- **`LICENSES/Apache-2.0.md` is now `LICENSES/Apache-2.0.txt`**, for whoever vendors the file by its path.
- **Downgrading.** A folder closed to automatic filing holds a hidden `.closed` file, and new notes can have `omitted` in their frontmatter: versions before 1.0.0 ignore both, so a closed folder takes notes again there.

**Behaviour changes with the default settings** (no code breaks, results differ)

- In CROW mode, `ask` finds its notes with the wiki's search: the best 16 by BM25 (and by meaning, with an embedder), the first kept first and the others reordered by one classifier call, `crow.k` of them read. It no longer walks the folders with the classifier. Only when the search finds nothing (no embedder, and no word in common with any note) does the LLM walk the folders, as in classic mode, so a question that shares no word with any note (one in another language, say) is still answered; the search counts every word of three letters or more, so a question in the wiki's language usually finds notes and is not walked; set an `embedder` (`OKF_EMBEDDER_PROVIDER`, `OKF_EMBEDDER_MODEL`) for the search to go by meaning too. `crow.rerank` (`OKF_RERANK=false`) reads the notes in the search's order and asks the classifier nothing. Classic mode is unchanged.
- CROW's `match` and `relate` ask about at most 24 notes each, the closest ones, and the notes that share rare values add at most 10: on a wiki with large folders a document is compared with fewer notes.
- A quotation of four words or more that the source does not have loses its quotation marks, in a note's body and summary and in an answer, your own `answer` step's included; its words stay.
- Notes written from a long source lose the "Part k of n" labels a small writer copies; a first line that looks like a file name is never taken as a title; `Wiki.search` puts first the notes that hold a quoted expression or a code-like token; `where` on a list field matches an item; a custom `select` without the wiki's search ranks by BM25, and `StepContext.children()` skips folders closed to automatic filing.

**Automatic, nothing to do:** the classifier's cache asks again, once, the questions whose prompt changed; the vectors of an embedder are kept in `.llmw2/cache/embeddings.sqlite` from the first search, under a key that includes the embedder's model; new notes record a new provenance hash.

**New, optional:** the types the methods return or take are importable from `llmw2` (`IngestResult`, `Answer`, `Folder`, `Note`, `Proposal`, `UsageTracker`, ...); the module paths used so far still work.

### Added

- `link_scope` (`OKF_LINK_SCOPE`, `wiki` by default): with `root`, the See-also links code adds (the candidates of `relate`, rare-value ones included, in CROW and classic) stay between notes of the same top-level folder, and a note that changes area (`move_note`, a folder moved or merged, `merge_roots`, `confirm_proposal`, the audit) loses the entries that no longer fit, from both notes. With `folder`, the links stay between notes of the same folder, and a note moved to another folder loses them. `Wiki.move_notes(moves)` moves many notes at once and judges the See-also entries once, after all the moves: two linked notes that end in the same area stay linked, whatever the order.
- A folder can be closed to automatic filing: `Wiki.create_folder(..., routable=False)`, `Wiki.set_routable(path, value)` and `Folder.routable` / `Folder.is_routable` (a folder below a closed one is closed too), kept as a hidden `.closed` file in the folder. A closed folder is not offered by the routing (CROW and classic), not shown to `plan`, not joined by `plan_groups`, gives no notes to merge into and none to the audit; a new folder never takes the name of a closed sibling. With `link_scope` `root` its area gives and gets no link. `move_note` still files there.
- `overflow_folder` (`OKF_OVERFLOW_FOLDER`): the label of a folder that takes a note when routing cannot open a top-level folder (the limit `max_top_folders` is reached) in place of the likeliest existing one, with no classifier call. The note is marked `overflow: {declared: 1.0, at}`, `log.md` says so, and the entry of `BatchReport.deviations` has `declared: true`. A folder that does not exist warns and the behaviour is as before; when every top-level folder is closed and none can be opened, the ingest stops with a `StepError`.
- `omitted` in the frontmatter of a note written from a source: the headings (`#` to `###`) of the source none of whose words of four letters appear in the note's body, in the source's order, at most 20; absent when there are none. Found in code, with no model call; a merge keeps what earlier sources left out and the note still leaves out. The body and its hash are unchanged.
- `Wiki(cfg, redact=fn)`: a function from text to text applied to the texts derived from a document and repeated outside the note's body: a note's title and summary (so its file name, its lines in indexes and See-also lists and in `log.md`), and the names and descriptions of the folders a plan, a split or a new subfolder makes. The body is never touched. Without it nothing changes.
- `where` in `ask`, `search` and `matching` holds a list field when one of its items equals the value (compared as text); other values compare as before.
- `embedder` in the config (`WikiConfig.embedder`): `provider` (`openai`, any server with `POST {base_url}/embeddings`, or `ollama`, `POST {base_url}/api/embed`; empty by default, no embedder), `base_url` (empty: `https://api.openai.com/v1` or `http://localhost:11434`), `model`, `api_key` (optional) and `timeout` (60 s). Environment: `OKF_EMBEDDER_PROVIDER`, `OKF_EMBEDDER_BASE_URL`, `OKF_EMBEDDER_MODEL`, `OKF_EMBEDDER_API_KEY`, `OKF_EMBEDDER_TIMEOUT`; the settings file takes an `embedder` object. When it is set and `Wiki(..., embedder=)` gets no callable, `Wiki` builds the embedder from it (texts go 16 to a request; Ollama's with `truncate`), so the command line, the server, the memory MCP and an app that reads the settings search by meaning too; a callable you pass wins.
- `Wiki.search` puts first the notes that hold what the query quotes (text in double quotes, «», “” or backticks, 3 to 60 characters) or a code-like token (a digit, a capital inside a word, or `- _ . / :` between word characters; 4 characters or more). They are looked for in the best 16 candidates; an expression found in more than 3 of them tells nothing and is ignored; the notes holding one keep their order and come before the others. A query with none keeps the order it had.
- A note written with `llm.note_steps` that was asked for again, or kept the source text, leaves a `note_retry` or `note_source` decision (`fallback` for the latter) in `IngestResult.decisions`; `benchmarks/incremental.py` prints the count per document and in total, and writes it to `meta.json`.
- `Wiki.search(query, k=8, where=None)` finds the best notes for a query with no model call, and returns `Hit`s (`path`, `title`, `summary`, `rank`). BM25 on the whole note (title, summary and body, without See also) over an index that counts each note's words once and rebuilds only for notes that changed; `where` and the empty-query error are as in `ask`. `Wiki(cfg, embedder=...)` takes an optional callable from texts to vectors: the result is then the `k - 1` nearest notes by cosine, one by BM25 and the rest in the order of meaning, no note twice. The vectors are kept in `.llmw2/cache/embeddings.sqlite` under the hash of the embedder's model and the note's text, so only new or changed notes are embedded. In CROW mode `ask` uses it (see Changed); ingest is unchanged.
- `llm.note_copy_words` (`OKF_LLM_NOTE_COPY_WORDS`, 0 by default): with `llm.note_steps`, a source of fewer words than this keeps its own text as the body, with no body call, and only the title, summary and tags are asked for. 0 leaves `note_steps` as it was.
- `llm.note_copy_parts` (`OKF_LLM_NOTE_COPY_PARTS`, off by default): with `llm.note_steps`, a source too long for one call keeps its own text as the body instead of being written from notes on its parts, with no call on the parts; only the title, summary and tags are asked for. A small writer loses much of the parts' notes when it combines them.
- `benchmarks/eurlex_select.py`, `cuad_select.py`, `hmrc_select.py`: three real professional corpora to measure on, built from their open sources with a pin and the sha256 of every file (MultiEURLEX laws in Italian and English, CUAD contracts, HMRC internal manuals); the texts are not committed. `benchmarks/README.md` gives the commands to build and score them with cloud models and a reference of the branch to compare a change with.
- `benchmarks/retrieval.py --as-asked`: the notes are chosen as `ask` chooses them, each run in its own mode (a classic run: the LLM walks the folders; CROW: the wiki's search, then the classifier reorders), so CROW and classic wikis can be compared on the notes an answer would read. The banner of the README and `benchmarks/README.md` give CROW against classic measured this way on eleven corpora.
- The repository follows the REUSE specification 3.3: `REUSE.toml` states the copyright and licence of the files that carry no header, `LICENSES/` holds every licence text used (the Apache License 2.0 text is now `LICENSES/Apache-2.0.txt`), and a CI job runs `reuse lint`.
- Each prompt in `src/llmw2/agents/prompts/` starts with an HTML comment holding its copyright, its SPDX licence and the project's canary GUID. `Prompts.load` strips it, also from an override in `OKF_PROMPTS_DIR`, so the text sent to a model and the prompts' fingerprint are unchanged.
- `AGENTS.md` (read by `CLAUDE.md`) tells contributors and AI assistants the licence, the obligations of whoever distributes code or prompts from the repository, and how to contribute.
- `llm.note_steps` (`OKF_LLM_NOTE_STEPS`, off by default), an opt-in for small local writers (with a strong writer, the note in one call is more useful: leave it off): `summarize`, and the final write of a long source, ask for the note body as plain text (in the source's language), strip a code fence or lone title the model adds, and then ask for the title, summary and tags from the start of the body, instead of one JSON reply. If the body is empty or the second reply is invalid, the single JSON call writes the note. With the setting off, prompts and calls are unchanged.
- `Wiki.search` is also in the memory MCP, the HTTP API and the command line, with no model call: the memory's `search(query, k, where)` tool (read-only; `recall` still writes an answer), `GET /search?q=…&k=…` and `llmwiki2 search QUERY [-k N] [--where KEY=VALUE] [--json]`. They return the same hits as `Wiki.search` (path, title, summary, rank).
- `llm.note_language` (`OKF_LLM_NOTE_LANGUAGE`, off by default), an opt-in for small local models, which ignore "write in the language of the source": the prompts that summarize and merge (`summarize`, `summarize_part`, `summarize_combine`, `merge`, `merge_source`) end with a line naming the source's language when it is Italian, French, German, Spanish or Portuguese. The language comes from function words in the source (no model call, none when unclear). With the setting off the prompts are unchanged.
- Python 3.14 is tested in CI and listed in the package's classifiers.
- The types that methods return, take or that you build by hand are exported from `llmw2`: `IngestResult`, `Answer`, `Folder`, `Note`, `Proposal`, `Deleted`, `Problem`, `OriginRecord`, `UsageReport`, `UsageTracker`, `Decision`, `NoteChanged`, `AnswerCache`, `Embedder`, `LLM` and `Classifier`. The examples and the guides import them from there; the module paths they had still work.
- A [Compatibility](docs/library/index.md#compatibility) section in the library guide: what semantic versioning covers from 1.0.0 (the names in `llmw2.__all__`, the `Steps` API, the settings, the command line, the HTTP API, the MCP tools, the layout of a wiki on disk) and what it does not (prompt texts, models and presets, thresholds, caches, decision names, everything not exported), and the deprecation rule.

### Changed

- CROW's `match` and `relate` ask about at most 24 candidate notes each (`MATCH_TOP`, `RELATE_TOP`): the notes of the document's folder first, then the others ranked by BM25 on their text (relate: first by the probability `match` gave them); the notes that share rare values are their own group, now at most 10 (`linking.RARE_TOP`, was 40). A cut is the decision `match_cut` / `relate_cut`. Their requests grew with every note of the candidate folders, so the classifier's tokens per document grew with the wiki; with the cap they nearly stop growing. Measured on 1,000 Enron mails filed in their owners' folders (out of the corpora the cap was set on): classifier tokens per document about 10,000 from 300 documents on, rising by about 160 every 100 documents after 400, against about 16,600 at 400 and rising by about 4,000 every 100 documents without it; AMI 0.252 against 0.207 at 400 documents; the same retrieval and merges. On six smaller corpora: AMI, retrieval and merges unchanged, See-also links within the variation between two runs.
- With no wiki search in its context, CROW's `select` ranks by BM25 alone and keeps no vector file (it built a cache in the current folder).
- **Behaviour change:** in CROW mode, `ask` finds its notes with `Wiki.search` (16 notes, BM25 and the embedder if there is one; the reordering below pays off most with an embedder) instead of walking the folders with the classifier. The first note stays first; the other 15 are reordered by one batched classifier call, each asked with its title, summary and the 600-character passage of its body that best matches the question, and the first `crow.k` are read. It finds the right note in the first three more often, with one classifier call instead of about three. Only when the search finds no note at all (no embedder, and no word in common with any note) does the LLM walk the folders, as classic mode does: the decision `select` is recorded with `fallback` and no setting is involved, so a question that shares no word with any note is still answered. `crow.rerank` (`OKF_RERANK`, `True` by default; `crow_rerank` in the settings) turns the reordering off: the search's order, no classifier call; meant for small local classifiers on a wiki where it does not help. If the classifier fails, the search's order is kept and the decision is traced as a fallback. The `classifier/retrieval_note` prompt gains a `passage` value.
- With `llm.note_steps`, a body written in another language than a source whose language is known is asked for once more with the next seed (`ask_text` and `LLM.complete`/`chat` take an optional `seed` that replaces the configured one for that request); the second body is used if its language matches the source, otherwise the note keeps the source text; a warning is logged.
- With `llm.note_steps`, checks in code around the writer's two calls. Metadata call: it is given the document's own heading (else the file name) and up to 5 proper names of the source; a title with an id-like token (hex or digit run) the source lacks, a cut or dangling title, or a name misspelt against the source (or an acronym or name of the same initial and close length, as ACM for ACME) is repaired (one candidate) or replaced by the heading, else the file name without its id suffix. Body call: up to 15 URLs, e-mail addresses, paths, code spans and identifiers of the source are listed to copy exactly; prompt tags and an outer code fence are stripped, repeated sections removed, a near-identical altered string restored in place; if strings the source does not have remain, or the language is wrong, the body is asked for once more (one retry in total, shared with the language retry) and is the source text if that fails too. A summary in another language than the source, even one short sentence, becomes the first sentence of the body. A note with none of this makes the same calls as before.
- With `llm.note_steps`, a body that quotes four words or more the source does not have in that order (a quotation changed, made up or moved to another speaker; letters and digits compared, so spacing, punctuation and quote marks do not count) is asked for once more, like a changed string, and is the source text if that fails too.
- A note written in one call (the default, strong writers), from the parts of a long source, or by a merge: a quotation of four words or more, in the body or the summary, that the source does not have (for a merge, neither the existing note nor the source) loses its quotation marks, in code and with no model call, and the trace has a `quote_unquoted` decision; its words stay. The same goes for an answer of `ask`: a quotation the notes it read do not have loses its marks (`Answer.decisions`). `misquoted` (and so this repair and the retry of `llm.note_steps`) looks for each part of a quotation between omissions (`...`, `…`, `[...]`) on its own, and does not count the quoted-printable escapes of an exported mail (`=20`) as words of the source.
- With `llm.note_steps`, a body that gives a quotation of the source to someone else is asked for once more, and is the source text if that fails too: the names in the body's sentence of the quotation are none of those the source writes next to it (`"...," Dyke said`). Not judged: a quotation the source does not put in quotation marks itself (a clause of a contract), a title or a defined term (most words capitalized), a sentence copied from the source, and a quotation given without a name. A quotation in Markdown emphasis (`*"..."*`) is now judged too.
- The first sentence that replaces a summary in another language is taken from the body's first line of prose, not from a list item, quote, table row, tag or code block; with no prose line, from the first line without its list or quote mark.
- A source under `llm.note_copy_words` is no longer split into parts when it is longer than one call: its text is the body, and the calls on its parts (thrown away before) are not made. The note is the same.
- A note written from the parts of a long source loses the "Part k of n" labels a small writer copies into its body, title or summary (the label line and the part prompt's "This is part k of n, starting in: ..."), unless the source itself has such words.
- `over_limit` is keyword-only in `Wiki.create_area` and `Wiki.confirm_proposal`, as `routable` is in `create_folder`. **Breaking** for code that passed it by position.
- The package's licence expression covers every licence of the files it ships (`AGPL-3.0-or-later AND Apache-2.0 AND MIT AND ISC AND (Apache-2.0 OR MPL-2.0) AND OFL-1.1`), and the development status is Production/Stable. `SECURITY.md` supports the 1.0.x line.

### Removed

- `source_chars` (the key in the settings file or a mapping), deprecated since 0.4.0: use `llm.read_chars` (`OKF_LLM_READ_CHARS`). In the environment, a settings file or a mapping it is ignored with one `DeprecationWarning`; `cfg.source_chars` raises `AttributeError`.
- `crow.tau_fold`, `crow.tau_ret` and `crow.retrieval_beam` (`OKF_TAU_FOLD`, `OKF_TAU_RET`, `OKF_RETRIEVAL_BEAM`, and the same keys in the settings file and in `POST /settings`, whose page loses the three controls): `ask` finds its notes with the search. In the environment, a settings file or a mapping they are ignored with one `DeprecationWarning` per name and process; `cfg.crow.tau_fold` raises `AttributeError`.
- The prompt `classifier/retrieval_folder` (an override of it in `OKF_PROMPTS_DIR` is no longer read) and the decision `retrieve_folder`.

### Fixed

- A first line that is a file name ("ACME_04_06_2017-EX-10.11-SUPPLY AGREEMENT", "report_v2_final.pdf"; two signs by form: `_` between words, a token of 20 characters or more, an `_` or 8-digit date, a file extension) is no heading. `heading_of` and the fallback of `checked_title` skip it, the metadata call of `note_steps` does not show it (neither as the document's name nor in the note text), and the guard against file-name titles of a small writer (`_unusable_title`) refuses such a title whatever the name of the source's own file. A title that opens with the file counter of the source's own name ("007 acme 08 11 2005 ...") is that name copied: it is refused too, and the metadata call is made once more without the name. A small writer copied the file name as the title of every note of a corpus whose texts open with it.
- `benchmarks/cuad_select.py` labels each contract with its official CUAD type (the folder of its PDF), not the type read from the file name, which differed for about one contract in eight; the selection and its checksums are unchanged. `--max-chars` lets a selection take the longest contracts too.
- `benchmarks/incremental.py` and `benchmarks/retrieval.py` kept the package's CROW thresholds whatever the classifier: a run with a local classifier (`OKF_CLASSIFIER_PROVIDER=custom`) got Jev's, and a threshold set in the environment (`OKF_TAU_CONS`) was dropped. A run now uses the preset of its classifier and the environment's thresholds, and still ignores the local settings file; runs with Jev are unchanged.
- Type errors found by strict `mypy`, which CI runs: a variable of the ingest held a text and then a note, and two calls did not narrow an optional.

## [0.8.0] - 2026-11-03

### Upgrading from 0.7.0

The API only gains optional arguments, but some defaults change what a wiki does:

- A wiki has at most 10 top-level folders (`max_top_folders`, `OKF_MAX_TOP_FOLDERS`). A wiki that already has as many opens no new one: a note that would open one is filed in the likeliest existing top-level folder and marked `overflow`. To keep opening areas, set a higher limit.
- At the end of a CROW batch, a planned folder left with one note gives it to its parent, and a planned top-level folder with no note of its own and a single subfolder takes the subfolder's place.
- Links to files that are not in the wiki become plain text when a note is written.
- A note edited by hand during a batch is never overwritten: a document that must merge into it is counted in `BatchReport.conflicts` and filed by the next run.
- On Windows the wiki holds a lock file, `.llmw2/lock`.

### Added

- `Wiki.purge(key, *, author=None, dry_run=False)` and `llmwiki2 purge KEY --yes` (without `--yes` it says what it would delete and exits with 2) take a document out for good: its raw copies (a spreadsheet's sheets with it), their archived versions and every note citing them go. A note fused from several documents is deleted whole and the other documents it held are filed again from their raw copies, as `rebuild` does (no plan, no new folder), so the guarantee for paraphrased phrases lies in the construction (delete, then file again from the raw copies), not in a check of the text. The links to the notes deleted go from the others, index.md is written again, the lines of log.md that name a deleted note or raw copy keep their verb and author and say "[purged]", and a "Purged" line without the document's title is added. `sync(..., purge_removed=False)` and `llmwiki2 sync --purge-removed` purge what was removed at the source instead of marking it. No MCP tool, no keeping for a time in the code (the docs give a few lines to do it), and the classifier cache, which holds hashes and labels only, is not touched.
- `sync(..., max_cost=...)` and `llmwiki2 sync --max-cost USD` cap what a sync spends, in the cost the provider reports. `UsageTracker.limit(max_cost)` opens the cap, which counts and stops only the calls made inside it (in its thread and the lanes the batch starts), so other work on the same tracker, such as the server's, goes on, and `UsageTracker.check()` is called before each request to a model, the writer's and the classifier's, retries included: once the cap is reached it raises `SpendingCap` (a `WikiError`, not a `ModelError`: no fallback of a step takes it for a failing model), so the sync stops with `stopped` saying "spending cap" and the cursor at the last page finished, and the next run resumes without paying again for what was filed. A document is never filed half way: the cap is not checked while one is being filed and is checked before the next, and a document still being prepared or a window being planned is not filed and is redone by the next run. The cap is passed by at most the calls in flight (`concurrency`) plus the calls of the filing of one document; an answer served from the classifier's cache is never blocked; a provider that reports no cost (Ollama) never reaches the cap, and without `max_cost` nothing changes. A page is read and planned whole before its first document is filed, so a cap under that cost files nothing: `stopped` then also says "nothing was filed" and what reading and planning the page cost so far; in the measures, planning a page of 150 documents cost about as much as filing 20 to 25 of them. The cost of a sync in the history is that of its own calls, not of other work on the same tracker. The estimate tends to be too low (in the measures the ratio of the estimate to the real cost went from about 0.7 to 1.3, up to about a third under): set `--max-cost` to about 1.5 times the estimate. The cap is passed by at most the calls in flight plus the filing of one document. `UsageTracker.spent()` gives the cost so far.
- Each sync adds the documents it filed and its cost to `.llmw2/sync/history.jsonl` of the wiki, and `sources.estimate(wiki, connector)` (`llmwiki2 sync FOLDER --estimate`) lists the changes from the saved cursor without fetching or filing anything and returns `{"documents", "est_cost", "basis"}`: the documents a sync would file times the mean cost per document of the history, or `None` ("no history") before the first sync, when the command advises `--max-cost`.
- `classifier.cache` (`OKF_CLASSIFIER_CACHE`, off by default) keeps the classifier's answers in `.llmw2/cache/classifier.sqlite` of the bundle (SQLite in WAL mode, shared by threads and processes), under the sha256 of the request (address, model, state and questions). Only hashes and answers are stored, never text or errors. An answer served from it is counted as `served` in the usage (`Usage.served`, also per step), not as a call, with no tokens and no cost. It pays on a rebuild, a resumed sync and a repeated audit, not on a sync of new documents. `wiki.clear()` deletes the file; `rebuild` keeps it. `Classifier` takes a `cache` with two methods, `get(key)` and `put(key, answers)` (`AnswerCache`), so a shared store can replace the file; the library guide shows a Redis adapter, and no Redis code ships.
- `LocalFolderSource` hands its changes over in pages of 200 (`more=True`), and its cursor after a page is the listing with the pages handed over applied, so `sync` saves it per page: a sync of thousands of documents that stops resumes at the first page not finished and redoes at most one. Cursors saved by earlier versions still read.
- `Wiki.rebuild(items)` files the documents again on the folders the wiki has: notes, raw copies, archived versions and sync cursors go, the folders stay with their descriptions, and no `plan` is made, so with the same classifier answers the notes go where they were. The folders are fixed: a path score under `tau_path` files the note in the best path's folder without the routing fallback to the model, "New subfolder" counts as "Here", no folder is created (`name_folder`, a step's `create`) and no folder is split. `Wiki.ingest_many` takes `plan=False` for it, and `WikiStore.clear(keep_folders=True)` empties a wiki and leaves its folders.
- `max_top_folders` (`OKF_MAX_TOP_FOLDERS`, 10 by default, at least 1) sets how many top-level folders a wiki may have. The plan prompt is told the limit, `plan_groups` opens no top-level folder over it (the documents of a group left out are routed as usual), and routing stays within it.
- `WikiStore.write_note` takes `overflow`, which marks a note the limit on top-level folders deviated (filed in the likeliest existing folder instead of a new one) in its frontmatter: the classifier's `p_none` and `p_root`, and `at`.

- A note the limit on top-level folders deviated is never silent: its frontmatter has `overflow` in every mode (also when the folder's words chose, then with `at` only), `log.md` has a "Deviated note" line with the note, the root it went to and the probabilities, and `IngestResult.overflow` carries them. `BatchReport` and `SyncReport` list the deviated notes (`deviations`: note, root, `p_none`, `p_root`) and count them (`deviated`, in `to_dict()` too); `llmwiki2 ingest`, `sync` and `rebuild` print them, and the MCP `remember` returns them when there are any. In a rebuild, a score under `tau_path` that sends a note to the likeliest top-level folder is marked as well.
- `llmwiki2 rebuild [folder] --yes` and the MCP tool `rebuild(confirm)` expose `Wiki.rebuild`. The CLI files the documents of `folder`, or with no folder the wiki's own raw copies (`Wiki.raw_items()`, read before anything is deleted: an origin from its latest copy, a copy with no origin once per text); without `--yes` it only says what it would do and exits with 2. The tool does nothing unless `confirm` is true and runs as a job like `remember` when the client does the model work. The documents keep their origins, so a `sync` of the same folder afterwards files nothing again. `sources.documents(connector)` lists what a connector has as items.

- Managed top-level areas. The wiki never opens or proposes an area by itself: proposals come from users, and the deviated notes help to compose them. `Wiki.propose_area(label, description, notes)` queues a `Proposal` (`id`, `label`, `description`, `notes`, `origin`, `created`, `status`) in `.llmw2/proposals.json` of the wiki, which survives between syncs and reopening; `proposals(status="open")` lists the queue and `dismiss_proposal(id)` closes a proposal. `confirm_proposal(id, over_limit=False)` creates the root (counted in `max_top_folders`: `InputError` at the limit, unless `over_limit`) and moves into it only the proposed notes still in the wiki, writing `moved_from` and dropping `overflow`, with no rebuild and one "Confirmed area" line in `log.md`. `Wiki.create_area(name, description)` is `create_folder` at the top level, refused over the limit.
- `Wiki.rename_root(path, new_name)` renames a top-level folder and `Wiki.merge_roots(source, target)` moves every note and subfolder of one into another (notes with `moved_from`) and deletes it; links to and from the notes follow, descriptions stay, and each is one line of `log.md`. `WikiStore.move_folder` and `merge_folders` are the primitives, and `move_note` takes `mark`, `log_it` and `root`.
- The same areas in the CLI (`llmwiki2 proposals [add|confirm|dismiss]`, `rename-root`, `merge-roots`), the HTTP API (`GET /proposals`, `POST /proposals`, `POST /proposals/{id}/confirm` and `/dismiss`, `POST /areas` (`over_limit` to pass the limit), `POST /folder/rename`, `POST /folder/merge`), an **Areas** section on the page (create an area, confirm or dismiss proposals, rename, merge) and four MCP tools: `create_area`, `propose_area`, `proposals` and `confirm_area(id, yes)`.

- `fields` kept apart and filterable. A document is never merged into a note when their `fields` have a key in common with different values (any key, no setting): the notes that conflict leave the pool of the `match` step, in classic and CROW, and the document becomes a new note or merges into another that agrees; a trace line (`match`, "N excluded by fields") appears only when a note was left out. `Change` takes `fields`, `sync` passes them to the documents, and `LocalFolderSource(folder, fields=lambda rel: ...)` sets them from the path. `Wiki.ask(question, where=...)`, `POST /ask` (`where`), the MCP `recall(question, where)` and `llmwiki2 ask --where key=value` (repeatable) restrict the answer to the notes whose `fields` hold every pair, compared as text (scalar values); `where` is a filter, not an access control: the page, See-also links and `index.md` show every note.

- Who made a change, in `log.md`. `Wiki(cfg, author=None)` and an `author=` keyword on the methods that change the wiki (`ingest`, `ingest_file`, `ingest_many`, `rebuild`, `mark_removed`, `sync`, `create_folder`, `create_area`, `describe_folder`, `move_note`, `propose_area`, `confirm_proposal`, `dismiss_proposal`, `rename_root`, `merge_roots`, `delete_note`, `delete_folder`, `clear`, `audit_routes` with `apply`, `plan`, `plan_groups`): every log line they write ends with ` · by <author>` (one line, without `*`, `[`, `]`, `(`, `)`, at most 60 characters). The call's author wins over the `Wiki`'s, and it holds for the call, not for the object, also through the lanes of a batch, so threads sharing one `Wiki` (the server's) never mix theirs. `propose_area` and `dismiss_proposal` now write a "Proposed area" and a "Dismissed proposal" line in `log.md`, as "Confirmed area" does. Without an author the log is written as it was. The CLI uses the login name (`getpass.getuser()`) for the commands that change the wiki; HTTP takes the `X-Author` header on every request that is not a read; the MCP memory takes `X-Author`, else the client's name from the handshake (`clientInfo.name`), else the product of its `User-Agent` (the memory answers statelessly, so the handshake name is seldom there). The author is **declared, not verified**: there is no authentication and no signature, and nothing goes in the frontmatter; behind a reverse proxy that authenticates users, have it set `X-Author`. `taxonomy_health`, `last_split_at` and `check` read the log the same with and without the suffix.

- Documentation: "More than one wiki" in the memory guide (one MCP server per wiki added to the same client, with the `claude mcp add` and `codex mcp add` commands for two) and "Asking several wikis" in the library guide (`ask` on each wiki and compose the answers), with the limits: no common ranking and no permission model between wikis, and `where` is a filter, not an access control. The library guide also says how a service's delta API maps onto `SourceConnector` (the delta token as the cursor, a page per `ChangeBatch`, removals as `Change("remove", ...)`), that a folder synced by a desktop client works as a `LocalFolderSource` while the bundle itself should not live in one, and why there are no ready connectors for SaaS services.

### Changed

- `Wiki.ingest_many` (and so `sync` and `rebuild`) is one session of the store (`WikiStore.session`): the tree is read whole once, then only the folders whose modification time changed, the same for `raw/` and the index of origins, and the See-also candidates by rare values come from an inverted index kept between documents. The time of the library per document stays almost constant as the wiki grows. The notes and the files written are the same.
- The store never overwrites a note whose file changed since it read or wrote it (`Note.stamp`: modification time, size, inode). The links between notes, the refresh of See-also entries and the rewriting of links read the file again and apply to what is there, so a hand edit during a batch is kept; a document that must merge into a note changed meanwhile is not filed (no raw copy, no note): it is a `NoteChanged` result (an `InputError`), counted in `BatchReport.conflicts`, listed in the errors of a `sync`, and filed by the next run.
- `WikiStore.load` reads a note again only when its file changed (modification time, size or inode, and always when written in the last two seconds): it keeps what it read of each note and hands out a copy, the frontmatter is parsed with libyaml when installed, and `OriginIndex` walks the notes only to answer `citing`. The time to file a document grows far more slowly with the size of the wiki; the notes and the files written are the same.
- The plan prompt for a wiki that has folders (`librarian/plan_more`) is told the top-level areas are fixed when the wiki already has `max_top_folders` of them: it no longer proposes a new one, and puts every new folder under the area it belongs to. Below the limit the prompt is as before. Top-level folders made by hand with `create_folder` count in the limit, so as many as the limit closes the wiki to new ones; the docs now say how to give your own areas.
- `Wiki.ingest_many` (and so `sync`) in CROW mode, on a batch of more than one window of 50 documents and without `plan: groups`, now reads and plans every window first, and only then summarizes and files them, on the folders of all the plans: a document is routed among the folders the whole batch needs, not only among those made by the windows before it. A plan is shown how full each folder would be (the folder BM25 ranks first for each document of the windows already planned, by its title and opening against the folders' names and descriptions: no model is asked) in place of the notes that do not exist yet. Once planned, the text of the documents read waits in files of a temporary folder (removed when the batch ends, however it ends) and only their opening stays in memory; it is read back when the document is summarized. A batch, finished or stopped (a spending cap, a failing model), takes back the folders of its plans (the writer's, or the groups of `plan: groups`) that are still empty (the deepest first, "Removed folder" in `log.md`) and leaves the notes filed so far with their folders: a folder with a note, or one that was there before, stays, so a run stopped and resumed plans on the tree a run never stopped would have found. A batch of one window, `plan: groups` and the classic mode are unchanged.
- A CROW batch that finished now tidies the folders its plans made (the writer's, or the groups of `plan: groups`), the deepest first, with no model call, no setting and no threshold. A folder below the top level with one note and no subfolder gives the note to the folder above (links and archived copies follow, a "Moved" line in `log.md`) and is removed ("Removed folder"); a top-level folder with no note of its own and a single subfolder, which a plan made too, takes the subfolder's notes and subfolders, and the subfolder is removed (a "Moved folder" line), while the top-level folder keeps its name and description. Folders that were there before the batch, folders routing made and top-level folders with one note stay. The batch's results give each note where it went. A stopped batch does not tidy, so the next run plans on the tree it would have found; the classic mode is unchanged.
- A wiki has at most `max_top_folders` top-level folders (ten by default): `plan` and `plan_groups` make no more, `plan` leaves out a top-level folder it proposes beyond the limit, with its subfolders, and a note whose route would open one (the per-level routing, its fallback, `name_folder`, a custom `route` step's `create`) is filed in the top-level folder the classifier's flat Choice gives most probability to, None of these left out (by BM25 on the folders' names and descriptions when the classifier fails). No model call is added.
- `Answer.citations` (and so `/ask` and the MCP `recall`) now counts every note path in a bracket that holds several, separated by `;` or `,`: `[a/x.md; b/y.md]` cites both. A bracket with any other word is still no citation. `Answer` gains `titles`, each cited path with the note's title, which `/ask` and `recall` carry; in the page each path of such a bracket is its own link, labelled with the note's title.
- With `revisions: replace`, a note takes the title written for the new version of its document, as it already took the summary. Its file keeps its path.
- A source that came from a file is shown to the writer by its file name, on a line `File name: q3-board-minutes.md`, in the prompts that summarize it, instead of a `Source title` made from that name. A text given a title by the caller is shown as before, and `Source.title` keeps its value (revisions and fallback folder names read it). Spreadsheet sheets and the plan prompts are shown as before.
- The prompt that names a new folder no longer gives example names for the top level, so a writer has none to copy; the rule (the broad area, never the specific topic) stays.
- The prompts of the plan (`plan`, `plan_more`) say that a single client, supplier, project or product is never a top-level folder, however many documents are about it, but a subfolder of the broad area it belongs to.
- The first plan of an empty wiki (`plan`) says that a folder with subfolders has at least two, which together hold all of its documents: a subject with no second one beside it stays in its parent. Plans now lay out far fewer folders with a single subfolder, which drew the rest of their area's notes into the parent.
- A note's links that lead nowhere become plain text when the note is written: `[text](x.md)` to a file that is not in the wiki folder becomes `text (x.md)`, and `[[name]]`, `[[name|label]]` and `![[name]]` become the name or the label. Links that resolve, web and mail links, code and raw copies are left as they are.

### Fixed

- On Windows, `WikiStore.lock()` also holds across processes (a byte lock on `.llmw2/lock`, retried until free), as `flock` does elsewhere, so a `sync` and a server writing the same wiki no longer lose a line of `log.md` or `index.md`.
- On Windows, a note, `index.md` or `log.md` that another process is replacing at that moment (a `remember` of the MCP during a `sync`) is read once it is there, waiting up to about 0.3 s, instead of stopping the batch with `PermissionError`.
- When a merge or a revision changes a note's title or summary, the See-also entries of the other notes that link to it show the new ones; the links and the rest of those notes stay as they were.
- A sync now recognises a file that was filed on its own (`ingest_file`, or `ingest` of a file on the command line): when its text is the text of a raw copy with no origin that a note cites, the copy takes the sync's origin and the file counts as unchanged, instead of being filed again as a second note. Its text and hash stay.
- `check` no longer reports a broken link written inside a fenced code block (```` ``` ```` or `~~~`) or inline code.

## [0.7.0] - 2026-10-28

### Added

- The wiki is the long-term memory of Claude Code, Codex or any MCP client, documents already filed included, with the `mcp` extra (`pip install 'llmwiki2[mcp]'`; the Docker image has it): the server answers at `/mcp` (Streamable HTTP, stateless, plain JSON replies, through the official MCP SDK). `recall` answers from the wiki as **Ask** does, citing its notes; `remember` files a text like a note written on the page, through CROW (routed, merged and linked); `read_note` reads one note. It is off by default: **Memory** in the page's header starts and stops it (the setting `mcp`, or `OKF_MCP=1`) and shows the `claude mcp add` and `codex mcp add` lines to copy. While it is off, its tools answer how to turn it on, so a client added before Start needs no reconnecting. `/mcp` sits behind the server's guard (a loopback Host when bound to localhost, no cross-site request); without the extra, it answers 501 with what to install, and the page says the same.
- The client can do the memory's model work, on its own plan, with no key in the wiki: Claude or GPT for everything (the setting `mcp_models = "client"`, or **Claude or GPT for everything** in the Memory page's **Which models do the work**). CROW runs as always, but each model call is handed to the client as a request (`models/relay.py`), and the tool `reply` hands back the answers until the result comes. A `decide` request is the System One questions with the typed-decision prompt of TypeSafe's system-one-adapter (MIT, see NOTICE; `models/typed.py`), which Claude Code runs with a Haiku subagent; a `write` request is the wiki's own prompt, for the client's model. The decision prompt ends with the reply's shape keyed by the question ids, and a reply is read as a light model writes it: a lone answer keyed by its question's text, a reply cut before its last brackets (closed for decisions only, not for the writer's replies), percentages (a value above 1, none above 100), or a choice given as one of its labels (that label, for sure). Any other value outside 0 to 1, or anything else, falls back to the LLM; nothing is clamped. The requests carry the documents' text: the client is told to read it as data and to answer with text alone, using no tool. A request left unanswered for 5 minutes fails the work, and `log.md` names `mcp-client` as the model of what the client filed. `POST /settings/memory` turns the memory on or off and switches its models with no key check.
- New setting `plan` (`OKF_PLAN`): `"writer"` (the default) or `"groups"`. With `"groups"`, `Wiki.ingest_many` (and so `sync`) in CROW mode on a wiki that has no folders when the batch begins plans every window in code, with no model call, instead of asking `librarian/plan` (`Wiki.plan_groups`), with any provider. It is meant for a small local writer (about 1B parameters or less), which plans poorly; a capable writer plans better than the groups. The groups: the documents that share a subject (TF-IDF over their whole text, cosine similarity, average-linkage clustering) are grouped; a group whose documents resemble the sources of an existing folder's notes goes there, any other to a new top-level folder named after the two words that weigh most in its documents and described by the titles of its first three (a group whose words a folder name cannot spell is routed instead). The documents of a group are filed in its folder without routing (decision `route` by `code`); a document in no group is routed as before. Folders made this way end their "Created folder" line with `, by plan`. A wiki that has folders when the batch begins still gets `librarian/plan_more`, and with `"writer"` the writer's `librarian/plan` plans as before.

### Changed

- A `custom` classifier whose model is not one of Laya's checkpoints (`typed-decisions`, `auto`, `english`, `multilingual`), such as a small classifier on Ollama, merges at `tau_cons` 0.7 instead of Laya's 0.26, the value it was measured with. Laya keeps its preset; a `tau_cons` set by the user still wins.
- `librarian/summarize` shows what each field of its JSON reply should hold (`"<a specific title written from the content>"`, `"<one sentence: what the note answers>"`, ...) instead of `"..."`, for every writer.

### Fixed

- A note no longer vanishes when its summary or a field holds a `---` line (the frontmatter ended there, so the note failed to parse and left the tree and its index). A byte order mark no longer hides the frontmatter, and form feeds and U+2028 in a body stay as written. Text written by hand after a note's See-also list is kept when links change: it moves above the list, which stays one section. A note that is not UTF-8 is skipped with a warning and named by `check`, instead of stopping the whole wiki; an `index.md` or `log.md` saved as Latin-1 is read as such and written back as UTF-8.
- A model failure in `relate`, which runs once the note is written, no longer fails the ingest: the note is filed and logged without See-also links, with a `relate` / `error` decision in the trace. Before, the note was left on disk with no log line, the sync stopped, and the next sync skipped it, so the links never came. `{"related": null}` reads as no links, and `"/a.md"` and `"a.md"` count as one link.
- A merge no longer loses the incoming note when it does not fit what is left of the call after the existing note: it was cut to that room (to nothing for a note that filled it), the model merged what was left, and the note was logged as merged. Now no model is asked: the incoming body is added to the note as a section headed by its title, and the tags are joined.
- With a writer on Ollama, a reply to summarize, merge or name_folder that is invalid, cut, or refused by its check no longer stops the ingest: the note keeps the source text (decision `summarize` / `verbatim`), the incoming note is added to the matched one as a section (`merge` / `appended`), or the folder is named after the note, from the first words of its title, summary or file name (`notes` if none has words), with the summary as description. Each is logged as a warning. An endpoint that fails (server down, wrong model, timeout) still stops it, and with any other provider a reply error is raised as before. Ollama's 500 "token repeat limit reached", sent when it stops a model that keeps repeating itself, is an invalid reply after the usual retries, on every provider: in a batch it is that document's result, not an endpoint failure.
- The Ollama preset sends `reasoning_effort: "none"`, and `"low"` once if the model refuses it, as the OpenRouter preset does. Before, a thinking model thought on every call, and every capped reply got the reasoning room on top, which a small model that ran away wrote to the end. When the caller's own `extra_body` is that off switch, an Ollama reply's cap gets no reasoning room; with other providers the cap is computed as before.
- A reply whose first JSON object does not parse is no longer read through an object nested in it, which took a broken plan for one of its folders, or for none: the broken object is skipped whole. A plan or split reply without `folders` is an invalid reply and is retried, not an empty plan; `{"folders": []}` still means none. In classic mode a `find_match` reply broken on the retry too is now an error, where a nested object could read as no match.
- A local classifier server that takes at most 64 questions per request, or whose context a batch exceeds, no longer fails it: when it answers that the questions "must contain 1-64 fields", or that the "context size has been exceeded", the batch is asked again in halves, and halves of those if needed; a single question that exceeds the context still fails. No other request changes.
- With a writer on Ollama, a note's title that is only its source's file name, numbering included (`26 new hire checklist` for `26-new-hire-checklist.md`, or a file name ending in `-md`), or that has no letter or digit in any script, is replaced by the source's first heading, unless that is one too; a summary with no letter or digit (`...`) by the first line of the note's text; and a title or summary made only of words of the summarize prompts (the reply format copied) by the source's or the note's first lines. Each is logged as a warning. A hosted writer's title and summary, and a source filed verbatim (`OKF_SUMMARIZE=false`), are kept as before.
- With a writer on Ollama, a folder name or description copied from the prompt rather than written from the documents (the reply format `/area/subject`, the naming rule, an example such as `engineering`, file-name codes, the words of the parent's path) is not used: a folder of a plan or a split so named or described is left out, a `name_folder` name gives way to the first words of the note (never words only of the prompts), and a `name_folder` description to the note's summary. Documents in a script that folder names cannot spell (Cyrillic, Chinese...) leave nothing to compare with: names and descriptions are taken as before. A hosted writer's names are taken as before.
- With a writer on Ollama, a `route_fallback` call that fails no longer stops the ingest: the note goes where the classifier's best path ended, or into a new top-level folder when there is none (decision `route` by `code`, logged as routed by fallback). With any other provider it is raised as before.
- With a writer on Ollama, the retry of an invalid JSON reply asks for the reply's own JSON schema (`response_format` of type `json_schema`), which Ollama holds the model to, instead of any JSON object. The first request and other providers are unchanged.
- A reply holding a lone UTF-16 surrogate (`\ud8a1`, written as text or as a JSON escape) no longer stops the batch when its note is written as UTF-8: each one becomes U+FFFD.

## [0.6.0] - 2026-10-19

### Changed

- CROW filing is one classifier Choice among every folder of the tree (and `None of these`), not a walk level by level. The per-level routing, with its beam and votes, decides only for an empty tree, on `None of these` or on a classifier error; `tau_route` now also decides whether the second folder's branch joins the match pool, and `votes` applies to the per-level routing only. When one request cannot hold every folder, the BM25 cut applies to this Choice too (decision `route_cut`). New decisions: `route` (the folder picked), `route_direct` (`None of these` or an error) and, with its own prompt, `classifier/route_direct`. Measured on four corpora, two writing models, an empty and a full wiki: 0.74 to 0.94 times the cost per document, 1.2 to 2.5 times the agreement with the real folders, retrieval on a par (about 96% for both), and 14% to 53% of the notes below the first level against 0% to 13%. One run per configuration, and retrieval is near its ceiling.
- `Wiki.ingest_many` (and so `sync`) in CROW mode files in windows of 50 documents. Before a window is filed, when two or more of its documents are to be filed, an LLM call lays out the folders they need (`librarian/plan` for an empty wiki, `librarian/plan_more` for one that has folders: only new folders, never a rename, a move or a new description). A plan that fails is logged and the window goes on with the folders it has. The window and the plan's limits are constants, not settings. `Wiki.plan` can be called on its own and raises on a bad reply.
- After a document filed alone (`ingest`, `ingest_file`, `/ingest`, `/upload`, a window with one document), CROW tries once to split its folder when it holds 12 notes directly, then at 24, 36 and so on: one LLM call (`librarian/split`) opens subfolders for the subjects at least two of the notes share and moves those notes there. The attempt is written in `log.md` as a "Split" line, also when it opens nothing, so a new `Wiki` does not try the same multiple again. A split never fails the ingest; its decision is `split`. Without a batch to plan, it takes the notes below the first level from 0% to 30%-44%, with agreement and retrieval within a few points of what they were. The limit is a constant, not a setting.
- `audit_routes` (`llmwiki2 audit`, `POST /audit`) asks the same Choice among every folder as the filing, instead of the per-level routing, and proposes only a move to another branch: a folder below the note's own, or above it, is no longer a proposal. `--min-confidence` (0.7) is compared with the Choice's top probability, a scale that has not been recalibrated.
- `taxonomy_health` (`llmwiki2 health`, `GET /taxonomy`) does not count folders laid out by a plan, a plan_more or a split as new subfolders: their "Created folder" lines end `, by plan`, `, by plan_more` or `, by split`.
- CROW routing on a level with more subfolders than one classifier request holds (255 options for Jev, the `request_chars` of a classifier with a smaller context) offers the ones a BM25 ranking against the note puts first, as many as fit, instead of failing over to the LLM. A level that fits is asked as before. With Jev on a level of 500 and 1,000 folders the right folder is among those offered for 51 and 49 of 51 documents, and the folder taken is right for 38 and 37, as with about 250 folders all offered at once; asking every folder in groups and a final costs two to four times as much for the same result.

### Fixed

- The Docker image builds again: `.dockerignore` lets in `LICENSE` and `LICENSES/`, which the package build needs; the README and `docker-compose.yml` no longer point to `/docs`, which the server turns off.
- Emptying the wiki removes the sync cursors too, so a `sync` afterwards files every document again instead of none. Deleting a note or a folder, or emptying the wiki, also removes the archived versions of the notes deleted (`Deleted.archived` lists them); the confirmation on the page says so.
- Saving settings with a wiki folder that cannot be made (a file, or not writable) is refused with 422 and saves nothing; before, the bad folder was saved and the server failed on it, then and at every start. A missing key is refused before the folder is made. `~` in `OKF_BUNDLE`, `OKF_USAGE_LOG`, `OKF_PROMPTS_DIR` or the wiki folder on the settings page is the home folder, not a folder named `~`; a usage log's folder is made when it does not exist.

## [0.5.0] - 2026-10-16

### Added

- Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`), OpenDocument text (`.odt`) and spreadsheets (`.ods`), read with the standard library only (no new dependency): documents become Markdown with their headings, lists, tables, footnotes and speaker notes; the type is read from the content, and a damaged or hostile file (zip or XML bomb, broken XML) is an input error that does not stop a sync. A slide's heading is its title, `## Results (slide 4)`, so the summary slide of a long deck can be used as its summary; a title repeated on the next slides is not repeated, slide numbers and dates are left out and footers come once at the end. Tables of contents are left out, fill-in lines (`______`, `.......`) become `___`, and an ODT "heading" over 80 characters or with a blank to fill is a paragraph. Excel numbers keep 15 significant digits (`0.0551`, not `5.5100000000000003E-2`), whole numbers all their digits, and percentages read as such (`5.51%`).
- A spreadsheet is filed one note per sheet: the note describes the sheet (written from its first rows), the sheet's rows are kept as CSV in the raw copy (title rows above the header become a `Caption:` line, which the note's writer reads too), and the researcher adds to a sheet's note the rows a question needs (the whole sheet when it fits, otherwise the closest rows and the totals of its numeric columns). The notes of one workbook link to each other; a sheet deleted from the workbook is marked removed. `IngestResult.parts` holds the other sheets' results.
- The local folder source skips Office lock files (`~$name.docx`).
- `benchmarks/office/`: where the 2,629 real Office/ODF files of the readers' bench come from (NapierOne, Common Crawl), with their sha256, the scripts that fetch and select them, the free check between two versions and the criteria of the measures; the files themselves are not committed.

### Changed

- `Wiki.ingest_many` (and so `sync`) reads spreadsheets first, on worker threads, and reports one item per sheet (`"plan, Budget.csv"`); sync counts are per sheet. A workbook has no record under its own origin key (`Wiki.origin` returns None for it), so a sync that lists it fetches it again; sheets whose text is unchanged cost no model call.
- CI runs the tests in parallel (`pytest -n auto`, with `pytest-xdist` among the dev dependencies): the Windows jobs had taken the CI from about 3 to about 8.5 minutes.

### Fixed

- One unusable model reply no longer stops a sync: a document whose reply stays invalid after the retry (malformed JSON, no content, cut at the limit) is that document's error, the rest of its page is filed, and the sync stops at the end of the page without saving its cursor, so the next run retries that document alone (`llmwiki2 sync` still exits with 2). Two in a row still stop at once, as a model that cannot answer. New `ReplyInvalid` (a `ModelError`; `ReplyCut` is one).

## [0.4.4] - 2026-10-15

### Fixed

- Saving settings on Windows with Python 3.11 and 3.12: "Save and start" stopped with `module 'os' has no attribute 'fchmod'` (Windows has `os.fchmod` only from Python 3.13). The file is now made owner-only with `os.chmod`, and a failed save no longer leaves a temporary file behind.

### Changed

- CI runs the tests on Windows too, with Python 3.11, 3.12 and 3.13.

## [0.4.3] - 2026-10-14

### Changed

- `pip install llmwiki2` is the whole install: the web page and the HTTP API (`fastapi`, `uvicorn`) are in the base dependencies, so `llmwiki2` and `llmwiki2 demo` start without an extra. Before, they stopped with `No module named 'fastapi'`. `llmwiki2[server]` still installs, now the same as `llmwiki2`.
- README: two steps, `pip install llmwiki2` first and uv as the alternative for those without Python or who want it kept apart.

## [0.4.2] - 2026-10-14

### Changed

- The README, which PyPI shows: a demo GIF, a "Why not RAG?" section, a FAQ (what runs locally, what Jev is, whose benchmarks these are, why AGPL and a CLA) and the pip install next to uv.
- Releases: the GitHub release is created by the release workflow after PyPI, with the version's CHANGELOG section as notes and a copy of the CLA signatures attached. To release, push the tag only.

## [0.4.1] - 2026-10-14

### Changed

- Published on PyPI as `llmwiki2`, the same name as the command: `uv tool install "llmwiki2[server]"`, or `uvx "llmwiki2[server]" demo` to try it. The module is still imported as `llmw2`.

### Fixed

- NOTICE: the OKF files are licensed by Google LLC under the Apache License 2.0, not AGPL-3.0.

## [0.4.0] - 2026-10-12

### Added

- Move a note to another folder: `wiki.move_note(path, folder)`, `llmwiki2 move NOTE FOLDER`, `POST /note/move` and a Move button on the note page. Every link to and from the note (See-also entries and Markdown links) is rewritten, the indexes of both folders too, and archived copies get the new `archived_from`. Edit a folder's description: `wiki.describe_folder(path, description)`, `llmwiki2 describe FOLDER "DESCRIPTION"`, `POST /folder/describe` and an Edit description button in the sidebar.
- Long documents with the LLM alone. A source that fits in one call is summarized whole, as before; a longer one is read in parts: `librarian/summarize_part` takes notes on each block in turn (plain text, the facts first) and `librarian/summarize_combine` writes the note from them. `split_blocks(text, max_chars)` in `llmw2.agents.state` cuts at sections, then paragraphs, then sentences, losing nothing. A document is still one note. The notes of a part over their share are cut at a sentence end, and a part cut by the output limit keeps what it wrote. A warning is logged when the note lacks values (codes, numbers, two-word names) of its part notes. A part that fails fails the step.
- `read_chars` (`OKF_LLM_READ_CHARS`, "Characters per call" in the settings page, kept per provider): the characters one request may hold. Empty means automatic: 100000 on OpenRouter, OpenAI and Gemini, 16000 on Ollama and custom servers (`Provider.read_chars`, `WikiConfig.effective_read_chars`).
- `note_chars` (`OKF_NOTE_CHARS`, "Note length" in the settings page): about how long a note body may be. Empty means 10000, and at most half of what one call holds. It is asked of the model, never enforced: a body over 1.2 times the length asked is logged, not cut.
- `note_length` (`OKF_NOTE_LENGTH`): `proportional` or `fixed`. A proportional note is about a quarter of its source (`NOTE_RATIO`), never below `note_chars`, up to what a call holds and `NOTE_MAX` (45000); a merge asks for the existing note plus that share (`WikiConfig.target_note_chars`). `fixed` is a note of about `note_chars`.
- `revisions` (`OKF_REVISIONS`): `replace` or `merge`. A source that revises the one document a note cites replaces the note (a summary of the new version, a "Changes from the previous version" section with each changed value and "previously ...", and a "From the previous version" section with the sentences that still hold), and the old body goes to a hidden `.archive/` (`WikiStore.archive_note`, `previous_version` in the frontmatter). When a question asks about an earlier version, the researcher reads the archived copy and answers with the earlier value. When the guard does not pass (not the same document, or a body not written by code alone), the source is merged as usual. The guard is recorded as a decision whose `decider` is the new value `"code"`.
- The web page of a note revised in place has a Versions block that links its archived version; an archived copy is labelled as such, links the current note and the version before it, and has no delete button.
- Every note records `generated.body_hash`, the hash of its main body, on create and on every update.
- `votes` (`OKF_VOTES`, default 1) and `vote_band` (`OKF_VOTE_BAND`, default 0.05) for CROW. With `votes` above 1, a routing decision close to `tau_route`, to a tie between two folders or to `tau_path` is asked `votes` times and the classifier's answers are averaged, so its noise flips the decision less often between runs. The `route_step` trace ends with `(n votes)` when it was averaged.
- `fallback_menu` (`OKF_FALLBACK_MENU`, `visited` or `tree`, default `visited`) for CROW, an extension of the paper. With `tree`, the routing fallback shows the LLM the folders the classifier visited and then the other folders of the wiki (at most 8000 characters; the visited ones are never left out), so it can pick a folder the classifier never reached. It is in the settings page next to `votes`.
- A deterministic state for routing. CROW `route` reads a digest built from the source text (the text whole if short; else its summary or introduction, outline and excerpts) instead of the LLM-written summary, which changes on every run. `Candidate.state` holds it and a custom `summarize` may fill it; `StepContext.route_state(cand)` returns it. For a source longer than twice `note_chars` the digest leaves out the source's own summary. `StepContext.candidate` is the note `summarize` wrote, set before `route`.
- An output cap on every model call made through `StepContext.ask_json` and `ask_text`, after a model ran away to 131072 tokens. Both take `reply_chars` (default `note_chars`; decisions use 2000), and `LLM.chat`, `json` and `complete` take `max_tokens`. With `keep_cut`, `ask_text` and `LLM.complete` return a reply cut by the cap as written, with a warning, and append it to `cut` when given. `LLM.chat` takes `json_mode`, which asks for `response_format` `json_object`. `reply_tokens(chars)` is 3 times the length at 3.5 characters a token, at least 1024, plus 32000 when the model may think. The field is `max_completion_tokens` on OpenAI (`Provider.max_tokens_field`); a provider that refuses a cap is asked again without it, and an `extra_body` cap wins.
- `llm.seed` (`OKF_LLM_SEED`) and `llm.pin_provider` (`OKF_LLM_PIN_PROVIDER`, comma-separated), both on the settings page under "Model parameters". The seed is sent with every request. On OpenRouter the pinned providers answer in order, with no fallback to others and only if they support every parameter sent; other providers ignore the pin with a warning. An `extra_body["provider"]` wins. Neither is set by default.
- Every line of the usage log records the `provider` that served the call (when the response says), the `temperature`, the `seed` and the `finish_reason`.
- `Prompts.fingerprint()` and `decision_hash(steps, cfg, prompts)` in `llmw2.agents.steps`.
- The settings page warns, when testing an Ollama model, if its `num_ctx` is smaller than a call can need.
- A stable `id` in the frontmatter of every new note (12 hex characters). Updates, moves and archived copies keep it; older notes get none; it is never used in links or indexes. `Wiki.find_note(id)`; `check` reports two notes with the same `id`.
- Routing audit (CROW): `wiki.audit_routes(min_confidence=0.7, apply=False)`, `llmwiki2 audit [--min-confidence X] [--apply]` and `POST /audit`. Routes each note of the tree again with `route`'s classifier decisions and never its LLM fallback, and proposes the notes whose folder differs, not deeper in the note's own branch, with a confidence of at least `min_confidence` (default `AUDIT_MIN`); `apply` moves them. No LLM call, never automatic. It returns `RouteProposal`s; `taxonomy_health` below returns `FolderHealth`s. All three are exported from `llmw2`.
- Taxonomy health: the CROW ingest log line ends `, routed by fallback` when the LLM fallback chose the folder, and `wiki.taxonomy_health(min_filed=5, max_share=0.3)`, `llmwiki2 health` and `GET /taxonomy` read `log.md` (no model) for the new subfolders and fallbacks per folder and month, flagging the folders with too many.
- Benchmarks in `benchmarks/` (stability of the filing decisions, filing errors judged blind by readers, facts that survive into long notes, replay of model answers), the corpora they use and the stored judgements, with a README.

### Changed

- **Behaviour change:** the confidence of a classifier Choice is computed by the library from the probabilities, on the scale the CROW thresholds are calibrated on (`llmw2.models.classifier.confidence`), and the server's `confidence` field is ignored. Servers disagree on what that field means (Ollama and Laya send one measure, some OpenRouter providers another), so the same answer took different decisions on different servers; with a classifier on Ollama, CROW never merged an update. One set of thresholds now works on every server. On Jev nothing changes but a decision exactly at a threshold: 27 of 17,558 stored answers, none a merge. A server answer without `confidence` is no longer malformed.
- **Behaviour change:** Laya's preset `tau_cons` is 0.26 instead of 0.05: the same merges on the new scale. A custom classifier's `tau_route` (0.6) is now read on Jev's scale too; if you set `OKF_TAU_CONS` or `OKF_TAU_ROUTE` for a custom server, set them again on it.
- **Behaviour change:** `revisions` defaults to `replace`: a source that revises the document a note cites replaces the note and archives the old body. `OKF_REVISIONS=merge` keeps 0.3.0's merge.
- **Behaviour change:** `note_length` defaults to `proportional`: a note grows with its source, to about a quarter of it. `OKF_NOTE_LENGTH=fixed` keeps 0.3.0's length.
- **Behaviour change:** every note now records `generated.body_hash`.
- **Behaviour change:** the decision hash changes: runs before this one hash differently. It now also covers `read_chars`, `note_chars`, `note_length` (unless `fixed`), `relate_rare_values`, `answer_conflicts` and `strict_new_folders` (only when `false`), the size and version of the routing state, the fallback menu's character limit, `SUMMARY_VERSION`, the seed and the pinned providers, and the text of `note_rules` and `merge` changed. The ingest log line ends with `(name hash, model)` instead of `(name hash)`, plus `, via providers` when providers are pinned.
- **Behaviour change:** a reply cut by the output limit is asked once more, shorter; if it is cut again, the call raises `ReplyCut` (a `ModelError`) and the source fails, where before a long note was filed with a warning (or ran away). Direct `ctx.llm` calls stay uncapped, and the cap is not in the decision hash.
- `summarize` reads a source that fits in one call without a length rule, and its output cap follows the source length. The length rule of `shared/note_rules` is the placeholder `{{length_rule}}`, left empty by `librarian/summarize` and `librarian/merge_source`.
- CROW `route` and `consolidate` read the source, not the LLM-written summary. `route` reads the deterministic state; `consolidate` reads it for a source up to twice `note_chars`, and the candidate's compact form for a longer one.
- `merge` goes from note to note: it reads the existing note and the incoming note (the candidate, with its source's title and location), so it fits the call however long the source is, and keeps the existing note's title, summary, headings and wording where nothing changed. A source longer than twice `note_chars` is merged from its text (`librarian/merge_source`). If the draft is unchanged, or keeps fewer than half of the new values (codes, numbers, names) of the incoming text, it is asked once more from the source; if it drops more than 10% of the values of the existing note, it is asked once more naming up to 80 of them with the newer value next to each ("previously ..."). Its output cap is the existing note plus the share the source adds. A step that calls `classic.merge` without a `ctx.candidate` still gets the source, cut to what the call can hold.
- `source_chars` is deprecated (a `DeprecationWarning` when it is set) and is no longer a cut: when `llm.read_chars` is not set, an explicit `source_chars` is the length above which `summarize` reads in parts.
- `LLM.json` retries once when a reply is not valid JSON or fails the schema. The retry repeats the request with a note on what was wrong and asks for `response_format` `json_object` (on OpenRouter with `require_parameters`); a provider that refuses `response_format` is asked again without it, and the model remembers that.
- **Behaviour change:** CROW `relate` also reads, as candidates, up to 40 notes anywhere in the wiki that share rare values (codes, amounts, dates, names held by few notes) with the new or updated note, skipping notes that already have 12 See-also links. This changes the links CROW adds; classic is unchanged. `OKF_RELATE_RARE_VALUES=false` keeps 0.3.0's `relate`.
- **Behaviour change:** CROW `ask` asks one more typed question (`ask/conflict`) when two or more notes go to the answer: if the notes seem to disagree on the answer, the question gets a hint to report every value, which note gives which and which source is more recent. This changes the CROW answer; classic is unchanged. `OKF_ANSWER_CONFLICTS=false` keeps 0.3.0's answer input.
- **Behaviour change:** CROW routing opens a new subfolder only when the classifier is sure of it (`strict_new_folders`, on by default): a `New subfolder` answer under `tau_route` cannot end a path and its probability goes to `Here` (at the root the other options are renormalised), and `classifier/route` asks whether a folder's description covers the note. Fewer folders with a single note and fewer notes of different subjects sharing a folder, fewer LLM fallbacks; a note on a new subject more often stays in the parent folder. `OKF_STRICT_NEW_FOLDERS=false` keeps 0.3.0's routing, whose prompt is now `classifier/route_open`.
- **Prompt overrides** (`prompts_dir`, `OKF_PROMPTS_DIR`) written for 0.3.0 need a look. `librarian/merge` now receives the incoming note as `incoming_title`, `incoming_summary`, `incoming_tags`, `incoming_body` and `incoming_from`; a 0.3.0 override that reads `{{source_text}}` gets it empty when there is a candidate, so the draft usually misses the new values and the merge is asked again from the source with `librarian/merge_source`, one call more each time. An override of `classifier/route` replaces the strict prompt; with `strict_new_folders` off the routing reads `classifier/route_open`, so a 0.3.0 override of `classifier/route` must be renamed to stay in use. `shared/note_rules` has the `{{length_rule}}` placeholder: `ask_json` and `ask_text` fill it, a direct `ctx.prompts.render` of a template that includes it must pass it.

### Fixed

- `LLM.chat`: the fallback to the least reasoning now holds for requests already in flight. With several threads (`ingest_many`), requests built before the first 400 "Reasoning is mandatory" used to raise once another thread had switched; they are now resent once with the new body.
- Text read from a `.txt` or `.md` file has `\n` line endings: the same file saved on Windows and elsewhere is the same text, with the same hash. A file with `\r\n` line endings filed by 0.3.0 hashes differently once: `ingest_file` and `ingest_many` with an `Origin` that has no version file it again; `sync` with `LocalFolderSource` skips a file whose version is unchanged.
- Files are written with `\n` line endings on every system, and a write that fails on Windows because another thread or process has the file open is retried briefly.

## [0.3.0] - 2026-09-30

### Added

- Provenance on ingest. `ingest` and `ingest_file` accept an `Origin` (source, account, id, and optionally link, version and modified time) that says where a document comes from.
- Raw copies record a content hash, and the origin and a save sequence number when one is given. Notes cite the hash and origin of their sources.
- A new `unchanged` ingest result: when a document with the same origin was already filed with the same content or version, no model call is made and nothing is written.
- `wiki.origin(key)` returns the latest raw copy cited by a note and the notes for one original.
- `wiki.mark_removed(key)` records that an original was deleted at its source. The raw copies are marked and the notes are kept.
- Source connector protocol (`SourceConnector`, `Change`, `ChangeBatch`) and `sync(wiki, connector)`, which files what changed since the last run and resumes from the saved cursor. Unreadable items are listed in the report and the sync continues.
- `LocalFolderSource`, a connector for a local folder (for example an rclone-synced OneDrive). Hidden files, symbolic links and unsupported file types are ignored.
- `llmwiki2 sync FOLDER` files a folder's documents and keeps the wiki in step with it. `--json` prints the full report. The exit code is 1 if any item failed.
- `sync` files each page of changes as one batch: one change per origin (the last), removals first, items of a known version not fetched. A model failure stops it with `SyncReport.stopped` set and the cursor of the last finished page kept, so the next run resumes there; `llmwiki2 sync` then exits with 2.
- `POST /ingest` accepts an optional `origin` object.
- Package metadata on PyPI: authors, keywords, classifiers, and project URLs (homepage, repository, issues, paper, changelog).
- A release workflow that builds the package and publishes it to PyPI with trusted publishing when a `v*` tag is pushed. A manual run publishes to TestPyPI.
- Customizable steps. The ingest and ask flows stay fixed. Each step is a function that a `Steps` set can replace: `extract`, `summarize`, `route`, `name_folder`, `match`, `consolidate`, `merge` and `relate` for ingest, and `select` and `answer` for ask. `CLASSIC` and `CROW` are the two ready-made sets. Pass your own with `Wiki(cfg, steps=replace(CROW, name="...", summarize=...))`. `extract` and `summarize` may run in parallel on different threads and must be thread-safe; the other ingest steps run one document at a time, under the lock.
- `StepContext`, given to every step: the models, prompts, config and `decide()`. Model calls a custom step makes through it, a custom `extract` included, are counted in usage and decisions like the built-in ones.
- `StepError`: a step that returns something the flow cannot take stops the ingest or the question with an error naming the step, before anything is written.
- `fields`: `ingest` and `ingest_file` accept `fields=`, user data the steps read as `ctx.source.fields`. `Candidate.fields` and `NoteDraft.fields` are written to the note frontmatter under `fields:`.
- `Route.create`: a route can ask for a whole folder path in one ingest, one `(name, description)` per level. Existing levels are reused.
- `wiki.ingest_many(items, concurrency=, on_result=)` files a batch of `Item`s, preparing up to `concurrency` documents at once (fetch, extract, the "unchanged" check and `summarize`) and filing them one at a time, in order of their original's modified time, under the wiki's lock. It returns a `BatchReport` (`results`, `not_done`, `stopped`). A model failure stops the batch: the documents before the failed one are filed, the rest are reported in `not_done`. On a benchmark of 8 runs per side (CROW mode, the demo corpus) filing with 4 at once took half the time of filing one at a time, with folders, notes, merges, links and `check()` within the spread of the serial runs.
- `Item` and `BatchReport` are exported from `llmw2`. `Librarian.prepare` and `Librarian.file` are the two phases of `Librarian.ingest`.
- `concurrency` in `WikiConfig` (`OKF_CONCURRENCY`, the settings page; 1 to 16, default 4) and `llmwiki2 sync --concurrency N`.
- `GET /health` reports `concurrency`. The server reads and summarizes up to that many uploads at once and files them one at a time, and the page's filing queue sends that many at once.
- Library documentation in `docs/library/` (the public API and the customizable steps) and runnable examples in `examples/`.

### Changed

- The license is declared as the SPDX expression `AGPL-3.0-or-later`, which requires setuptools 77 or later to build.
- README links are absolute, so they work on the PyPI project page.
- The repository moved to https://github.com/crow-hq/llm-wiki-v2 and all project URLs point there.
- `wiki.origin`, the `unchanged` check and `sync` no longer reread every raw copy and the whole note tree for each item: an in-memory index rereads only the files that changed since the last look.
- Every note source entry records the step set that wrote it (`steps: {name, hash}`), and the ingest log line ends with `(name hash)` instead of `(mode)`.
- `CrowLibrarian` and `CrowResearcher` are gone. CROW is the classic step set with its decision steps replaced, and the steps live in `llmw2.agents.steps_classic` and `llmw2.agents.steps_crow`. Classic and CROW behave as before.
- The tags of a new note are now cleaned the same way as on a merge, whichever `summarize` wrote them.
- `ingest` and `ingest_file` no longer hold the wiki's lock while the model summarizes; they take it to file. The wiki's lock also holds between threads of one process, Windows included, so the server no longer wraps ingests in a lock of its own.

### Fixed

- A document whose ingest was interrupted after its raw copy was saved is no longer treated as unchanged forever. Only raw copies cited by a note count for `unchanged`, `wiki.origin` and `sync`, and the raw copy is now saved right before the note is written.

## [0.2.0] - 2026-09-24

The first release of the `llm-wiki-v2` repository, built on Google's Open Knowledge Format (OKF). Versions up to 0.2.0 were released under the Apache License 2.0 and stay available under it. Later versions are AGPL-3.0-or-later (see NOTICE).

### Added

- Librarian that files documents (`.txt`, `.md`, `.pdf`) into a wiki of Markdown notes and a researcher that answers questions citing the notes used.
- CROW mode, where a small classifier decides where notes go and which are read, and classic mode, where the LLM decides. A separate provider can be set for the classifier.
- Web UI served by `llmwiki2`, with a settings page for the provider and keys, drag-and-drop upload, writing notes from the page, and a brain view of the folder graph that refreshes as documents are filed.
- Deleting notes, folders or the whole wiki from the page.
- Any OpenAI-compatible provider (OpenRouter, OpenAI, Gemini, Ollama, others).
- `llmwiki2 demo`, an example wiki to browse without a key.
- Retrieval benchmark measuring accuracy, latency and cost as the wiki grows, and a Docker setup.

### Changed

- Relicensed from Apache-2.0 to AGPL-3.0-or-later, with a Contributor License Agreement for contributions.
- The package and module are named `llmw2` and the command `llmwiki2`.
- Dropped the OKF fork (own document parser, no viewer, no reference agent) and the Bifrost gateway.

[2.0.0rc1]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v2.0.0rc1
[1.0.5]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v1.0.5
[1.0.4]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v1.0.4
[1.0.3]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v1.0.3
[1.0.2]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v1.0.2
[1.0.1]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v1.0.1
[1.0.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v1.0.0
[0.8.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.8.0
[0.7.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.7.0
[0.6.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.6.0
[0.5.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.5.0
[0.4.4]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.4
[0.4.3]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.3
[0.4.2]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.2
[0.4.1]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.1
[0.4.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.4.0
[0.3.0]: https://github.com/crow-hq/llm-wiki-v2/releases/tag/v0.3.0
