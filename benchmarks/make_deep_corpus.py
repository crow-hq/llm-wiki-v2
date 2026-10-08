"""Write the deep corpus (`benchmarks/corpus/deep/`): the 30 growth documents with labels remapped to the deep demo, and
twelve new documents (31 to 42) written by the LLM of your settings on OpenRouter, with their `labels.json`.

    python benchmarks/make_deep_corpus.py [--only 31 35] [--model deepseek/deepseek-v4-flash]
    python benchmarks/make_deep_corpus.py --dry-run --out /tmp/deep    # synthetic texts, no model, free
    python benchmarks/make_deep_corpus.py --check [--out DIR]          # control table, free

The labels are fixed here, before writing: four documents whose wording points to the wrong top folder (kind existing,
one right leaf), four ambiguous across two branches (both leaves and their parents are right), four new in two groups.
It costs about $0.02-0.05 with deepseek-v4-flash; the real cost is printed from the usage of the replies.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # the scripts of this folder import each other
sys.path.insert(0, str(HERE.parent / "src"))

import make_deep_demo as deep  # noqa: E402
from corpus_common import WORLD, ask  # noqa: E402
from make_long2_corpus import demo_notes, words  # noqa: E402

from llmw2.settings import Settings  # noqa: E402

MODEL = "deepseek/deepseek-v4-flash"
MIN_CHARS, MAX_CHARS = 1500, 6000  # 4000 in the spec; the model wrote up to 5358, accepted rather than paid again


@dataclass(frozen=True)
class Doc:
    filename: str
    title: str
    what: str  # the brief given to the model
    kind: str
    folders: list[str]
    group: str | None = None
    alt_folders: list[str] = field(default_factory=list)

    @property
    def number(self) -> str:
        return self.filename[:2]

    def label(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "folders": self.folders, "merge_into": None, "group": self.group}
        if self.alt_folders:
            out["alt_folders"] = self.alt_folders
        return out


def with_parents(*leaves: str) -> list[str]:
    """The leaves and their parent folders (an LLM answering the parent files the document there too)."""
    return list(dict.fromkeys(f for leaf in leaves for f in (leaf, leaf.rsplit("/", 1)[0])))


# The labels are part of the design: they are fixed here, whatever the model writes.
SPEC = [
    # misleading surface: the wording points to a wrong top folder, the content belongs to one leaf
    Doc("31-board-meeting-17-november-2026.md", "Board meeting, 17 November 2026",
        "the minutes of a board meeting (date, attendees, apologies, agenda, decisions, next meeting) in which every agenda "
        "item is about one subject only: locking in the price of the green coffee Lumen will buy in 2027 with futures "
        "contracts, which fixings to take, how much of the volume to cover, the cost of the cover and what it does to the "
        "margin. Nothing else is discussed",
        "existing", ["/sourcing/risk"]),
    Doc("32-customer-email-thread-late-order.md", "Customer email thread: the order that left late",
        "an email thread (greeting, signatures, dates) between a wholesale café owner asking why their weekly order left a "
        "day late and a Lumen account manager who answers it. The whole body of the replies is about one fault of the "
        "roaster: the drum bearing and the burner that tripped, the engineer's diagnosis, the parts replaced and the repair "
        "steps. Nothing is said about the café's account, orders, prices or delivery beyond the first question",
        "existing", ["/operations/roasting/equipment"]),
    Doc("33-idea-memo-rolling-out-the-new-prices.md", "Idea: rolling out the new prices",
        "a short memo whose title and first lines call it an idea to try, but whose content is the new price list being "
        "applied: the new price per bag and per kilo of each coffee for wholesale and retail, the date it takes effect, who "
        "updates the invoices and the shop, how the change is told to customers. It reads as a decided change, with a table "
        "of old and new prices",
        "existing", ["/products/pricing"]),
    Doc("34-team-meeting-24-november-2026.md", "Team meeting, 24 November 2026",
        "the notes of a team meeting (date, attendees, a short agenda, action items) that is entirely a comparison of the "
        "rates of three parcel carriers: price per parcel by weight band, fuel and remote-area surcharges, delivery times, "
        "pickup windows, and which one the team prefers. Nothing else is discussed",
        "existing", ["/operations/shipping/carriers"]),
    # ambiguous across two branches: the two leaves and their parents are right
    Doc("35-hedged-prices-and-the-2027-list.md", "Hedged prices and the 2027 list",
        "a finance note about how the price Lumen locked in for next year's green coffee changes the price list for 2027: "
        "the hedged cost per kilo, the cost of the cover, the new price per bag of each coffee, the margin before and after. "
        "Give the cover and the list equal space",
        "ambiguous", with_parents("/sourcing/risk", "/products/pricing")),
    Doc("36-caffe-dora-late-parcels-complaint.md", "Caffè Dora: complaint about late parcels",
        "a long complaint from a wholesale café, Caffè Dora (an account of Lumen), about three parcels that arrived late "
        "with the carrier, with its effect on the café, what the owner expects, and what Lumen answers: the account, the "
        "orders and the café's volumes, and also the carrier's tracking, delays and what is asked of it. Give the café "
        "and the carrier equal space",
        "ambiguous", with_parents("/customers/wholesale", "/operations/shipping/carriers")),
    Doc("37-subscriber-survey-monthly-micro-lot-box.md", "Subscriber survey: a monthly micro-lot box",
        "the results of a survey of home subscribers about a product Lumen might launch, a monthly box of rare micro-lot "
        "coffees: who answered, how many would subscribe, the price they accept, what they ask for, what they say of their "
        "current subscription, and what the team thinks of the idea. Give the subscribers and the new product equal space",
        "ambiguous", with_parents("/customers/subscriptions", "/ideas/products")),
    Doc("38-board-decision-roaster-service-contract.md", "Board decision: the roaster service contract",
        "the part of the board minutes that decides the service contract of the roaster: the two offers, annual cost, "
        "response times, spare parts included or not, the vote and who signs. Give the board's discussion and the "
        "technical content of the contract equal space",
        "ambiguous", with_parents("/meetings/board", "/operations/roasting/equipment")),
    # new: two groups of two, in folders the base does not have
    Doc("39-roastery-energy-use-2026.md", "Roastery energy use, 2026",
        "a report on how much gas and electricity the roastery used in 2026, month by month: kWh and cubic metres, cost, "
        "the roaster's share, the afterburner, the packing line and the heating, compared with the kilos roasted, and what "
        "stands out",
        "new", [], "g4", ["/operations/roasting/equipment"]),
    Doc("40-heat-recovery-proposal-for-the-exhaust.md", "Proposal: recovering heat from the roaster exhaust",
        "a proposal to cut the roastery's energy bill by recovering heat from the roaster exhaust and the afterburner: "
        "the current consumption, the investment, the expected saving per year, the payback, the supplier's quote and the "
        "risks",
        "new", [], "g4", ["/operations/roasting/equipment"]),
    Doc("41-coffee-school-autumn-courses.md", "Lumen Coffee School: the autumn courses",
        "the programme of the coffee school Lumen already runs for the public in the roastery: the courses (home brewing, "
        "tasting, latte art), dates, group size, price per seat, the trainers and what is included. The courses have "
        "started and some are full",
        "new", [], "g5", ["/ideas/experiences", "/customers"]),
    Doc("42-coffee-school-first-cohorts-report.md", "Coffee School: report on the first cohorts",
        "a report on the first six sessions of the coffee school that Lumen runs for the public: enrolments, attendance, "
        "revenue, the feedback of the students, what to change in the courses and the dates of the next cohorts",
        "new", [], "g5", ["/ideas/experiences", "/customers"]),
    # Added on 2026-10-06 with the user: the fallback reached 4 documents of 42 in the pilot, all new topics. Ten more
    # new topics, at several depths, so the menus are compared on more fallback decisions.
    Doc("43-decaf-processing-partners-compared.md", "Decaf processing partners compared",
        "a comparison of three decaffeination partners for the green coffee of Night Shift (Swiss Water, a sugarcane "
        "ethyl acetate plant in Colombia, a CO2 plant in Germany): method, minimum lot, cost per kg, lead time, cup results",
        "new", [], "g6", ["/sourcing/importers", "/products/coffees"]),
    Doc("44-decaf-processing-trial-results.md", "Decaf processing: trial lots results",
        "the results of two trial lots decaffeinated by two different processing partners: residual caffeine, moisture, "
        "roast behaviour, cupping scores and the decision on which partner to use from spring",
        "new", [], "g6", ["/sourcing/importers", "/products/coffees"]),
    Doc("45-webshop-platform-migration-plan.md", "Webshop platform migration plan",
        "the plan to move the online shop and the subscription billing to a new e-commerce platform: why, the timeline, "
        "data to migrate, payment provider, risks for subscribers, who does what",
        "new", [], "g7", ["/customers/subscriptions"]),
    Doc("46-payment-provider-switch-checklist.md", "Payment provider switch: checklist",
        "the checklist for switching the card payment provider of the webshop: contracts, fees per transaction, testing "
        "refunds, recurring mandates of subscribers, the go-live night and the rollback plan",
        "new", [], "g7", ["/customers/subscriptions"]),
    Doc("47-food-safety-audit-november-2026.md", "Food safety audit, November 2026",
        "the report of the yearly food safety inspection of the roastery by the local health authority: HACCP plan, "
        "pest control, cleaning records, allergen handling, findings and the deadlines to fix them",
        "new", [], "g8", ["/operations/roasting/quality"]),
    Doc("48-haccp-plan-revision.md", "HACCP plan revision",
        "the revised hazard analysis and critical control points plan of the roastery: hazards per step from green "
        "coffee intake to packing, critical limits, monitoring, corrective actions and records to keep",
        "new", [], "g8", ["/operations/roasting/quality"]),
    Doc("49-new-bag-artwork-brief.md", "New bag artwork: the brief",
        "the brief to the designer for new coffee bag artwork: the brand story, what each bag must show (origin, roast "
        "date, tasting notes, recycling mark), the colour per range, sizes and the deadline",
        "new", [], "g9", ["/products/coffees", "/operations/shipping/packing"]),
    Doc("50-label-printer-and-bag-supplier-quotes.md", "Label printer and bag printing quotes",
        "quotes for printing the new bags and for an in-house label printer: suppliers, minimum orders, price per "
        "thousand bags, lead times, the printer's cost and running cost, and a recommendation",
        "new", [], "g9", ["/products/coffees", "/operations/shipping/packing"]),
    Doc("51-insurance-renewal-2027.md", "Insurance renewal 2027",
        "the renewal of the roastery's insurance policies for 2027: property, product liability, business interruption, "
        "the broker's offers, premiums, deductibles and the changes asked by the insurer",
        "new", [], None, []),
    Doc("52-farmers-market-stall-weekends.md", "Farmers market stall: the weekends",
        "how the weekend stall at the Turin farmers market went: which Saturdays, staff, takings, best-selling coffees, "
        "costs of the pitch and the van, and whether to continue in the spring",
        "new", [], None, ["/ideas/experiences", "/customers"]),
]


def avoid(doc: Doc) -> str:
    """The descriptions of the folders the document may be filed in: the text must not repeat them."""
    labels = doc.folders or doc.alt_folders
    return " | ".join(deep.FOLDERS[f].rstrip(".") for f in labels if f in deep.FOLDERS)


def prompt(doc: Doc) -> str:
    lo, hi = MIN_CHARS + 300, MAX_CHARS - 600
    return f"""{WORLD}

Write one internal document of the roastery, in English: {doc.what}.
It is about {lo} to {hi} characters long. Plain Markdown, starting with a title line "# {doc.title}".
Concrete and specific: names of people, numbers, dates in autumn 2026, amounts, small tables as lists. Write it as the
document itself, in its natural form (a meeting has attendees, an email has a greeting), with no closing remarks.
Never mention the wiki, folders, filing or where the document belongs. Do not copy these phrases: {avoid(doc) or "(none)"}."""


def dry_text(doc: Doc) -> str:
    """A synthetic text of about 1,800 characters that holds the brief, no model."""
    para = f"{doc.what[0].upper()}{doc.what[1:]}. Prepared by the team on 2026-11-{int(doc.number) - 10:02d}."
    return f"# {doc.title}\n\n" + "\n\n".join(f"{para} Part {i + 1}." for i in range(max(1, 1800 // len(para)))) + "\n"


def write_one(client: httpx.Client | None, key: str, model: str, doc: Doc) -> str:
    if client is None:
        return dry_text(doc)
    text = ""
    for _ in range(3):  # a text outside the length asked for is written again
        text = ask(client, key, model, prompt(doc), 3000)
        if MIN_CHARS <= len(text) <= MAX_CHARS:
            break
        print(f"  {doc.filename}: {len(text)} characters, writing it again", file=sys.stderr)
    return text.strip() + "\n"


def labels_all() -> dict[str, dict[str, Any]]:
    return {**deep.growth_labels(), **{d.filename: d.label() for d in SPEC}}


def write(out: Path, only: Sequence[str] | None, model: str, dry_run: bool) -> None:
    deep.write_growth(out)
    todo = [d for d in SPEC if not only or d.number in only]
    key = "" if dry_run else Settings().config().llm.api_key
    with ThreadPoolExecutor(6) as pool, httpx.Client(timeout=600) as client:
        texts = list(pool.map(lambda d: write_one(None if dry_run else client, key, model, d), todo))
    for doc, text in zip(todo, texts, strict=True):
        (out / doc.filename).write_text(text, encoding="utf-8", newline="\n")
        print(f"{doc.filename}: {len(text):,} characters")
    about = (
        "Synthetic deep benchmark: the 30 growth documents with labels remapped to the deep demo, and 12 new documents "
        "(31-34 misleading surface, 35-38 ambiguous across branches, 39-42 new folders in two groups). Written by "
        "benchmarks/make_deep_corpus.py."
    )
    deep.write_labels(out, about, labels_all())
    cost = "free (dry run)" if dry_run else f"${ask.cost:.4f}"  # type: ignore[attr-defined]
    print(f"cost: {cost}")


# -- the check ---------------------------------------------------------------------------------------


def check(out: Path) -> list[str]:
    """Print the control table of the twelve new documents in `out`; return the problems (they start with "ERROR")."""
    problems = [f"ERROR {p}" for p in deep.verify_growth(out)]
    notes = demo_notes()
    labels: dict[str, Any] = {}
    if (out / "labels.json").is_file():
        labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))["docs"]
    for doc in SPEC:
        path = out / doc.filename
        if not path.is_file():
            problems.append(f"ERROR {doc.filename} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        score = sorted(((len(words(f"{doc.title} {text}") & w), rel) for rel, w in notes.items()), reverse=True)
        print(f"== {doc.filename}: {len(text):,} characters, {doc.kind} {doc.folders or doc.alt_folders}")
        print("   notes of the demo closest: " + ", ".join(f"{rel} ({n})" for n, rel in score[:3]))
        if not MIN_CHARS <= len(text) <= MAX_CHARS:
            problems.append(f"ERROR {doc.filename}: {len(text)} characters, not {MIN_CHARS}-{MAX_CHARS}")
        if labels.get(doc.filename) != doc.label():
            problems.append(f"ERROR {doc.filename}: its label in labels.json is not the one of the script")
        low = text.lower()
        named = [f for f in [*doc.folders, *doc.alt_folders] if re.search(rf"(?<![\w/]){re.escape(f)}(?![\w-])", low)]
        named += [d for f in doc.folders if (d := deep.FOLDERS.get(f, "")) and d.rstrip(".").lower() in low]
        if named:
            problems.append(f"warning {doc.filename}: names a folder or repeats its description: {', '.join(named)}")
    for name in sorted(labels.keys() - {d.filename for d in SPEC} - deep.growth_labels().keys()):
        problems.append(f"ERROR labels.json has {name}, which is not a document of the corpus")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--only", nargs="+", default=None, help="only these documents (their number, e.g. 31 35)")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--out", type=Path, default=deep.DEEP, help="the folder of the corpus (default benchmarks/corpus/deep)")
    p.add_argument("--check", action="store_true", help="only check the corpus in --out (free): exit 1 on an error")
    p.add_argument("--dry-run", action="store_true", help="synthetic texts, no model: free")
    args = p.parse_args(argv)
    if not args.check:
        write(args.out, args.only, args.model, args.dry_run)
    problems = check(args.out)
    for line in problems:
        print(line)
    errors = [line for line in problems if line.startswith("ERROR")]
    print(f"{len(SPEC)} new documents, {len(errors)} errors, {len(problems) - len(errors)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
