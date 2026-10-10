# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task 9: a noisy real corpus from the Enron mail (https://www.cs.cmu.edu/~enron/, enron_mail_20150507.tar.gz).

    python benchmarks/t9_enron_select.py --tar enron_mail_20150507.tar.gz --list            # folders per mailbox
    python benchmarks/t9_enron_select.py --tar ... --user kaminski-v --out DIR [--n 150]

One mailbox; only the folders its owner made (system folders left out). Up to N messages, seeded, even over the user
folders' lifetime; a real quarter is a simulated month. Each message becomes a document (subject as title, headers From/To/
Date, the body as it is). Labels: the user folder. Events: the quarter each user folder was first used (from all its
messages, not only those sampled). Nothing of the corpus is committed: only this script, the URL and the archive's sha256.
"""

from __future__ import annotations

import argparse
import email
import email.utils
import json
import random
import re
import tarfile
from collections import Counter, defaultdict
from pathlib import Path

SYSTEM = {
    "inbox",
    "sent",
    "sent_items",
    "_sent_mail",
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
    "_sent",
}


def quarter_month(y: int, m: int) -> str:
    return f"{y}-{(m - 1) // 3 * 3 + 1:02d}"


def user_folder(name: str) -> tuple[str, str] | None:
    parts = name.split("/")  # maildir/<user>/<folder...>/<n>.
    if len(parts) < 4 or parts[0] != "maildir" or parts[2].lower() in SYSTEM:
        return None
    return parts[1], "/" + "/".join(p.lower() for p in parts[2:-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", type=Path, required=True)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--user")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=9)
    a = ap.parse_args()
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    msgs = []
    with tarfile.open(a.tar, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or not (uf := user_folder(member.name)):
                continue
            user, folder = uf
            counts[user][folder] += 1
            if a.user and user == a.user:
                raw = tar.extractfile(member).read().decode("latin-1")  # type: ignore[union-attr]
                msg = email.message_from_string(raw)
                try:
                    dt = email.utils.parsedate_to_datetime(msg["Date"])
                except (TypeError, ValueError):
                    continue
                body = msg.get_payload() if not msg.is_multipart() else ""
                msgs.append(
                    {
                        "folder": folder,
                        "date": dt.strftime("%Y-%m-%d"),
                        "y": dt.year,
                        "m": dt.month,
                        "subject": (msg["Subject"] or "").strip(),
                        "from": msg["From"] or "",
                        "to": (msg["To"] or "")[:200],
                        "body": str(body).strip(),
                        "id": member.name,
                    }
                )
    if a.list:
        for user, c in sorted(counts.items(), key=lambda kv: -len(kv[1])):
            print(f"{user:20} folders {len(c):3} messages {sum(c.values()):6}  top {c.most_common(4)}")
        return
    msgs = [m for m in msgs if 1999 <= m["y"] <= 2002 and m["body"]]
    first = {}
    for m in sorted(msgs, key=lambda m: m["date"]):
        first.setdefault(m["folder"], quarter_month(m["y"], m["m"]))
    random.Random(a.seed).shuffle(msgs)
    picked = sorted(msgs[: a.n], key=lambda m: (m["date"], m["id"]))
    assert a.out
    a.out.mkdir(parents=True, exist_ok=True)
    manifest, labels = {}, {}
    for i, m in enumerate(picked, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", m["subject"].lower()).strip("-")[:40] or "no-subject"
        name = f"{i:03d}-{slug}.md"
        text = f"# {m['subject'] or '(no subject)'}\n\nFrom: {m['from']}\nTo: {m['to']}\nDate: {m['date']}\n\n{m['body'][:20000]}\n"
        (a.out / name).write_text(text, encoding="utf-8")
        manifest[name] = quarter_month(m["y"], m["m"])
        labels[name] = {"folder": m["folder"], "date": m["date"], "message": m["id"]}
    events = [{"folder": f, "month": q} for f, q in sorted(first.items(), key=lambda kv: kv[1])]
    for f, v in (("manifest.json", manifest), ("labels.json", labels), ("events.json", events)):
        (a.out / f).write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")
    source = {"url": "https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz", "user": a.user, "n": a.n, "seed": a.seed}
    (a.out / "source.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
    print(
        f"{len(picked)} messages from {a.user}, {len(set(labels[n]['folder'] for n in labels))} folders, "
        f"months {Counter(manifest.values())}"
    )


if __name__ == "__main__":
    main()
