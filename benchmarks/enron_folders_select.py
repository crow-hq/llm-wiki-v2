# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scale corpus: the folders the Enron employees made, across mailboxes (https://www.cs.cmu.edu/~enron/).

    python benchmarks/enron_folders_select.py --tar enron_mail_20150507.tar.gz --out DIR [--n 2000] [--seed 9]

Pin: `enron_mail_20150507.tar.gz`, sha256 `b3da1b3fe0369ec3140bb4fbce94702c33b7da810ec15d718b3fadf5cd748ca7` (checked by the
script).

The messages that the owner of a mailbox filed in a folder of their own: every system folder (inbox, sent, sent_items,
deleted_items, all_documents, discussion_threads, notes_inbox, _sent_mail, calendar, contacts, ...) is left out. The folder is
the first one under the mailbox (a sub-folder counts for its top folder), so the reference folder has two levels,
`/mailbox/folder`. A message is kept once: a copy with the Message-ID, or the subject and body, of one already kept (the same mail
in several mailboxes or folders) is dropped, and so is one with no body or no parsable date. Only the folders that keep at least
`--min-folder` (50) messages are eligible, and the mailboxes that keep at least one such folder.

The N messages (default 2 000) are the first N of one seeded shuffle of the eligible ones: a selection of 1 000 is a prefix, in
filing order, of the one of 2 000 (same seed), so a run can grow from the first to the second without changing a document. The
files are numbered in that order (`0001-<subject>.md`). Each one is plain Markdown: the subject as heading, then the headers
From, To, Date and the body as written (up to 20 000 characters). `DIR-manifest.json` (next to DIR, so that the folder holds
only documents) gives the source, the pin, the parameters and, in filing order, for each message its file, folder (the gold of
`incremental_score.py --gold`), mailbox, original path in the tar and the sha256 of the file written, plus the count per mailbox
and folder. Nothing of the corpus is committed: only this script, the URL and the pin.
"""

from __future__ import annotations

import argparse
import email
import email.utils
import hashlib
import json
import random
import re
import tarfile
from collections import Counter
from pathlib import Path

URL = "https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz"
SHA256 = "b3da1b3fe0369ec3140bb4fbce94702c33b7da810ec15d718b3fadf5cd748ca7"
MAX_BODY = 20_000
SYSTEM = {
    "inbox",
    "sent",
    "sent_items",
    "_sent_mail",
    "_sent",
    "deleted_items",
    "all_documents",
    "discussion_threads",
    "notes_inbox",
    "calendar",
    "contacts",
    "straw",
    "to_do",
    "tasks",
    "drafts",
    "outbox",
    "junk",
    "junk_file",
    "junk_mail",
}


def user_folder(name: str) -> tuple[str, str] | None:
    parts = name.split("/")  # maildir/<user>/<folder>[/<sub>...]/<n>.
    if len(parts) < 4 or parts[0] != "maildir" or parts[2].lower() in SYSTEM:
        return None
    return parts[1], parts[2].lower()


def body_of(msg: email.message.Message) -> str:
    if not msg.is_multipart():
        return str(msg.get_payload()).strip()
    parts = [p.get_payload() for p in msg.walk() if p.get_content_type() == "text/plain"]
    return "\n".join(str(p) for p in parts).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description="select the Enron user-folder corpus")
    ap.add_argument("--tar", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=9)
    ap.add_argument("--min-folder", type=int, default=50, help="fewest messages kept for a folder to be eligible")
    a = ap.parse_args()
    sha = sha256_file(a.tar)
    if sha != SHA256:
        raise SystemExit(f"{a.tar} has sha256 {sha}, expected {SHA256}")
    msgs = []
    seen_ids: set[str] = set()
    seen_bodies: set[str] = set()
    dropped = Counter()
    with tarfile.open(a.tar, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or not (uf := user_folder(member.name)):
                continue
            raw = tar.extractfile(member).read().decode("latin-1")  # type: ignore[union-attr]
            msg = email.message_from_string(raw)
            body = body_of(msg)
            try:
                dt = email.utils.parsedate_to_datetime(msg["Date"])
            except (TypeError, ValueError):
                dt = None
            if dt is None or not body:
                dropped["no body or date"] += 1
                continue
            subject = " ".join((msg["Subject"] or "").split())
            mid = (msg["Message-ID"] or "").strip()
            bkey = hashlib.sha1(f"{subject}\n{body}".encode()).hexdigest()
            if (mid and mid in seen_ids) or bkey in seen_bodies:
                dropped["duplicate"] += 1
                continue
            seen_ids.add(mid)
            seen_bodies.add(bkey)
            msgs.append(
                {
                    "mailbox": uf[0],
                    "folder": uf[1],
                    "path": member.name,
                    "subject": subject,
                    "from": " ".join((msg["From"] or "").split()),
                    "to": " ".join((msg["To"] or "").split())[:200],
                    "date": dt.strftime("%Y-%m-%d"),
                    "body": body[:MAX_BODY],
                }
            )
    per = Counter((m["mailbox"], m["folder"]) for m in msgs)
    eligible = sorted((m for m in msgs if per[(m["mailbox"], m["folder"])] >= a.min_folder), key=lambda m: m["path"])
    random.Random(a.seed).shuffle(eligible)
    picked = eligible[: a.n]
    a.out.mkdir(parents=True, exist_ok=True)
    docs = []
    for i, m in enumerate(picked, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", m["subject"].lower()).strip("-")[:40] or "no-subject"
        name = f"{i:04d}-{slug}.md"
        data = (
            f"# {m['subject'] or '(no subject)'}\n\nFrom: {m['from']}\nTo: {m['to']}\nDate: {m['date']}\n\n{m['body']}\n"
        ).encode()
        (a.out / name).write_bytes(data)
        docs.append(
            {
                "file": name,
                "folder": f"/{m['mailbox']}/{m['folder']}",
                "mailbox": m["mailbox"],
                "path": m["path"],
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    counts = Counter(d["folder"] for d in docs)
    manifest = {
        "url": URL,
        "archive": "enron_mail_20150507.tar.gz",
        "archive_sha256": SHA256,
        "n": len(docs),
        "seed": a.seed,
        "min_folder": a.min_folder,
        "eligible": len(eligible),
        "eligible_folders": len({(m["mailbox"], m["folder"]) for m in eligible}),
        "dropped": dict(dropped),
        "per_mailbox": dict(sorted(Counter(d["mailbox"] for d in docs).items())),
        "per_folder": dict(sorted(counts.items())),
        "docs": docs,
    }
    (a.out.parent / f"{a.out.name}-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{len(docs)} messages of {len(eligible)} eligible in {manifest['eligible_folders']} folders; dropped {dict(dropped)}")
    print(f"{len(manifest['per_mailbox'])} mailboxes, {len(counts)} folders in the selection")


if __name__ == "__main__":
    main()
