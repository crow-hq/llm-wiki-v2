"""Build the deep demo wiki (`benchmarks/corpus/deep/base/`) and the growth part of the deep corpus. No model, free.

    python benchmarks/make_deep_demo.py           # rebuild corpus/deep/base from src/llmw2/demo, and the 30 growth documents
    python benchmarks/make_deep_demo.py --check   # only check what is there

The deep base holds the same 22 notes and `about-this-demo.md` as the demo, in a tree of three levels (FOLDERS and
NOTES below). It is written with the library's own store (folders, indexes, log), every relative link between notes
is rewritten to the notes' new places, and `check()` must pass. `corpus/deep/` also holds the 30 growth documents with
their labels remapped to the deep tree (`remap_labels`); `make_deep_corpus.py` adds twelve documents of its own.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from llmw2.bundle.check import check  # noqa: E402
from llmw2.bundle.document import OKFDocument  # noqa: E402
from llmw2.bundle.store import WikiStore, write_atomic  # noqa: E402
from llmw2.bundle.tree import INDEX, LOG, RAW, link_target, note_dir, rel_link  # noqa: E402

DEMO = HERE.parent / "src" / "llmw2" / "demo"
GROWTH = HERE / "corpus" / "growth"
DEEP = HERE / "corpus" / "deep"
BASE = DEEP / "base"
ABOUT = "about-this-demo.md"
REORGANISED = "the folder tree was reorganised into three levels for the deep benchmark (benchmarks/make_deep_demo.py)"

# folder -> description, in the order the folders are created (parents first)
FOLDERS: dict[str, str] = {
    "/customers": "Wholesale cafés and home subscribers: who they are, what they buy, how we serve them.",
    "/customers/wholesale": "Cafés that buy from us wholesale: accounts, onboarding, training.",
    "/customers/subscriptions": "Home subscribers: churn, retention, what they ask for.",
    "/customers/policies": "Customer policies: refunds, returns, terms.",
    "/products": "Coffees we sell and their prices.",
    "/products/coffees": "Our coffees: blends, single origins, decaf.",
    "/products/pricing": "Price lists and price changes.",
    "/sourcing": "Green coffee: importers, origins and price risk.",
    "/sourcing/importers": "The importers we buy from and our terms with them.",
    "/sourcing/origins": "Farms, washing stations and origin trips.",
    "/sourcing/risk": "Price risk and hedging of green coffee.",
    "/operations": "Roasting and shipping at the roastery.",
    "/operations/roasting": "Roasting coffee.",
    "/operations/roasting/profiles": "Roast profiles of our coffees.",
    "/operations/roasting/quality": "Quality control, cupping and defects.",
    "/operations/roasting/equipment": "The roaster and its maintenance.",
    "/operations/shipping": "Shipping orders and subscriptions.",
    "/operations/shipping/carriers": "Carriers, rates and delivery times.",
    "/operations/shipping/packing": "Packing days and how boxes are packed.",
    "/meetings": "Notes of board and team meetings.",
    "/meetings/board": "Board meetings.",
    "/meetings/team": "Team and working meetings.",
    "/ideas": "Ideas for products, customers and events not decided yet.",
    "/ideas/products": "Ideas for new products.",
    "/ideas/customers": "Ideas for serving customers better.",
    "/ideas/experiences": "Ideas for events and experiences.",
}

# note slug -> its folder in the deep base
NOTES: dict[str, str] = {
    "cafe-portello": "/customers/wholesale",
    "wholesale-cafe-onboarding": "/customers/wholesale",
    "subscription-churn-q2-2026": "/customers/subscriptions",
    "refund-policy": "/customers/policies",
    "aurora-espresso-blend": "/products/coffees",
    "ethiopia-guji-seasonal-single-origin": "/products/coffees",
    "night-shift-decaf": "/products/coffees",
    "price-list-from-july-2026": "/products/pricing",
    "andes-direct-importer": "/sourcing/importers",
    "guji-washing-station-visit-february-2026": "/sourcing/origins",
    "green-coffee-price-hedging": "/sourcing/risk",
    "aurora-roast-profile": "/operations/roasting/profiles",
    "quality-control-and-cupping": "/operations/roasting/quality",
    "roaster-maintenance": "/operations/roasting/equipment",
    "carriers-and-delivery-times": "/operations/shipping/carriers",
    "subscription-packing-day": "/operations/shipping/packing",
    "board-meeting-june-2026": "/meetings/board",
    "pricing-meeting-may-2026": "/meetings/team",
    "weekly-sync-10-june-2026": "/meetings/team",
    "cold-brew-cans": "/ideas/products",
    "pause-instead-of-cancel": "/ideas/customers",
    "roastery-tours": "/ideas/experiences",
}

_LINK = re.compile(r"(\]\()([^)\s]+)(\))")


def folder_with_descendants(label: str) -> list[str]:
    """A folder of the deep base and all its descendants, from the map (no disk)."""
    return [f for f in FOLDERS if f == label or f.startswith(label.rstrip("/") + "/")]


def old_paths() -> dict[str, str]:
    """Where each note is in the demo: its slug -> "customers/refund-policy.md"."""
    return {
        p.stem: p.relative_to(DEMO).as_posix()
        for p in sorted(DEMO.rglob("*.md"))
        if not p.relative_to(DEMO).as_posix().startswith(f"{RAW}/") and p.name not in (INDEX, LOG, ABOUT)
    }


def moves() -> dict[str, str]:
    """Old bundle path of each document of the demo -> its path in the deep base."""
    out = {old: f"{NOTES[slug].strip('/')}/{slug}.md" for slug, old in old_paths().items()}
    out[ABOUT] = ABOUT
    return out


def rewrite_links(text: str, old_rel: str, new_rel: str, moved: dict[str, str]) -> str:
    """`text` of the document at `old_rel` (now at `new_rel`) with each relative link to a moved document re-pointed."""

    def fix(m: re.Match[str]) -> str:
        link = m[2]
        target = link_target(old_rel, link) if not link.startswith("/") else None
        if target is None or target not in moved:
            return m[0]
        fragment = f"#{link.split('#', 1)[1]}" if "#" in link else ""
        return f"{m[1]}{rel_link(note_dir(new_rel), moved[target])}{fragment}{m[3]}"

    return _LINK.sub(fix, text)


# -- the base ----------------------------------------------------------------------------------------


def build(out: Path = BASE) -> None:
    """Rebuild the deep base in `out` from scratch."""
    shutil.rmtree(out, ignore_errors=True)
    moved = moves()
    store = WikiStore(out, actor="benchmarks/make_deep_demo.py")
    store.init()
    shutil.copytree(DEMO / RAW, out / RAW, dirs_exist_ok=True)
    root = store.load()
    for label, description in FOLDERS.items():
        parent_label, _, name = label.rpartition("/")
        parent = root.find(parent_label)
        assert parent is not None, label
        store.create_folder(parent, name, description)
    for old, new in moved.items():
        doc = OKFDocument.parse((DEMO / old).read_text(encoding="utf-8"))
        doc.body = rewrite_links(doc.body, old, new, moved)
        (out / new).parent.mkdir(parents=True, exist_ok=True)
        write_atomic(out / new, doc.serialize())
    for folder in store.load().walk():  # the library writes each index, notes included
        store.write_index(folder)
    write_atomic(out / LOG, rewrite_links((DEMO / LOG).read_text(encoding="utf-8"), LOG, LOG, moved))
    store.append_log("reorganised", REORGANISED)


def verify(out: Path = BASE) -> list[str]:
    """What is wrong with the deep base in `out` (empty: nothing)."""
    if not out.is_dir():
        return [f"{out} is missing"]
    problems = [str(p) for p in check(out)]
    root = WikiStore(out, actor="check").load()
    where = {n.slug: n.folder.label if n.folder else "?" for n in root.all_notes()}
    expected = {**NOTES, ABOUT.removesuffix(".md"): "/"}
    problems += [
        f"note {s}: in {where.get(s)}, expected {expected.get(s)}"
        for s in sorted(where.keys() | expected.keys())
        if where.get(s) != expected.get(s)
    ]
    for label, description in FOLDERS.items():
        folder = root.find(label)
        if folder is None:
            problems.append(f"folder {label} is missing")
        elif folder.description != description:
            problems.append(f"folder {label}: description {folder.description!r}")
    if extra := sorted({f.label for f in root.walk() if not f.is_root} - FOLDERS.keys()):
        problems.append(f"folders not in the map: {', '.join(extra)}")
    if len(root.all_notes()) != len(NOTES) + 1:
        problems.append(f"{len(root.all_notes())} notes, expected {len(NOTES) + 1}")
    if sorted(p.name for p in (out / RAW).glob("*.md")) != sorted(p.name for p in (DEMO / RAW).glob("*.md")):
        problems.append("raw/ is not the demo's")
    return problems


# -- the labels of the growth documents ---------------------------------------------------------------

EXTRA_ALT: dict[str, list[str]] = {  # old labels that the deep map now covers (R5 of the spec)
    "19-compostable-bag-supplier-quotes.md": ["/operations/shipping/packing"],
    "11-tasting-bar-plan-and-licence.md": ["/ideas/experiences"],
    "23-events-calendar-autumn-2026.md": ["/ideas/experiences"],
}


def remap_labels(growth: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The labels of the growth corpus for the deep base.

    `folders` and `alt_folders`: each old folder and all its descendants; `merge_into` / `merge_ok`: the notes' new paths;
    `kind`, `group` and `merge_with_doc` as they were. (A document labelled /customers is right anywhere below it.)
    """
    moved = moves()

    def folders(old: list[str]) -> list[str]:
        return list(dict.fromkeys(f for label in old for f in folder_with_descendants(label)))

    def notes(value: Any) -> Any:
        if not value:
            return value
        return moved[value] if isinstance(value, str) else [moved[v] for v in value]

    out = {}
    for name, label in growth.items():
        new = dict(label)
        new["folders"] = folders(label.get("folders", []))
        alt = folders(label.get("alt_folders", [])) + EXTRA_ALT.get(name, [])
        if alt:
            new["alt_folders"] = list(dict.fromkeys(alt))
        for key in ("merge_into", "merge_ok"):
            if key in label:
                new[key] = notes(label[key])
        out[name] = new
    return out


def growth_labels() -> dict[str, dict[str, Any]]:
    return remap_labels(json.loads((GROWTH / "labels.json").read_text(encoding="utf-8"))["docs"])


def write_labels(out: Path, about: str, docs: dict[str, dict[str, Any]]) -> None:
    (out / "labels.json").write_text(json.dumps({"about": about, "docs": docs}, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_growth(out: Path = DEEP) -> None:
    """The 30 growth documents, as they are, and their remapped labels, in `out`."""
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted(GROWTH.glob("*.md")):
        shutil.copyfile(path, out / path.name)
    about = (
        "Synthetic growth benchmark for the deep demo: the 30 growth documents of benchmarks/corpus/growth with labels "
        "remapped to the deep tree by benchmarks/make_deep_demo.py (a label covers the folder and all its descendants)."
    )
    write_labels(out, about, growth_labels())


def verify_growth(out: Path = DEEP) -> list[str]:
    problems = []
    for path in sorted(GROWTH.glob("*.md")):
        if not (out / path.name).is_file() or (out / path.name).read_bytes() != path.read_bytes():
            problems.append(f"{path.name} is not the growth document")
    if not (out / "labels.json").is_file():
        return [*problems, "labels.json is missing"]
    have = json.loads((out / "labels.json").read_text(encoding="utf-8"))["docs"]
    want = growth_labels()
    problems += [f"labels of {d} differ from the remap of the growth labels" for d in want if have.get(d) != want[d]]
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--check", action="store_true", help="only check what is there (free)")
    args = p.parse_args(argv)
    if not args.check:
        build()
        write_growth()
    problems = [*verify(), *verify_growth()]
    for line in problems:
        print(f"ERROR {line}")
    if not problems:
        print(f"deep base ok: {len(NOTES) + 1} notes, {len(FOLDERS)} folders, check() clean, {len(growth_labels())} growth labels")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
