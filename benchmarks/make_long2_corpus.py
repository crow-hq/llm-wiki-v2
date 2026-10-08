"""Write the second long-document corpus for the stability benchmark (`benchmarks/corpus/long2/`): fifteen hard documents
about the Lumen demo roastery and their `labels.json`.

The outline of every document is fixed here (sections with heading, what they cover, folder and length): the LLM only
writes the text of each section. Facts (a headline and some marginal details per section) are planted by a seeded
generator and written into the prompts to be included word for word; the labels are computed from the real texts.

    python benchmarks/make_long2_corpus.py [--only 03 11] [--model deepseek/deepseek-v4-flash]
    python benchmarks/make_long2_corpus.py --check             # free: the control table of the corpus, exit 1 on a defect
    python benchmarks/make_long2_corpus.py --dry-run --out DIR  # a fake model: synthetic texts that hold the facts, free

It costs about $0.22 with deepseek-v4-flash (documents 13 and 14 are copies of 01, made without the model, 14 apart from
its new section). Written once and committed: the benchmark reads the files, it never calls this script.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import zlib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # corpus_common is a sibling script
sys.path.insert(0, str(HERE.parent / "src"))

import yaml  # noqa: E402
from corpus_common import CHARS_PER_PAGE, WORLD, ask, render  # noqa: E402

from llmw2.agents import state  # noqa: E402

OUT = HERE / "corpus" / "long2"
DEMO = HERE.parent / "src" / "llmw2" / "demo"
SEED = 20261006
PART_MAX = 12000  # a section longer than this is written in several calls
SEPARATOR = "====="  # between the messages the model writes for the mailbox (document 15)
QUOTA_TOLERANCE = 10.0  # points between the planned and the real share of a folder
MODEL = "deepseek/deepseek-v4-flash"


# -- the specification, as data ----------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    heading: str
    covers: str
    folder: str  # the folder of the wiki where this part of the document belongs
    chars: int  # planned length


@dataclass(frozen=True)
class Doc:
    id: str
    slug: str
    title: str
    lang: str  # "en" | "it"
    layout: str  # "markdown" | "flat-numbered" | "flat-caps" | "flat-articles" | "email"
    summary: str | None  # heading of the native summary section (written last), if any
    pages: int
    what: str
    kind: str  # the label: existing | ambiguous | new | update
    folders: tuple[str, ...]
    sections: tuple[Section, ...]  # without the summary; for a copy, only the ones it adds to its parent's
    merge_into: tuple[str, ...] = ()
    merge_ok: tuple[str, ...] = ()
    merge_with_doc: str | None = None
    group: str | None = None
    alt_folders: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()  # demo notes that the document updates (the generator is given them)
    change: str = ""  # what changes with respect to the notes
    summary_note: str = ""  # an instruction for the native summary (document 12: misleading on purpose)
    summary_chars: int = 2500
    copy_of: str | None = None  # id of the document this one is a deterministic copy of
    rev: int = 0
    min_offsets: dict[str, int] = field(default_factory=dict)  # heading -> the offset the section must start beyond
    min_total: int = 0
    no_headings: bool = False

    @property
    def filename(self) -> str:
        return f"{self.id}-{self.slug}.md"


def plan(pages: int, summary_chars: int, groups: list[tuple[str, float, list[tuple[str, str]]]]) -> tuple[Section, ...]:
    """Sections from (folder, share of the characters, [(heading, covers)]): a folder's share is split among its sections."""
    body = pages * CHARS_PER_PAGE - summary_chars
    return tuple(
        Section(heading, covers, folder, round(body * share / len(items))) for folder, share, items in groups for heading, covers in items
    )


D01 = Doc(
    "01", "wholesale-programme-review-2026", "Wholesale programme review 2026", "en", "markdown", "Executive summary", 22,
    "the yearly review of Lumen's wholesale programme: accounts, volumes, complaints, credit terms, product mix, wholesale "
    "price list and deliveries to cafés",
    "existing", ("/customers",),
    plan(22, 2500, [
        ("/customers", 0.6, [
            ("Active wholesale accounts", "the cafés that buy from Lumen, how long, what they are like"),
            ("Volumes by account and month", "kilos per account and month, growth and seasonality"),
            ("Complaints and credit notes", "the quality and delivery complaints of the year and how they were settled"),
            ("Credit terms and payment behaviour", "payment terms, late payers, credit limits"),
            ("Account health and churn risk", "which accounts are at risk and what Lumen does about it"),
        ]),
        ("/products", 0.3, [
            ("Product mix sold to wholesale", "which coffees the cafés buy and in which share"),
            ("Wholesale price list", "the wholesale prices per product and how they changed"),
        ]),
        ("/operations/shipping", 0.1, [("Delivery to wholesale accounts", "how orders reach the cafés: days, carriers, problems")]),
    ]),
    merge_ok=("customers/cafe-portello.md", "customers/wholesale-cafe-onboarding.md", "products/price-list-from-july-2026.md"),
)
D02 = Doc(
    "02", "second-roaster-feasibility-study", "Second roaster feasibility study", "en", "flat-numbered", "Introduction", 24,
    "the feasibility study for buying a second roaster: capacity, candidate machines, space and utilities, costs, revenue "
    "scenarios, product ideas it would enable, green coffee needs and risks",
    "ambiguous", ("/operations/roasting", "/ideas", "/operations"),
    plan(24, 2500, [
        ("/operations/roasting", 0.5, [
            ("Current roaster capacity and utilisation", "how busy the existing roaster is and where it limits Lumen"),
            ("Candidate roasters compared", "the machines considered, their capacity and features"),
            ("Installation, space and utilities", "where it would go, gas, power, exhaust and works"),
            ("Operating cost and staffing", "running costs, maintenance and who would run it"),
        ]),
        ("/ideas", 0.3, [
            ("Revenue scenarios from added capacity", "what extra capacity could sell in three scenarios"),
            ("Product ideas the second roaster would enable", "new products and roast styles it would allow"),
            ("Risks and alternatives to buying", "what could go wrong and options such as toll roasting"),
        ]),
        ("/sourcing", 0.2, [
            ("Green coffee needs at higher volume", "how much more green coffee would be needed and when"),
            ("Importer capacity and lead times", "whether importers can supply the extra volume"),
        ]),
    ]),
    merge_ok=("operations/roasting/roaster-maintenance.md", "meetings/board-meeting-june-2026.md"),
)
D03 = Doc(
    "03", "relazione-semestrale-acquisti-prezzi-2026", "Relazione semestrale acquisti e prezzi 2026", "it", "flat-caps", None, 20,
    "la relazione semestrale (in italiano) sugli acquisti di caffè verde e sui prezzi: origini, importatori, coperture sul "
    "mercato C, costo per miscela, effetti sul listino e sui clienti",
    "existing", ("/sourcing",),
    plan(20, 0, [
        ("/sourcing", 0.55, [
            ("Acquisti di caffè verde per origine", "quantità e lotti acquistati nel semestre per origine"),
            ("Andamento dei prezzi del mercato C e coperture", "come si sono mossi i prezzi e cosa è stato coperto"),
            ("Condizioni con gli importatori", "le condizioni contrattuali con gli importatori"),
            ("Qualità dei lotti e reclami ai fornitori", "la qualità dei lotti arrivati e i reclami ai fornitori"),
        ]),
        ("/products", 0.35, [
            ("Costo del caffè verde per miscela", "quanto pesa il caffè verde sul costo di ogni miscela"),
            ("Effetto dei prezzi sul listino", "come i prezzi d'acquisto hanno spinto il listino"),
        ]),
        ("/customers", 0.1, [("Impatto sui clienti wholesale", "come i clienti wholesale hanno reagito agli aumenti")]),
    ]),
    merge_ok=("sourcing/green-coffee-price-hedging.md", "products/price-list-from-july-2026.md", "sourcing/andes-direct-importer.md"),
    summary_chars=0,
)
D04 = Doc(
    "04", "decaf-training-programme-wholesale-cafes", "Decaf training programme for wholesale cafés", "en", "markdown", None, 16,
    "a programme to train the staff of wholesale cafés to serve Night Shift decaf: why, sessions, the coffee itself, "
    "brewing, ordering and follow-up",
    "ambiguous", ("/customers", "/products"),
    plan(16, 0, [
        ("/customers", 0.5, [
            ("Why wholesale cafés need decaf training", "the café side: complaints and lost sales with decaf"),
            ("Training sessions for café staff", "the format, duration and agenda of the sessions"),
            ("Café feedback and follow-up", "how feedback is collected and what follows"),
        ]),
        ("/products", 0.5, [
            ("Night Shift decaf: process and flavour", "how the decaf is made and what it tastes like"),
            ("Brewing Night Shift decaf: recipes and dial-in", "recipes for espresso and filter and how to dial in"),
            ("Decaf ordering and pricing for cafés", "pack sizes, prices and how cafés order decaf"),
        ]),
    ]),
    merge_ok=("customers/cafe-portello.md", "products/night-shift-decaf.md"),
    summary_chars=0,
)
D05 = Doc(
    "05", "subscription-box-redesign-proposal", "Subscription box redesign proposal", "en", "markdown", "Summary", 18,
    "a proposal to redesign the subscription box: reasons, new formats and tiers, pause instead of cancel, packing, "
    "carriers and costs, subscriber research",
    "ambiguous", ("/ideas", "/operations/shipping"),
    plan(18, 2500, [
        ("/ideas", 0.5, [
            ("Why redesign the subscription box", "the problems of today's box and the opportunity"),
            ("New box formats and tiers", "the formats, tiers and contents proposed"),
            ("Pause instead of cancel in the new design", "letting subscribers pause, how and with what effect"),
        ]),
        ("/operations/shipping", 0.4, [
            ("Packing the new boxes", "how packing day changes with the new formats"),
            ("Carriers and costs of the new formats", "carrier services and shipping cost per format"),
            ("Packing day schedule and staffing", "who packs what and when on packing day"),
        ]),
        ("/customers", 0.1, [("Subscriber research and feedback", "what subscribers said in surveys and interviews")]),
    ]),
    merge_ok=("operations/shipping/subscription-packing-day.md", "ideas/pause-instead-of-cancel.md"),
)
D06 = Doc(
    "06", "carbon-footprint-assessment-2026", "Carbon footprint assessment 2026", "en", "markdown", "Executive summary", 20,
    "the carbon footprint assessment of Lumen for 2026: method, emissions per phase of the supply chain, reduction targets",
    "new", (),
    plan(20, 2500, [
        ("/sustainability", 1.0, [
            ("Scope and method", "the boundaries, standards and calculation method used"),
            ("Emissions from green coffee farming and transport", "emissions from origin to the roastery"),
            ("Emissions from roasting and energy", "emissions of the roastery's energy and roasting"),
            ("Emissions from packaging and shipping", "emissions of packaging materials and deliveries"),
            ("Reduction targets 2027-2030", "the targets and the actions to reach them"),
            ("Data quality and limits", "how reliable the figures are and what is estimated"),
        ]),
    ]),
    group="sust", alt_folders=("/operations",),
)
D07 = Doc(
    "07", "esg-report-banca-sella-credit-line", "ESG report for Banca Sella credit line", "en", "flat-numbered", "Purpose", 18,
    "an ESG report that Lumen prepares for its bank (Banca Sella) in support of a credit line: environmental and social "
    "indicators, governance and commitments",
    "new", (),
    plan(18, 2500, [
        ("/sustainability", 1.0, [
            ("Environmental indicators", "energy, water, waste and emissions indicators"),
            ("Social indicators", "employment, safety, training and community indicators"),
            ("Governance and risk management", "how the company is governed and manages ESG risks"),
            ("Commitments tied to the credit line", "the commitments Lumen makes to the bank and how they are monitored"),
        ]),
    ]),
    group="sust",
)
D08 = Doc(
    "08", "politica-sostenibilita-codice-condotta-fornitori", "Politica di sostenibilità e codice di condotta fornitori", "it",
    "flat-articles", "Premessa", 16,
    "la politica di sostenibilità di Lumen e il codice di condotta per i fornitori (in italiano, articoli): principi, "
    "requisiti per i fornitori, verifiche",
    "new", (),
    plan(16, 2500, [
        ("/sustainability", 1.0, [
            ("Principi della politica di sostenibilità", "i principi e gli obiettivi dell'azienda"),
            ("Requisiti per i fornitori", "cosa Lumen richiede ai fornitori in tema sociale e ambientale"),
            ("Verifiche e audit", "come i requisiti sono verificati"),
            ("Violazioni e miglioramento continuo", "cosa accade in caso di violazioni e come si migliora"),
        ]),
    ]),
    group="sust", alt_folders=("/sourcing",),
)
D09 = Doc(
    "09", "aurora-espresso-blend-2027-recipe-change", "Aurora espresso blend: 2027 recipe change", "en", "markdown", None, 10,
    "the plan to change the recipe of the Aurora espresso blend in 2027, from two origins to three",
    "update", ("/products",),
    plan(10, 0, [
        ("/products", 1.0, [
            ("Why the recipe changes", "the reasons: supply, flavour and consistency"),
            ("The new three-origin recipe", "the new proportions and the role of each origin"),
            ("Sensory results of the trials", "cupping and tasting results of the trial blends"),
            ("Rollout to cafés and subscribers", "how and when the change reaches customers"),
        ]),
    ]),
    merge_into=("products/aurora-espresso-blend.md",),
    notes=("products/aurora-espresso-blend.md",),
    change="the recipe changes from 60% Brazil Cerrado and 40% Ethiopia Guji to 50/30/20: the same two origins reduced and a "
    "third origin (a real coffee origin of your choice) added at 20%. Do not give prices or roast profiles.",
    summary_chars=0,
)
D10 = Doc(
    "10", "shipping-terms-revision-2027", "Shipping terms revision 2027", "en", "flat-numbered", "Purpose", 20,
    "the revision of Lumen's shipping terms for 2027: a new carrier, the free shipping threshold, delivery times and the "
    "subscription packing day",
    "update", ("/operations/shipping",),
    plan(20, 2500, [
        ("/operations/shipping", 1.0, [
            ("New carrier selection", "why and how the new carrier was chosen and what it replaces"),
            ("Free shipping threshold", "the new threshold for free shipping and its effect"),
            ("Delivery times by zone", "the delivery times promised per zone"),
            ("Wednesday packing day", "subscription packing moves to Wednesday: workflow and cut-offs"),
            ("Subscription box handling", "how subscription boxes are handled and labelled"),
            ("Returns and damaged parcels", "the procedure for returns and damaged parcels"),
        ]),
    ]),
    merge_into=("operations/shipping/carriers-and-delivery-times.md", "operations/shipping/subscription-packing-day.md"),
    notes=("operations/shipping/carriers-and-delivery-times.md", "operations/shipping/subscription-packing-day.md"),
    change="a new carrier replaces the current one, the threshold for free shipping changes, and subscription packing day "
    "moves to Wednesday.",
)
D11 = Doc(
    "11", "green-coffee-purchasing-archive-2020-2026-and-2027-terms", "Green coffee purchasing archive 2020-2026 and 2027 terms",
    "en", "markdown", None, 55,
    "a long archive of Lumen's green coffee purchases from 2020 to 2026 (lot by lot, as a running record) followed by the "
    "2027 purchasing terms that change the hedging rule",
    # Label corrected on 2026-10-06 (round 2 of task 6): was "update" with merge_into the hedging note. Six years of archive
    # plus the 2027 terms: merging into the note or keeping a note of its own are both reasonable.
    "existing", ("/sourcing",),
    (
        Section("Purchasing archive 2020-2026", "a running record of every green coffee purchase from 2020 to 2026: lots, "
                "importers, quantities, prices, quality notes and decisions, year after year, as continuous prose", "/sourcing", 108000),
        *plan(19, 0, [
            ("/sourcing", 1.0, [
                ("2027 terms: the new hedging rule", "the new hedging rule that replaces the current one"),
                ("2027 terms: volumes and importers", "the volumes and importers for 2027"),
                ("2027 terms: payment and credit", "payment terms and credit with the importers"),
                ("2027 terms: quality and claims", "quality specifications and claims for 2027"),
                ("2027 terms: implementation timeline", "when and how the 2027 terms take effect"),
            ]),
        ]),
    ),
    merge_ok=("sourcing/green-coffee-price-hedging.md",),
    notes=("sourcing/green-coffee-price-hedging.md",),
    change="the 2027 terms change the hedging rule of the note (cover 70% of needs, up to six months ahead) into a different rule.",
    min_offsets={"2027 terms: the new hedging rule": 100000},
)
D12 = Doc(
    "12", "quality-control-laboratory-manual", "Quality control laboratory manual", "en", "flat-numbered", "Executive summary", 22,
    "the manual of Lumen's quality control laboratory: organisation, sampling, moisture, colour, cupping, calibration, "
    "non-conforming lots",
    "existing", ("/operations/roasting",),
    plan(22, 2500, [
        ("/operations/roasting", 1.0, [
            ("Laboratory organisation and responsibilities", "who does what in the laboratory"),
            ("Sample taking and preparation", "how samples are taken and prepared"),
            ("Moisture and density measurement", "the procedures and tolerances for moisture and density"),
            ("Colour measurement", "the colour measurement procedure and the target values"),
            ("Cupping protocol and scoring", "the cupping protocol, forms and scoring"),
            ("Calibration and records", "calibration of the instruments and record keeping"),
            ("Handling non-conforming lots", "what the laboratory does with a lot that fails"),
        ]),
    ]),
    merge_ok=("operations/roasting/quality-control-and-cupping.md",),
    summary_note="Despite the real content, the summary describes the document as mostly about handling customer complaints, "
    "refunds and credit notes: it must mislead a reader about the main topic. Do not say that it is about a laboratory manual.",
)
D13 = replace(
    D01, id="13", slug="wholesale-programme-review-2026-rev2", title="Wholesale programme review 2026 — rev. 2", kind="update",
    sections=(), merge_ok=(), merge_with_doc=D01.filename, copy_of="01", rev=2,
)
D14 = replace(
    D01, id="14", slug="wholesale-programme-review-2026-rev3", title="Wholesale programme review 2026 — rev. 3", kind="update",
    sections=(
        Section("Addendum: new accounts and renegotiated terms", "wholesale accounts won or renegotiated after the review, and "
                "their terms (about six pages)", "/customers", 18000),
    ),
    merge_ok=(), merge_with_doc=D13.filename, copy_of="13", rev=3,
)
D15 = Doc(
    "15", "cafe-complaints-mailbox-2025-2026", "Café complaints mailbox, 2025-2026", "en", "email", None, 42,
    "a export of the complaints mailbox of Lumen from 2025 to 2026: threads of emails from cafés about quality, deliveries, "
    "invoices and refunds, with the replies of the staff",
    "existing", ("/customers",),
    (Section("Complaints mailbox", "email threads between cafés and Lumen about complaints, refunds and replacements",
             "/customers", 100000),),
    merge_ok=("customers/refund-policy.md", "customers/cafe-portello.md"), summary_chars=0, min_total=120000, no_headings=True,
)
SPEC: list[Doc] = [D01, D02, D03, D04, D05, D06, D07, D08, D09, D10, D11, D12, D13, D14, D15]
DOCS = SPEC
BY_ID = {d.id: d for d in SPEC}


def body_sections(doc: Doc) -> tuple[Section, ...]:
    """The sections of a document that are not its native summary: for a copy, those of its parent and its own."""
    return (body_sections(BY_ID[doc.copy_of]) if doc.copy_of else ()) + doc.sections


def summary_heading(doc: Doc) -> str | None:
    return summary_heading(BY_ID[doc.copy_of]) if doc.copy_of else doc.summary


def parts_of(section: Section) -> int:
    return max(1, math.ceil(section.chars / PART_MAX))


# -- the facts ---------------------------------------------------------------------------------------

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre",
           "dicembre"],
}
FIRST = {"en": ["Verena", "Callum", "Marisol", "Tobias", "Ingrid", "Desmond", "Odette", "Ravi"],
         "it": ["Ilaria", "Brando", "Ottavia", "Corrado", "Eleonora", "Tullio", "Fiorenza", "Gaetano"]}
LAST = {"en": ["Calloway", "Thorne", "Vasquez", "Pemberton", "Okafor", "Lindqvist", "Harrow", "Delacroix"],
        "it": ["Vandelli", "Scarpellini", "Morandotti", "Ghiberti", "Casadei", "Pellegrini", "Orsolini", "Brambilla"]}
KINDS = ("code", "amount", "date", "percent", "name")
KIND_WORDS = {
    "code": "a reference code (of a lot, contract, case or document)", "amount": "an amount of money", "date": "a date",
    "percent": "a percentage", "name": "the name of a person (invented) involved",
}


def make_value(rng: random.Random, kind: str, lang: str) -> str:
    """A value of a kind, formatted as the language of the document writes it ("3,85 €" in Italian, "€3.85" in English)."""
    if kind == "code":
        return "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXZ") for _ in range(3)) + f"-{rng.randint(1000, 9999)}"
    if kind == "amount":
        value = f"{rng.choice([rng.uniform(3, 90), rng.uniform(100, 9000), rng.uniform(10000, 90000)]):,.2f}"
        return f"€{value}" if lang == "en" else value.translate(str.maketrans(",.", ".,")) + " €"
    if kind == "date":
        month = MONTHS[lang][rng.randrange(12)]
        return f"{rng.randint(1, 28)} {month} {rng.choice([2026, 2027, 2028])}"
    if kind == "percent":
        number = f"{rng.randint(11, 97)}.{rng.randint(1, 9)}"
        return number + "%" if lang == "en" else number.replace(".", ",") + "%"
    return f"{rng.choice(FIRST[lang])} {rng.choice(LAST[lang])}"


def digits_of(value: str) -> str:
    return re.sub(r"\D", "", value) or value


def fresh_value(rng: random.Random, kind: str, lang: str, used: set[str]) -> str:
    """A value that is not in `used` (neither as written nor as its digits), which it joins."""
    while True:
        value = make_value(rng, kind, lang)
        if value not in used and digits_of(value) not in used:
            used.update((value, digits_of(value)))
            return value


def assign_parts(facts: list[dict[str, Any]], n: int) -> list[list[dict[str, Any]]]:
    """Which part of a section (written in `n` calls) holds each fact: the headline in a part fixed by its value, a marginal in each."""
    parts: list[list[dict[str, Any]]] = [[] for _ in range(n)]
    marginal = 0
    for f in facts:
        if f["kind"] == "headline":
            parts[sum(map(ord, f["fact"])) % n].append(f)
        else:
            parts[marginal % n].append(f)
            marginal += 1
    return parts


def plant_facts(doc: Doc, seed: int = SEED) -> list[dict[str, Any]]:
    """The facts of a document, from a seeded generator: [{"fact", "kind": "headline"|"marginal", "section", ("supersedes")}].

    A section that is not the summary gets one headline and one marginal fact per part written. A copy (13, 14) has its
    parent's facts, four headline ones changed (`supersedes` is the old value), and the facts of its own new sections.
    """
    if doc.copy_of:
        return revise(doc, seed)[0]
    return own_facts(doc, doc.sections, random.Random(f"{seed}:{doc.id}"), set())


def own_facts(doc: Doc, sections: Sequence[Section], rng: random.Random, used: set[str]) -> list[dict[str, Any]]:
    facts = []
    for s in sections:
        kinds = [rng.choice(KINDS) for _ in range(1 + parts_of(s))]
        facts.append({"fact": fresh_value(rng, kinds[0], doc.lang, used), "kind": "headline", "section": s.heading})
        facts += [{"fact": fresh_value(rng, k, doc.lang, used), "kind": "marginal", "section": s.heading} for k in kinds[1:]]
    return facts


def revise(doc: Doc, seed: int = SEED) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    """The facts of a copy and its changes [(old value, new value, section)]: four headline facts of its parent, new values.

    The four are chosen among those not changed by the parent first (`supersedes` of the parent's facts marks the others).
    """
    parent = BY_ID[str(doc.copy_of)]
    before = plant_facts(parent, seed)
    rng = random.Random(f"{seed}:{doc.id}")
    headline = [f for f in before if f["kind"] == "headline"]
    candidates = [f for f in headline if "supersedes" not in f] + [f for f in headline if "supersedes" in f]
    chosen = {f["fact"] for f in candidates[:4]}
    used = {f["fact"] for f in before} | {digits_of(f["fact"]) for f in before}
    used |= {f["supersedes"] for f in before if "supersedes" in f}
    changes, facts = [], []
    for f in before:
        if f["fact"] in chosen:
            new = fresh_value(rng, _kind_of_value(f["fact"]), doc.lang, used)
            changes.append((f["fact"], new, f["section"]))
            facts.append({"fact": new, "kind": "headline", "section": f["section"], "supersedes": f["fact"]})
        else:
            facts.append({k: v for k, v in f.items() if k != "supersedes"})
    return facts + own_facts(doc, doc.sections, rng, used), changes


def _kind_of_value(value: str) -> str:
    """The kind of a value as `make_value` writes it."""
    if re.fullmatch(r"[A-Z]{3}-\d{4}", value):
        return "code"
    if "%" in value:
        return "percent"
    if "€" in value:
        return "amount"
    if re.match(r"\d{1,2} \w+ \d{4}$", value):
        return "date"
    return "name"


def fact_pattern(fact: str) -> re.Pattern[str]:
    """The fact as a whole token (not inside a longer number, code or word)."""
    return re.compile(r"(?<![\w.,-])" + re.escape(fact) + r"(?!\w|[.,]\d)")


def count_fact(text: str, fact: str) -> int:
    return len(fact_pattern(fact).findall(text))


# -- the texts ---------------------------------------------------------------------------------------

Writer = Callable[[str, int, list[str], bool], str]  # (prompt, characters wanted, facts to include, in mailbox form) -> text
LANGUAGE = {"en": "English", "it": "Italian"}


def read_note(rel: str) -> tuple[str, str, str]:
    """(title, summary, body) of a demo note; one that is not in the demo (it grows into the wiki) comes from its raw copy."""
    path = DEMO / rel
    if not path.exists():
        found = sorted(DEMO.glob(f"raw/*-{Path(rel).stem}.md"))
        if not found:
            raise FileNotFoundError(rel)
        path = found[0]
    text = path.read_text(encoding="utf-8")
    front, _, body = text.removeprefix("---\n").partition("\n---\n") if text.startswith("---") else ("", "", text)
    meta = yaml.safe_load(front) if front else {}
    return str(meta.get("title") or Path(rel).stem), str(meta.get("description") or ""), body.strip()


def update_context(doc: Doc) -> str:
    if not doc.notes:
        return ""
    out = ["This document updates what the wiki already holds. The existing note(s) of the wiki:"]
    for rel in doc.notes:
        title, summary, body = read_note(rel)
        out.append(f'Note "{title}" (summary: {summary or "none"}):\n{body[:3000]}')
    out.append(f"What changes with respect to the note(s): {doc.change}")
    return "\n\n".join(out)


def facts_text(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return ""
    lines = ["Include these facts in your text word for word, exactly as written between the quotes (they are invented):"]
    for f in facts:
        how = ("the key fact of this section: state it plainly and prominently, as the central figure or decision"
               if f["kind"] == "headline" else "a minor detail: mention it once, in passing")
        lines.append(f'- "{f["fact"]}" ({KIND_WORDS[_kind_of_value(f["fact"])]}; {how})')
    lines.append("Each of them must appear exactly once in your text.")
    return "\n".join(lines)


def section_prompt(doc: Doc, s: Section, facts: list[dict[str, Any]], k: int, n: int, tail: str) -> str:
    language = LANGUAGE[doc.lang]
    toc = chr(10).join(f"- {x.heading}: {x.covers}" for x in body_sections(doc))
    part = f" This is part {k + 1} of {n} of the section: it continues the text below, without repeating it." if n > 1 else ""
    after = f"\n\nThe previous part ends with:\n{tail}" if tail else ""
    last = "" if k == n - 1 else " Do not conclude: the section goes on in the next part."
    if doc.layout == "email":
        shape = (f"Write about {max(4, (s.chars // n) // 650)} emails of a mailbox, one after the other, in {language}: the messages of "
                 f"cafés and customers and the replies of Lumen's staff. Separate the emails with a line containing only {SEPARATOR}. "
                 "Write only the body of each email (no From, To, Date or Subject lines, no headings, no quoted replies), "
                 "starting with a greeting and ending with a signature of one line.")
    else:
        shape = ("Plain paragraphs and plain lists (no tables, no Markdown, no headings of any kind, no '#' or '**'). "
                 "Do not write the section heading itself. No closing remarks.")
    return f"""{WORLD}

You are writing the document "{doc.title}" ({doc.what}), in {language}. Its sections are:
{toc}

Write only the section "{s.heading}" ({s.covers}), about {s.chars // n} characters.{part}{last}
Stay on the topic of this section: other parts of the business may be named only as context, never described (no prices,
roast profiles, commercial terms or procedures that belong to another topic). Concrete and specific: names, numbers,
dates, amounts. {shape}

{facts_text(facts)}{after}

{update_context(doc)}""".strip()


def summary_prompt(doc: Doc) -> str:
    language = LANGUAGE[doc.lang]
    toc = chr(10).join(f"- {x.heading}: {x.covers}" for x in body_sections(doc))
    note = f"\n{doc.summary_note}" if doc.summary_note else ""
    return f"""{WORLD}

You are writing the section "{summary_heading(doc)}" of the document "{doc.title}" ({doc.what}), in {language}. It summarises
the whole document in a few paragraphs, about {doc.summary_chars} characters. The sections of the document are:
{toc}
Write no figures, no codes, no dates and no names of people: only words. Plain paragraphs, no Markdown, no heading.{note}"""


def clean(text: str) -> str:
    """The text of a part without Markdown headings, bold and the like (a section has no heading of its own inside it)."""
    lines = [re.sub(r"^\s{0,3}#+\s*", "", line).replace("**", "") for line in text.strip().splitlines()]
    return "\n".join(lines).strip()


def write_part(writer: Writer, doc: Doc, prompt: str, chars: int, facts: list[dict[str, Any]]) -> str:
    """One part: asked again if a fact is not there exactly once, continued if too short, patched if a fact still lacks."""
    mailbox = doc.layout == "email"
    values = [f["fact"] for f in facts]
    text = ""
    for _ in range(3):
        text = clean(writer(prompt, chars, values, mailbox))
        for _ in range(2):  # too short: the model often writes less than asked
            if len(text) >= 0.85 * chars:
                break
            more = writer(f"{prompt}\n\nYou wrote the text below, which is too short. Write its continuation, about "
                          f"{chars - len(text)} more characters, without repeating anything and without any of the facts "
                          f"listed above:\n{text[-1500:]}", chars - len(text), [], mailbox)
            text += ("\n" + SEPARATOR + "\n" if mailbox else "\n\n") + clean(more)
        if all(count_fact(text, v) == 1 for v in values):
            return text
        print("  retry: a fact is not exactly once", file=sys.stderr)
    for v in values:
        if count_fact(text, v) == 0:
            print(f"  patched a missing fact: {v}", file=sys.stderr)
            text += f"\n\n(Ref. {v})"
    return text


def write_section(writer: Writer, doc: Doc, s: Section, facts: list[dict[str, Any]]) -> str:
    n = parts_of(s)
    tail, out = "", []
    for k, mine in enumerate(assign_parts([f for f in facts if f["section"] == s.heading], n)):
        text = write_part(writer, doc, section_prompt(doc, s, mine, k, n, tail), s.chars // n, mine)
        out.append(text)
        tail = text[-1200:]
    return ("\n" + SEPARATOR + "\n" if doc.layout == "email" else "\n\n").join(out)


def email_layout(bodies: list[str], doc: Doc, facts: list[str], seed: int = SEED) -> str:
    """A mailbox as flat text: From, To, Date and Subject lines, the body, a quote of the message answered, a rule.

    The quote never holds a fact (it would repeat it). No line can be read as a heading by the routing state (see
    `state.py`): one that could ends with a full stop.
    """
    rng = random.Random(f"{seed}:{doc.id}:mail")
    messages = [m.strip() for body in bodies for m in re.split(rf"^\s*{SEPARATOR}\s*$", body, flags=re.M) if m.strip()]
    subjects = ["Refund for a damaged parcel", "Late delivery of the weekly box", "Wrong grind in my order", "Question about the invoice",
                "Bitter espresso since the last batch", "Missing bags in the delivery", "Decaf tastes flat", "Credit note not received",
                "Machine problems after the new blend", "Order cancelled by mistake"]
    senders = ["Café Portello", "Bar Centrale", "Torrefazione Alba", "Caffè Gioia", "Pasticceria Moretti", "Osteria del Ponte"]
    staff = ["Lumen support", "Giulia from Lumen", "Marco from Lumen", "Lumen accounts"]
    when, lines, subject, previous = datetime(2025, 1, 6, 8, 0), [], subjects[0], None
    for i, body in enumerate(messages):
        thread_start = i % 4 == 0
        if thread_start:
            subject, previous = rng.choice(subjects), None
        reply = i % 2 == 1  # a thread goes back and forth between a customer and the staff
        name = rng.choice(staff) if reply else rng.choice(senders)
        when += timedelta(minutes=rng.randint(20, 900))
        stamp = when.strftime("%a, %d %b %Y %H:%M")
        lines += [f"From: {name} <{re.sub(r'[^a-z]', '', name.lower())}@mail.example>", "To: complaints@lumen.example", f"Date: {stamp}",
                  f"Subject: {subject if thread_start else 'Re: ' + subject}", ""]
        lines += [line.strip() for line in body.splitlines()]
        if previous and not thread_start:
            quote = [ln for ln in previous.splitlines() if ln.strip() and not any(f in ln for f in facts)][:2]
            if quote:
                lines += ["", f"On {stamp}, the previous sender wrote:", *(f"> {ln.strip()[:160]}" for ln in quote)]
        lines += ["", "-----", ""]
        previous = body
    out: list[str] = []
    for i, line in enumerate(lines):  # a line that the state could take for a heading gets a full stop
        before, after = (lines[i - 1] if i else ""), (lines[i + 1] if i + 1 < len(lines) else "")
        out.append(line + "." if state._heading(line, before, after, False) else line)
    return "\n".join(out).strip() + "\n"


def revision_line(doc: Doc, changes: list[tuple[str, str, str]]) -> str:
    sections = ", ".join(sorted({c[2] for c in changes}))
    added = " A new section was added at the end." if doc.sections else ""
    return f"Revision {doc.rev}: the headline figures of these sections were updated: {sections}.{added}"


def derive_text(doc: Doc, parent_text: str, seed: int = SEED, new_bodies: Sequence[str] = ()) -> str:
    """A copy of the parent text, deterministic: four headline facts replaced, the title and a revision line, new sections."""
    parent = BY_ID[str(doc.copy_of)]
    _, changes = revise(doc, seed)
    text = parent_text
    for old, new, _ in changes:
        text = fact_pattern(old).sub(new, text)
    head, _, rest = text.partition("\n")
    if head != f"# {parent.title}":
        raise ValueError(f"the text of {parent.id} does not start with its title")
    out = f"# {doc.title}\n\n{revision_line(doc, changes)}\n{rest}"
    for s, body in zip(doc.sections, new_bodies, strict=True):
        out = out.rstrip("\n") + f"\n\n## {s.heading}\n\n{body}\n"
    return out


def write_doc(writer: Writer, doc: Doc, seed: int, pool: ThreadPoolExecutor, parent_text: str | None = None) -> str:
    """The text of a document: its sections in parallel, the native summary last, then the layout."""
    facts = plant_facts(doc, seed)
    if doc.copy_of:
        if parent_text is None:
            raise ValueError(f"{doc.id} is a copy of {doc.copy_of}: its text is needed")
        mine = [f for f in facts if f["section"] in {s.heading for s in doc.sections}]
        bodies = [write_section(writer, doc, s, mine) for s in doc.sections]
        return derive_text(doc, parent_text, seed, bodies)
    bodies = list(pool.map(lambda s: write_section(writer, doc, s, facts), doc.sections))
    if doc.layout == "email":
        return email_layout(bodies, doc, [f["fact"] for f in facts], seed)
    heading = summary_heading(doc)
    names: list[dict[str, str]] = [{"heading": s.heading} for s in doc.sections]
    if heading:
        bodies.insert(0, clean(writer(summary_prompt(doc), doc.summary_chars, [], False)))
        names.insert(0, {"heading": heading})
    return render((doc.id, doc.title, doc.lang, doc.layout), names, bodies)  # type: ignore[arg-type]


# -- the fake model (--dry-run) ----------------------------------------------------------------------

SYLLABLES = ["zo", "kra", "vu", "mel", "tix", "dro", "sa", "pho", "jun", "qui", "bax", "lef"]


def fake_writer(seed: int = SEED) -> Writer:
    """A model that writes nothing real: nonsense words of the length asked, with every fact once, in its own paragraph."""

    def write(prompt: str, chars: int, facts: list[str], mailbox: bool) -> str:
        rng = random.Random(f"{seed}:{zlib.crc32(prompt.encode('utf-8'))}")  # per prompt: the same text whatever the thread order

        def word() -> str:
            return "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 3)))

        size, paragraphs = 380 if not mailbox else 520, []
        while sum(len(p) + 2 for p in paragraphs) < chars:
            paragraphs.append(" ".join(word() for _ in range(size // 6)) + ".")
        for i, f in enumerate(facts):
            paragraphs.insert(round((i + 1) * len(paragraphs) / (len(facts) + 1)), f"{word()} {f} {word()}.")
        if mailbox:
            return f"\n{SEPARATOR}\n".join(f"Hello {word()},\n\n{p}\n\nRegards,\n{word()}" for p in paragraphs)
        return "\n\n".join(paragraphs)

    return write


# -- the labels --------------------------------------------------------------------------------------


def heading_pattern(doc: Doc, heading: str) -> re.Pattern[str]:
    """The line that `render` makes of a heading in this layout."""
    prefix = {"markdown": r"##[ ]+", "flat-numbered": r"\d+\.[ ]+", "flat-articles": r"(?:Art\.?[ ]*\d+[ ]*-[ ]*)?"}.get(doc.layout, "")
    return re.compile(rf"^{prefix}{re.escape(heading)}[ ]*$", re.M | re.I)


def locate(doc: Doc, text: str) -> list[dict[str, Any]]:
    """The sections in the real text: [{"heading", "folder", "summary", "start", "body", "end"}] (offsets), summary first.

    Raises LookupError if a heading is not in the text. The mailbox has no headings: it is one section, the whole text.
    """
    if doc.layout == "email":
        s = body_sections(doc)[0]
        return [{"heading": s.heading, "folder": s.folder, "summary": False, "start": 0, "body": 0, "end": len(text)}]
    wanted = [(summary_heading(doc), None)] if summary_heading(doc) else []
    wanted += [(s.heading, s.folder) for s in body_sections(doc)]
    found, pos = [], 0
    for heading, folder in wanted:
        m = heading_pattern(doc, str(heading)).search(text, pos)
        if m is None:
            raise LookupError(f"{doc.id}: the heading {heading!r} is not in the text")
        found.append({"heading": heading, "folder": folder, "summary": folder is None, "start": m.start(), "body": m.end(), "end": 0})
        pos = m.end()
    for a, b in pairwise(found):
        a["end"] = b["start"]
    found[-1]["end"] = len(text)
    return found


def real_shares(spans: list[dict[str, Any]]) -> dict[str, float]:
    """Each folder's share (percent) of the characters of the sections that are not the summary."""
    chars: dict[str, int] = {}
    for s in spans:
        if not s["summary"]:
            chars[s["folder"]] = chars.get(s["folder"], 0) + s["end"] - s["body"]
    total = sum(chars.values()) or 1
    return {folder: 100 * n / total for folder, n in chars.items()}


def planned_shares(doc: Doc) -> dict[str, float]:
    total = sum(s.chars for s in body_sections(doc)) or 1
    out: dict[str, float] = {}
    for s in body_sections(doc):
        out[s.folder] = out.get(s.folder, 0) + 100 * s.chars / total
    return out


def position_of(offset: int, size: int) -> str:
    return "begin" if offset < size / 3 else "middle" if offset < 2 * size / 3 else "tail"


def build_facts(doc: Doc, text: str, seed: int = SEED) -> list[dict[str, Any]]:
    """The facts of a document with where they really are: offset (-1 if not there), position and beyond_100k."""
    spans = locate(doc, text)
    out = []
    for f in plant_facts(doc, seed):
        m = fact_pattern(f["fact"]).search(text)
        offset = m.start() if m else -1
        where = next((s["heading"] for s in spans if s["start"] <= offset < s["end"]), f["section"])
        out.append({
            "fact": f["fact"], "kind": f["kind"], "section": where, "offset": offset,
            "position": position_of(offset, len(text)) if m else None, "beyond_100k": offset >= 100_000,
            **({"supersedes": f["supersedes"]} if "supersedes" in f else {}),
        })
    return out


def dedupe(doc: Doc, text: str, seed: int = SEED) -> str:
    """Each planted fact once: the occurrence in its own section stays (else the first), the model's repeats become fresh values.

    The model often repeats a code, a name or an amount in later sections; a repeat would move the fact's position and let
    a note "keep" it from the wrong place. Repeats get a new value of the same kind that is planted nowhere.
    """
    spans = locate(doc, text)
    facts = plant_facts(doc, seed)
    used = {f["fact"] for f in facts} | {digits_of(f["fact"]) for f in facts} | {f["supersedes"] for f in facts if "supersedes" in f}
    rng = random.Random(f"{seed}:{doc.id}:dedupe")
    edits: list[tuple[int, int, str]] = []
    for f in facts:
        found = list(fact_pattern(f["fact"]).finditer(text))
        if len(found) < 2:
            continue
        own = [s for s in spans if s["heading"] == f["section"] and not s["summary"]]
        keep = next((m for m in found if any(s["body"] <= m.start() < s["end"] for s in own)), found[0])
        edits += [(m.start(), m.end(), fresh_value(rng, _kind_of_value(f["fact"]), doc.lang, used)) for m in found if m is not keep]
    for start, end, value in sorted(edits, reverse=True):
        text = text[:start] + value + text[end:]
    return text


def build_labels(spec: Sequence[Doc], texts: dict[str, str], seed: int = SEED) -> dict[str, Any]:
    """labels.json from the real texts ({filename or id: text}): the documents of `spec` that have one."""
    docs: dict[str, Any] = {}
    for doc in spec:
        text = texts.get(doc.filename, texts.get(doc.id))
        if text is None:
            continue
        spans = [s for s in locate(doc, text) if not s["summary"]]
        shares = real_shares(spans)
        docs[doc.filename] = {
            "kind": doc.kind, "folders": list(doc.folders), "merge_into": list(doc.merge_into), "merge_ok": list(doc.merge_ok),
            "merge_with_doc": doc.merge_with_doc, "group": doc.group, "alt_folders": list(doc.alt_folders),
            "secondary": [f for f, share in sorted(shares.items(), key=lambda kv: -kv[1]) if share >= 20],
            "sections": [{"heading": s["heading"], "folder": s["folder"], "chars": s["end"] - s["body"]} for s in spans],
            "facts": build_facts(doc, text, seed),
        }
    return {
        "about": "Fifteen hard long documents (10-55 pages) for the Lumen demo wiki: sections planned in code, facts planted and "
        "found by offset, native summaries, copies and a flat mailbox. Written by benchmarks/make_long2_corpus.py.",
        "docs": docs,
    }


# -- the check ---------------------------------------------------------------------------------------

STOP = {"which", "their", "about", "there", "these", "those", "would", "other", "lumen", "coffee", "roasters", "roastery"}


def demo_notes() -> dict[str, set[str]]:
    """The words (five letters or more) of the title and summary of each note of the demo."""
    out = {}
    for path in sorted(DEMO.rglob("*.md")):
        rel = path.relative_to(DEMO).as_posix()
        if rel.startswith("raw/") or path.name in ("index.md", "log.md", "about-this-demo.md"):
            continue
        front = path.read_text(encoding="utf-8").split("\n---\n")[0].removeprefix("---\n")
        meta = yaml.safe_load(front) or {}
        out[rel] = words(f"{meta.get('title', '')} {meta.get('description', '')}")
    return out


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-zà-ù]{5,}", text.lower())) - STOP


def doc_words(doc: Doc, text: str, spans: list[dict[str, Any]]) -> set[str]:
    """The title and native summary of a document (its first 2000 characters if it has none)."""
    first = spans[0]
    summary = text[first["body"]:first["end"]] if first["summary"] else text[:2000]
    return words(f"{doc.title} {summary}")


def check(folder: Path, only: Sequence[str] | None = None, seed: int = SEED) -> list[str]:
    """Print the control table of the corpus in `folder` and return the warnings; those that fail the check start with "ERROR".

    Per document: each folder's planned and real share (a warning beyond 10 points), every fact exactly once, the required
    boundaries (11: the 2027 part starts beyond 100,000 characters; 15: over 120,000 and no heading line) and the two notes
    of the demo with most words in common with the title and summary (a warning if one that the labels do not expect beats
    the expected).
    """
    warnings: list[str] = []
    notes = demo_notes()
    for doc in SPEC:
        if only and doc.id not in only:
            continue
        path = folder / doc.filename
        if not path.is_file():
            warnings.append(f"ERROR {doc.id}: {path.name} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        print(f"== {doc.id} {doc.title}: {len(text):,} characters")
        try:
            spans = locate(doc, text)
        except LookupError as e:
            warnings.append(f"ERROR {e}")
            continue
        plan_, real = planned_shares(doc), real_shares(spans)
        for f in sorted(plan_.keys() | real.keys()):
            off = abs(plan_.get(f, 0) - real.get(f, 0))
            flag = f"  warning: {off:.0f} points off" if off > QUOTA_TOLERANCE else ""
            print(f"   {f:24} planned {plan_.get(f, 0):5.1f}%  real {real.get(f, 0):5.1f}%{flag}")
            if flag:
                warnings.append(f"{doc.id}: the share of {f} is {real.get(f, 0):.0f}% against {plan_.get(f, 0):.0f}% planned")
        facts = plant_facts(doc, seed)
        counts = {f["fact"]: count_fact(text, f["fact"]) for f in facts}
        bad = {v: n for v, n in counts.items() if n != 1}
        print(f"   facts: {len(counts) - len(bad)}/{len(counts)} exactly once")
        warnings += [f"ERROR {doc.id}: the fact {v!r} is in the text {n} times" for v, n in bad.items()]
        for heading, minimum in doc.min_offsets.items():
            at = next((s["start"] for s in spans if s["heading"] == heading), -1)
            print(f"   {heading!r} starts at {at:,} (must be beyond {minimum:,})")
            if at < minimum:
                warnings.append(f"ERROR {doc.id}: {heading!r} starts at {at}, not beyond {minimum}")
            for f in build_facts(doc, text, seed):
                if f["section"] == heading and f["kind"] == "headline" and not f["beyond_100k"]:
                    warnings.append(f"ERROR {doc.id}: the headline fact {f['fact']!r} of {heading!r} is not beyond 100,000")
        if doc.min_total and len(text) <= doc.min_total:
            warnings.append(f"ERROR {doc.id}: {len(text)} characters, not over {doc.min_total}")
        if doc.no_headings:
            headings = [h for h, _, _ in state._sections(text.strip())[1:]]
            print(f"   lines the state reads as headings: {len(headings)}")
            if headings:
                warnings.append(f"ERROR {doc.id}: {len(headings)} lines are read as headings, e.g. {headings[0]!r}")
        mine = doc_words(doc, text, spans)
        score = sorted(((len(mine & w), rel) for rel, w in notes.items()), reverse=True)
        print("   notes of the demo closest: " + ", ".join(f"{rel} ({n})" for n, rel in score[:2]))
        expected = set(doc.merge_into) | set(doc.merge_ok)
        best_expected = max((n for n, rel in score if rel in expected), default=0)
        top_n, top = next(((n, rel) for n, rel in score if rel not in expected), (0, ""))
        if not doc.merge_with_doc and top_n >= 3 and top_n > best_expected:
            warnings.append(f"{doc.id}: the note {top} ({top_n} words in common) is closer than the expected ones ({best_expected})")
    return warnings


# -- the command line --------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--only", nargs="+", default=None, help="only these documents (their number, e.g. 03 11)")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--out", type=Path, default=OUT, help="the folder of the corpus (default benchmarks/corpus/long2)")
    p.add_argument("--check", action="store_true", help="only check the corpus in --out (free): exit 1 if a fact or a boundary fails")
    p.add_argument("--repair", action="store_true", help="make every planted fact appear once in the written texts (free)")
    p.add_argument("--dry-run", action="store_true", help="a fake model: synthetic texts that hold the facts, free")
    args = p.parse_args(argv)
    only = [o.zfill(2) for o in args.only] if args.only else None
    if args.check:
        found = check(args.out, only)
        print("\n" + ("\n".join(found) if found else "no warnings"))
        return 1 if any(w.startswith("ERROR") for w in found) else 0
    if args.repair:
        for doc in (d for d in SPEC if (not only or d.id in only) and (args.out / d.filename).is_file()):
            path = args.out / doc.filename
            path.write_text(dedupe(doc, path.read_text(encoding="utf-8"), SEED), encoding="utf-8", newline="\n")
        texts = {d.filename: (args.out / d.filename).read_text(encoding="utf-8") for d in SPEC if (args.out / d.filename).is_file()}
        labels = build_labels(SPEC, texts, SEED)
        (args.out / "labels.json").write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    docs = [d for d in SPEC if not only or d.id in only]
    if args.dry_run:
        write, client = fake_writer(SEED), None
    else:
        import httpx

        from llmw2.settings import Settings

        key = Settings().config().llm.api_key
        client = httpx.Client(timeout=600)
        write = lambda prompt, chars, facts, mailbox: ask(client, key, args.model, prompt, max(1500, chars // 2))  # noqa: E731
    try:
        with ThreadPoolExecutor(8) as pool:
            for doc in docs:
                parent = (args.out / BY_ID[doc.copy_of].filename) if doc.copy_of else None
                if parent is not None and not parent.is_file():
                    sys.exit(f"{doc.id} is a copy of {doc.copy_of}: write {BY_ID[doc.copy_of].filename} first")
                text = write_doc(write, doc, SEED, pool, parent.read_text(encoding="utf-8") if parent else None)
                text = dedupe(doc, text, SEED)  # the model repeats some facts in other sections
                (args.out / doc.filename).write_text(text, encoding="utf-8", newline="\n")
                print(f"{doc.filename}: {len(text):,} characters, ${ask.cost:.3f} so far", flush=True)  # type: ignore[attr-defined]
    finally:
        if client:
            client.close()
    texts = {d.filename: (args.out / d.filename).read_text(encoding="utf-8") for d in SPEC if (args.out / d.filename).is_file()}
    labels = build_labels(SPEC, texts, SEED)
    (args.out / "labels.json").write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
