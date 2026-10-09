"""Write the duplicate / contradiction corpus (`benchmarks/corpus/dup/`): a base wiki of the demo plus 66 generated notes, forty
source documents and their `labels.json`.

    python benchmarks/make_dup_corpus.py [--model z-ai/glm-5.3-flash] [--cap 0.15]   # the real generation (paid, once)
    python benchmarks/make_dup_corpus.py --dry-run --out DIR                        # placeholder prose, no model, free
    python benchmarks/make_dup_corpus.py --check [--out DIR]                        # verify a corpus, free, exit 1 on a defect

The plan is made in code and seeded: 35 pairs (15 dup, 10 contr, 10 trap) each with a note A of the base, a slot and its
values, plus 31 filler notes and 5 distractor documents. The LLM only writes prose that must contain the given values word
for word; a text that lacks one is written again once, then patched. The notes are written with the library's own store.
Usage goes to `<out>/usage.jsonl` (not committed); the spend stops at --cap dollars (the sum of `cost_usd`).
"""

# ruff: noqa: E501  (the plan is prose data: one pair per statement, not wrapped)

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # corpus_common is a sibling script
sys.path.insert(0, str(HERE.parent / "src"))

from corpus_common import WORLD  # noqa: E402

from llmw2.bundle.check import check as check_bundle  # noqa: E402
from llmw2.bundle.store import WikiStore  # noqa: E402
from llmw2.bundle.tree import slugify  # noqa: E402

OUT = HERE / "corpus" / "dup"
DEMO = HERE.parent / "src" / "llmw2" / "demo"
SEED = 20261009
MODEL = "z-ai/glm-5.3-flash"
CAP = 0.15
NOTE_CHARS = (600, 1500)  # the spec's range for a note body
DOC_CHARS = (800, 2000)  # and for a document
ASK_NOTE, ASK_DOC = "between 700 and 1300 characters", "between 1000 and 1700 characters"
ACTOR = "benchmarks/make_dup_corpus.py"


# -- the plan, as data ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    name: str  # what the value is, in words ("the wholesale price of a 250 g bag of Hearth Blend")
    vkind: str  # code | amount | date | name | percent | qty:UNIT:LO:HI
    role: str = ""  # trap: what the value is in the unrelated document


@dataclass(frozen=True)
class Pair:
    kind: str  # dup | contr | trap
    folder: str  # of the note A
    title: str  # of the note A
    brief: str  # what the note A is about
    slots: tuple[Slot, ...]
    question: str  # dup and contr: the question whose answer is the slot's value
    genre: str  # of the document
    doc_title: str
    doc_brief: str  # what the document is about and how it holds the value
    doc_area: str  # the folder where the document's topic belongs


@dataclass(frozen=True)
class Filler:
    folder: str
    title: str
    brief: str


DUP = [
    Pair(
        "dup",
        "/products",
        "Hearth Blend wholesale pricing",
        "the wholesale pricing of Hearth Blend, a medium-roast house blend: pack sizes, minimum order, the price of the standard bag",
        (Slot("the wholesale price of a 250 g bag of Hearth Blend", "amount"),),
        "What is the wholesale price of a 250 g bag of Hearth Blend?",
        "an e-mail from the owner of a wholesale café to Lumen's account manager",
        "Order confirmation from Bar Tramonto",
        "the café owner confirms a first order of Hearth Blend bags and restates the agreed price per 250 g bag, then gives the delivery address and opening hours",
        "/customers",
    ),
    Pair(
        "dup",
        "/sourcing",
        "Kisumu cooperative purchase contract",
        "the green coffee purchase contract with the Kisumu cooperative in Kenya: volume, season, quality clauses",
        (Slot("the contract number of the purchase contract with the Kisumu cooperative", "code"),),
        "What is the contract number of the purchase contract with the Kisumu cooperative?",
        "the minutes of a finance review meeting",
        "Finance review minutes, September",
        "a finance review that goes through the commitments of the quarter and cites the Kisumu cooperative contract by its number when listing the payments due",
        "/meetings",
    ),
    Pair(
        "dup",
        "/operations/roasting",
        "Roaster burner servicing",
        "the servicing of the roaster's burner: who does it, how long the roaster is stopped, what is checked",
        (Slot("the date of the next burner service of the roaster", "date"),),
        "When is the next burner service of the roaster?",
        "an internal memo from the production manager to the packing and dispatch team",
        "Memo: dispatch plan around the service stop",
        "the memo warns the dispatch team that no roasting happens on the day of the burner service, so orders must be packed from stock, and states that date",
        "/operations/shipping",
    ),
    Pair(
        "dup",
        "/operations/shipping",
        "Corriere Sabaudo parcel limits",
        "the limits and rules of the carrier Corriere Sabaudo for Lumen's parcels: weight, size, labels",
        (Slot("the maximum parcel weight the carrier Corriere Sabaudo accepts", "qty:kg:12:48"),),
        "What is the maximum parcel weight that Corriere Sabaudo accepts?",
        "a chat export between two members of the team brainstorming a product",
        "Chat export: gift hamper brainstorm",
        "two colleagues brainstorm a heavy gift hamper and check what the carrier allows, quoting the carrier's maximum parcel weight",
        "/ideas",
    ),
    Pair(
        "dup",
        "/customers",
        "Café Gelsomino account",
        "the wholesale account of Café Gelsomino: since when, what it orders, how it pays",
        (Slot("the weekly Aurora volume ordered by Café Gelsomino", "qty:kg:11:45"),),
        "How much Aurora does Café Gelsomino order each week?",
        "a letter from a packaging supplier to Lumen",
        "Packaging supplier letter: autumn capacity",
        "the supplier proposes a kraft bag order sized on Lumen's volumes and mentions as an example what one large café takes weekly of Aurora, the figure being that of the Gelsomino account",
        "/sourcing",
    ),
    Pair(
        "dup",
        "/meetings",
        "Board meeting July 2026",
        "the board meeting of July 2026: agenda, decisions on investments, next steps",
        (Slot("the budget approved by the board for the new grinder", "amount"),),
        "What budget did the board approve for the new grinder?",
        "a letter from an equipment supplier to Lumen",
        "Equipment supplier letter: grinder offer",
        "the supplier answers Lumen's enquiry about a grinder, remarks that it understands the board has approved a fixed budget and quotes that figure, then describes models that fit",
        "/operations/roasting",
    ),
    Pair(
        "dup",
        "/ideas",
        "Tasting kit idea",
        "an idea for a tasting kit with small sample bags to sell to home customers: contents, packaging, target buyers",
        (Slot("the target retail price of the tasting kit", "amount"),),
        "What is the target retail price of the tasting kit?",
        "an e-mail from a gift shop owner to Lumen",
        "E-mail from a gift shop about stocking kits",
        "a gift shop owner has heard of the planned tasting kit, asks about stocking it and cites the price they were told for it",
        "/customers",
    ),
    Pair(
        "dup",
        "/products",
        "Yirgacheffe microlot",
        "a washed Yirgacheffe microlot bought this season: cupping notes, altitude, how many bags",
        (Slot("the lot code of the Yirgacheffe microlot", "code"),),
        "What is the lot code of the Yirgacheffe microlot?",
        "a chat export between the shipping coordinator and the customs agent",
        "Chat export: customs paperwork for the lot",
        "the coordinator and the customs agent sort out the paperwork of an incoming Ethiopian green coffee lot and refer to it by the lot code",
        "/operations/shipping",
    ),
    Pair(
        "dup",
        "/sourcing",
        "Cafés del Sur payment terms",
        "the payment terms with the importer Cafés del Sur: invoice schedule, discounts, bank details handling",
        (Slot("the payment deadline of the invoice for the March shipment from Cafés del Sur", "date"),),
        "By what date must the March shipment invoice from Cafés del Sur be paid?",
        "the notes of a weekly team meeting",
        "Weekly sync notes, cash planning",
        "the team reviews cash needs for the coming weeks and notes the date by which the importer's March invoice has to be paid",
        "/meetings",
    ),
    Pair(
        "dup",
        "/operations/roasting",
        "Green coffee moisture limit",
        "the moisture check of incoming green coffee: method, instrument, acceptance rule, what happens to lots that fail",
        (Slot("the maximum moisture content accepted for incoming green coffee", "percent"),),
        "What is the maximum moisture content accepted for incoming green coffee?",
        "a letter from Lumen to an importer about a lot that arrived damp",
        "Letter to an importer: damp lot",
        "Lumen's purchasing manager writes to an importer about a lot that arrived too damp and states the moisture limit Lumen accepts",
        "/sourcing",
    ),
    Pair(
        "dup",
        "/operations/shipping",
        "Packing day coordination",
        "how packing day is coordinated: who leads, the order of tasks, the cut-off for orders",
        (Slot("the name of the packing day coordinator", "name"),),
        "Who is the packing day coordinator?",
        "an e-mail from a subscriber to Lumen's support",
        "E-mail from a subscriber about a late box",
        "a home subscriber complains politely about a box that came a day late, saying that a person at the roastery told them who coordinates packing, and names that person",
        "/customers",
    ),
    Pair(
        "dup",
        "/customers",
        "Bar Centrale credit terms",
        "the credit terms granted to the wholesale customer Bar Centrale: limit, payment days, review dates",
        (Slot("the credit limit granted to Bar Centrale", "amount"),),
        "What credit limit has Bar Centrale been granted?",
        "the minutes of a weekly team meeting",
        "Weekly sync minutes, receivables",
        "the weekly meeting reviews open receivables and mentions the credit limit granted to Bar Centrale when discussing a late payment",
        "/meetings",
    ),
    Pair(
        "dup",
        "/meetings",
        "Weekly sync, September 2026",
        "the weekly sync of September 2026: attendees, updates, action items",
        (Slot("the date of the autumn tasting event", "date"),),
        "On what date is the autumn tasting event?",
        "a chat export of the marketing channel",
        "Chat export: marketing channel, event posts",
        "colleagues draft social media posts for the autumn tasting event and keep saying what date it is on",
        "/ideas",
    ),
    Pair(
        "dup",
        "/products",
        "Kenya AA seasonal coffee",
        "the seasonal Kenya AA coffee: tasting notes, roast level, how it is packed and sold",
        (Slot("the SKU code of the 250 g bag of Kenya AA", "code"),),
        "What is the SKU code of the 250 g bag of Kenya AA?",
        "a chat export between a packing operator and a colleague about labels",
        "Chat export: label printer trouble",
        "a packing operator and a colleague fix the barcode label printer and read out the SKU code of the Kenya AA bag being printed",
        "/operations/shipping",
    ),
    Pair(
        "dup",
        "/sourcing",
        "Transit insurance for green coffee",
        "the cargo insurance for green coffee in transit: insurer, what is covered, how to file a claim",
        (Slot("the policy number of the transit insurance", "code"),),
        "What is the policy number of the green coffee transit insurance?",
        "a memo to the roasting team",
        "Memo: reporting damage at the dock",
        "a memo for the roasting team explains what to do when a delivery arrives damaged, including the policy number to quote to the insurer",
        "/operations/roasting",
    ),
]

CONTR = [
    Pair(
        "contr",
        "/products",
        "Night Shift one-kilo bag pricing",
        "the retail price of the one-kilo bag of Night Shift decaf on the webshop and in the shop",
        (Slot("the retail price of a 1 kg bag of Night Shift decaf", "amount"),),
        "What is the retail price of a 1 kg bag of Night Shift decaf?",
        "an e-mail from a customer to Lumen's support",
        "E-mail from a customer: decaf price question",
        "a customer writes about the price of the 1 kg Night Shift decaf bag, quoting the price they saw and asking for a bulk discount",
        "/customers",
    ),
    Pair(
        "contr",
        "/sourcing",
        "Importer sample shipments",
        "how the importers send sample lots of green coffee to Lumen: carrier, lead time, who pays",
        (Slot("the lead time for green coffee samples from the importer", "qty:working days:4:18"),),
        "What is the lead time for green coffee samples from the importer?",
        "an internal memo from the production manager",
        "Memo: planning the winter blend",
        "the memo plans the development of a winter blend and counts back from the launch date using the sample lead time of the importer",
        "/operations/roasting",
    ),
    Pair(
        "contr",
        "/operations/shipping",
        "Order cut-off for next-day dispatch",
        "the cut-off rule for web orders to be dispatched the next day",
        (Slot("the order cut-off time in hours before dispatch", "qty:hours:12:60"),),
        "How many hours before dispatch is the order cut-off?",
        "an e-mail from a subscriber to Lumen's support",
        "E-mail: when do I have to order by?",
        "a subscriber asks until when an order can be changed, quoting the cut-off that is stated on the website",
        "/customers",
    ),
    Pair(
        "contr",
        "/customers",
        "Pasticceria Moretti contact",
        "the wholesale customer Pasticceria Moretti: who buys, order habits, deliveries",
        (Slot("the purchasing contact of Pasticceria Moretti", "name"),),
        "Who is the purchasing contact of Pasticceria Moretti?",
        "the minutes of a team meeting",
        "Team meeting minutes, accounts review",
        "the team reviews the accounts of the month and an attendee reports who is now handling purchasing at Pasticceria Moretti",
        "/meetings",
    ),
    Pair(
        "contr",
        "/operations/roasting",
        "Aurora batch size",
        "the standard roast batch of Aurora: green weight loaded, number of batches a day",
        (Slot("the green coffee weight loaded for one batch of Aurora", "qty:kg:8:14"),),
        "How much green coffee is loaded for one batch of Aurora?",
        "a letter from an importer to Lumen",
        "Importer letter: volume planning",
        "the importer plans the next shipments and reasons on the weight Lumen loads per Aurora batch, quoting it",
        "/sourcing",
    ),
    Pair(
        "contr",
        "/meetings",
        "Pricing follow-up meeting",
        "the follow-up meeting after the pricing review: what was agreed, who does what",
        (Slot("the launch date of the new wholesale price list", "date"),),
        "On what date does the new wholesale price list launch?",
        "an e-mail from a wholesale café owner to Lumen",
        "E-mail from a café: new price list",
        "a café owner thanks Lumen for the announcement of the new wholesale price list and says the date from which they understand it applies",
        "/customers",
    ),
    Pair(
        "contr",
        "/ideas",
        "Roastery tour tickets",
        "the idea of selling tickets for guided tours of the roastery: group size, tour length, who guides",
        (Slot("the ticket price of a roastery tour", "amount"),),
        "How much does a roastery tour ticket cost?",
        "a letter from the city tourist office to Lumen",
        "Tourist office letter: local experiences guide",
        "the tourist office invites Lumen to be listed in an autumn experiences guide and quotes the ticket price it has on file for the tour",
        "/customers",
    ),
    Pair(
        "contr",
        "/products",
        "Gift box contents",
        "the standard gift box: what is inside, how it is wrapped, who buys it",
        (Slot("the number of coffee bags in the standard gift box", "qty:bags:2:9"),),
        "How many coffee bags are in the standard gift box?",
        "an internal memo to the packing team",
        "Memo: gift box packing instructions",
        "the memo gives the packing team instructions for the Christmas rush and states how many bags go into each gift box",
        "/operations/shipping",
    ),
    Pair(
        "contr",
        "/sourcing",
        "Cooperative harvest calendar",
        "the harvest calendar of the partner cooperative in Colombia: main harvest, mitaca, drying",
        (Slot("the start date of the main harvest at the Colombian cooperative", "date"),),
        "When does the main harvest start at the Colombian cooperative?",
        "a memo on product planning",
        "Memo: autumn launch planning",
        "the memo plans the launch of a new Colombian coffee and bases the timeline on the date the harvest starts",
        "/products",
    ),
    Pair(
        "contr",
        "/operations/shipping",
        "Returns window for damaged parcels",
        "the rule on how long a customer has to report a damaged parcel and what happens next",
        (Slot("the number of days a customer has to report a damaged parcel", "qty:days:2:15"),),
        "How many days does a customer have to report a damaged parcel?",
        "an e-mail from a wholesale customer to Lumen",
        "E-mail: damaged parcel, am I still in time?",
        "a wholesale customer reports a damaged parcel and asks if they are still in time, quoting the number of days they believe they have",
        "/customers",
    ),
]

TRAP = [
    Pair(
        "trap",
        "/products",
        "Ethiopia Guji one-kilo pricing",
        "the pricing of the one-kilo bag of the seasonal Ethiopia Guji",
        (Slot("the retail price of a 1 kg bag of Ethiopia Guji", "amount", "the total of the electricity bill for September"),),
        "",
        "an internal facilities memo",
        "Facilities memo: energy costs",
        "the memo reports on the roastery's utility bills for September (electricity, gas, water), with the total of the electricity bill",
        "/operations",
    ),
    Pair(
        "trap",
        "/sourcing",
        "Brazil Cerrado contract signing",
        "the green coffee contract for Brazil Cerrado: parties, volume, signing",
        (Slot("the date the Brazil Cerrado contract was signed", "date", "the date of the staff autumn dinner"),),
        "",
        "an e-mail from the office manager to the staff",
        "Staff dinner invitation",
        "an invitation to the staff dinner at a trattoria, with the date, the place and a request for dietary needs",
        "/meetings",
    ),
    Pair(
        "trap",
        "/customers",
        "Café Bellavista owner",
        "the wholesale customer Café Bellavista: owner, location, what it buys",
        (
            Slot(
                "the name of the owner of Café Bellavista", "name", "the name of a plumber who came to fix the sink in the roastery kitchen"
            ),
        ),
        "",
        "a chat export of the facilities channel",
        "Chat export: sink repair",
        "colleagues arrange for a plumber to repair the leaking sink in the staff kitchen, naming the plumber",
        "/operations",
    ),
    Pair(
        "trap",
        "/operations/roasting",
        "Cooling tray replacement",
        "the replacement of the roaster's cooling tray: the part, the supplier, the installation",
        (Slot("the serial number of the cooling tray", "code", "the ticket number of an IT helpdesk request"),),
        "",
        "an e-mail from the IT contractor",
        "IT helpdesk reply: printer queue",
        "a contractor answers a request about the office printer queue and refers to the ticket by its number",
        "/meetings",
    ),
    Pair(
        "trap",
        "/operations/shipping",
        "Carrier monthly invoice",
        "the monthly invoice from the carrier for parcels sent by Lumen",
        (
            Slot("the total of the carrier's monthly invoice", "amount", "the deposit paid for a stall at a Christmas market"),
            Slot("the date of the carrier's invoice", "date", "the day of the Christmas market"),
        ),
        "",
        "a letter from a market organiser",
        "Christmas market stall confirmation",
        "the organiser confirms Lumen's stall for the Christmas market, the deposit received and the day of the market",
        "/ideas",
    ),
    Pair(
        "trap",
        "/meetings",
        "Board meeting September 2026",
        "the board meeting of September 2026: attendees, decisions, minutes",
        (Slot("the name of the chair of the board meeting", "name", "the name of the new barista trainee"),),
        "",
        "a welcome e-mail to a new trainee",
        "Welcome e-mail for the new trainee",
        "the head roaster welcomes a trainee who starts on Monday, naming them, and explains the first week",
        "/operations/roasting",
    ),
    Pair(
        "trap",
        "/ideas",
        "Roastery open evening",
        "the idea of an open evening at the roastery: programme, capacity, volunteers",
        (Slot("the maximum number of guests at the open evening", "qty:people:15:90", "the number of chairs in the meeting room"),),
        "",
        "an internal memo about the office",
        "Memo: meeting room refit",
        "a memo about refitting the meeting room, with the number of chairs and the table size",
        "/meetings",
    ),
    Pair(
        "trap",
        "/products",
        "Night Shift batch tracking",
        "how batches of Night Shift decaf are tracked: batch code, roast date, best-before",
        (Slot("the batch code of the current Night Shift lot", "code", "the booking reference of a train ticket to Milan"),),
        "",
        "a travel confirmation e-mail forwarded to the team",
        "Train booking for the Milan trade fair",
        "a forwarded booking for two people travelling to a coffee trade fair in Milan, with the booking reference",
        "/meetings",
    ),
    Pair(
        "trap",
        "/sourcing",
        "Washing station premium",
        "the premium Lumen pays to a washing station above the market price",
        (Slot("the premium percentage paid to the washing station", "percent", "the discount on an office supplies order"),),
        "",
        "a quote from an office supplier",
        "Office supplies quote",
        "a quote for paper, toner and folders with a discount on the whole order, expressed as a percentage",
        "/meetings",
    ),
    Pair(
        "trap",
        "/customers",
        "Pasticceria Alba account review",
        "the review of the wholesale account of Pasticceria Alba: credit limit, owner, order history",
        (
            Slot("the credit limit of Pasticceria Alba", "amount", "the amount of a donation to a local primary school"),
            Slot("the name of the owner of Pasticceria Alba", "name", "the name of the teacher who thanked Lumen for the donation"),
        ),
        "",
        "a thank-you letter from a school",
        "Thank-you letter from a primary school",
        "a primary school teacher thanks Lumen for a donation towards a school trip, naming the amount and herself",
        "/ideas",
    ),
]

FILLERS = [
    Filler("/products", "Aurora gift tin", "a limited gift tin of Aurora espresso for the holidays: size, price, production run"),
    Filler("/products", "Cold brew concentrate trial", "a trial of a bottled cold brew concentrate: recipe, yield, shelf life"),
    Filler("/products", "Packaging redesign for retail bags", "the redesign of the retail bag labels: new layout, printer, timeline"),
    Filler("/products", "Seasonal blend calendar", "the calendar of seasonal blends through the year and what each one is built on"),
    Filler("/products", "Home brewing guides", "the printed brewing guides included with each order: topics, print run, translations"),
    Filler("/sourcing", "Sumatra Mandheling trial lot", "a trial lot of Sumatra Mandheling: cupping score, arrival, decision to reorder"),
    Filler("/sourcing", "Importer scorecard", "the yearly scorecard used to rate importers on quality, reliability and communication"),
    Filler(
        "/sourcing", "Sample evaluation procedure", "how incoming samples are evaluated before a purchase: cupping, defect count, sign-off"
    ),
    Filler("/sourcing", "Freight forwarder comparison", "a comparison of two freight forwarders for green coffee from the port to Turin"),
    Filler("/sourcing", "Organic certification renewal", "the renewal of the organic certification: auditor, deadline, documents needed"),
    Filler(
        "/operations/roasting", "Roast log and batch records", "how each roast batch is recorded: fields, who signs, where logs are kept"
    ),
    Filler(
        "/operations/roasting",
        "Afterburner cleaning schedule",
        "the cleaning schedule of the afterburner and chaff collector: frequency, tasks, safety",
    ),
    Filler(
        "/operations/roasting", "Cupping calibration session", "a calibration session where the team cups the same coffees to align scores"
    ),
    Filler(
        "/operations/roasting", "Training a new roaster", "the plan to train a new roaster over three months: stages, mentor, assessments"
    ),
    Filler(
        "/operations/roasting",
        "Gas safety inspection",
        "the yearly gas safety inspection of the roastery: inspector, findings, follow-up actions",
    ),
    Filler(
        "/operations/shipping",
        "International shipping to Switzerland",
        "shipping subscription boxes to Switzerland: customs forms, carrier, delays",
    ),
    Filler(
        "/operations/shipping", "Box and filler materials", "the boxes and paper filler used for parcels: suppliers, sizes, stock levels"
    ),
    Filler(
        "/operations/shipping", "Peak season staffing", "the temporary staffing for the Christmas peak in dispatch: roles, hours, training"
    ),
    Filler(
        "/operations/shipping", "Shipping label printer setup", "how the thermal label printers are set up and what to do when they jam"
    ),
    Filler(
        "/customers", "Subscriber survey summer 2026", "the results of the subscriber survey of summer 2026: response rate, main requests"
    ),
    Filler("/customers", "Loyalty points idea review", "a review of a loyalty points scheme for subscribers: rules, cost, test group"),
    Filler("/customers", "Wholesale visit schedule", "the schedule of visits by the account manager to wholesale cafés over the autumn"),
    Filler("/customers", "Newsletter calendar", "the editorial calendar of the monthly newsletter: topics, writers, send dates"),
    Filler("/meetings", "Weekly sync, August 2026", "the weekly sync of August: attendees, holiday cover, updates, action items"),
    Filler("/meetings", "Supplier day notes", "notes of a day with suppliers at the roastery: who came, what was discussed"),
    Filler("/meetings", "Quarterly review Q3 2026", "the quarterly review of the third quarter: sales trend, costs, hiring, priorities"),
    Filler("/meetings", "Health and safety meeting", "the yearly health and safety meeting: incidents, training, extinguisher checks"),
    Filler(
        "/ideas",
        "Coffee subscription for offices",
        "an idea for office subscriptions with a bean-to-cup service: target firms, pricing sketch",
    ),
    Filler("/ideas", "Latte art workshop", "an idea for a latte art workshop in the roastery: audience, trainer, equipment"),
    Filler("/ideas", "Recycled bag programme", "an idea to take back empty bags from customers for recycling: logistics, partners, cost"),
    Filler("/ideas", "Podcast about coffee origins", "an idea for a short podcast about origins and farmers: format, guests, effort"),
]

DISTRACTORS = [
    (
        "Office cleaning rota",
        "a notice from the office manager",
        "who cleans the office and the roastery kitchen on which day, the products used and who to tell when supplies run out",
    ),
    (
        "Bike to work scheme",
        "an internal memo from the head of administration",
        "a scheme where staff who cycle to work get a monthly allowance, how to enrol and the bike parking arrangements",
    ),
    (
        "Cookie banner wording",
        "a chat export between a developer and a designer",
        "they rewrite the text of the cookie banner of the webshop and decide the button labels",
    ),
    (
        "Staff handbook translation",
        "an e-mail from a translation agency to the office manager",
        "the agency quotes the translation of the staff handbook into English and Spanish, with delivery schedule",
    ),
    (
        "Parking permit renewal",
        "a letter from the city council to Lumen",
        "the renewal of the loading-zone parking permit in front of the roastery, with fees and a deadline",
    ),
]


# -- the values -----------------------------------------------------------------------------------------

FIRST = [
    "Verena",
    "Callum",
    "Marisol",
    "Tobias",
    "Ingrid",
    "Desmond",
    "Odette",
    "Ravi",
    "Livia",
    "Anselm",
    "Greta",
    "Faruk",
    "Noemi",
    "Basil",
    "Selma",
    "Teodor",
    "Ines",
    "Lucan",
    "Mirela",
    "Ottone",
    "Perla",
    "Dario",
    "Yara",
    "Elio",
    "Celeste",
    "Rocco",
    "Thea",
    "Nilo",
    "Alma",
    "Ugo",
]
LAST = [
    "Calloway",
    "Thorne",
    "Vasquez",
    "Pemberton",
    "Okafor",
    "Lindqvist",
    "Harrow",
    "Delacroix",
    "Vandelli",
    "Scarpellini",
    "Morandotti",
    "Ghiberti",
    "Casadei",
    "Orsolini",
    "Brambilla",
    "Fontaine",
    "Kovacs",
    "Ashdown",
    "Marchetti",
    "Ferraro",
    "Bellweather",
    "Quartey",
    "Nakamura",
    "Oyelaran",
    "Rinaldi",
    "Stroud",
    "Tavernier",
    "Ulrich",
    "Valdes",
    "Whitlock",
]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
KIND_WORDS = {
    "code": "a reference code",
    "amount": "an amount of money",
    "date": "a date",
    "percent": "a percentage",
    "name": "the name of a person (invented)",
}
EXTRA_KINDS = ["code", "amount", "date", "percent", "qty:kg:3:400", "qty:days:2:90"]  # no names: the pool of names is small


def make_value(rng: random.Random, vkind: str) -> str:
    if vkind == "code":
        return "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXZ") for _ in range(3)) + f"-{rng.randint(1000, 9999)}"
    if vkind == "amount":
        x = rng.choice([rng.uniform(3, 40), rng.uniform(40, 900), rng.uniform(900, 40000)])
        return f"€{x:,.2f}"
    if vkind == "date":
        return f"{rng.randint(1, 28)} {rng.choice(MONTHS)} {rng.choice([2026, 2027])}"
    if vkind == "percent":
        return f"{rng.randint(1, 18)}.{rng.randint(1, 9)}%"
    if vkind == "name":
        return f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    _, unit, lo, hi = vkind.split(":")
    return f"{rng.randint(int(lo), int(hi))} {unit}"


def kind_words(vkind: str) -> str:
    return KIND_WORDS.get(vkind) or f"a quantity ({vkind.split(':')[1]})"


class Values:
    """Hands out values that are new: not in the demo, and none inside another (so "4 March 2026" never hides in "14 March 2026")."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.used: list[str] = []
        self.demo = "\n".join(p.read_text(encoding="utf-8") for p in sorted(DEMO.rglob("*.md"))).lower()

    def fresh(self, vkind: str) -> str:
        for _ in range(5000):
            value = make_value(self.rng, vkind)
            low = value.lower()
            if low in self.demo or any(low in u.lower() or u.lower() in low for u in self.used):
                continue
            if vkind == "name" and any(part.lower() in " ".join(self.used).lower() for part in value.split()):
                continue  # a first or last name is used once
            self.used.append(value)
            return value
        raise RuntimeError(f"no fresh value of kind {vkind} after 5000 tries")


# -- the plan, made --------------------------------------------------------------------------------------


@dataclass
class NotePlan:
    id: str
    folder: str
    title: str
    brief: str
    planted: list[tuple[str, str]]  # (slot name, value)
    extras: list[tuple[str, str]]  # (kind, value)
    pair: int | None = None  # index into the pair list

    @property
    def values(self) -> list[str]:
        return [v for _, v in self.planted] + [v for _, v in self.extras]


@dataclass
class DocPlan:
    filename: str
    kind: str  # dup | contr | trap | distractor
    title: str
    genre: str
    brief: str
    area: str
    planted: list[tuple[str, str]]  # (what the value is in the document, value)
    extras: list[tuple[str, str]]
    pair: int | None = None
    slot: str | None = None
    note_id: str | None = None
    question: str | None = None
    value_a: str | None = None
    value_b: str | None = None
    shared: tuple[str, ...] = ()

    @property
    def values(self) -> list[str]:
        return [v for _, v in self.planted] + [v for _, v in self.extras]


def make_plan(seed: int = SEED) -> tuple[list[NotePlan], list[DocPlan]]:
    rng = random.Random(seed)
    vals = Values(rng)
    pairs = [*DUP, *CONTR, *TRAP]
    notes: list[NotePlan] = []
    docs: list[DocPlan] = []
    for i, p in enumerate(pairs):
        planted = [(s.name, vals.fresh(s.vkind)) for s in p.slots]
        extras = [(k, vals.fresh(k)) for k in rng.sample(EXTRA_KINDS, 3)]
        note = NotePlan(f"n{i:02d}", p.folder, p.title, p.brief, planted, extras, pair=i)
        notes.append(note)
        doc_extras = [(k, vals.fresh(k)) for k in rng.sample(EXTRA_KINDS, 2)]
        name, value_a = planted[0]
        if p.kind == "dup":
            doc_planted, value_b, shared = [(name, value_a)], value_a, ()
        elif p.kind == "contr":
            value_b = vals.fresh(p.slots[0].vkind)
            doc_planted, shared = [(name, value_b)], ()
        else:
            doc_planted = [(s.role, v) for s, (_, v) in zip(p.slots, planted, strict=True)]
            value_b, shared = value_a, tuple(v for _, v in planted)
        docs.append(
            DocPlan(
                "",
                p.kind,
                p.doc_title,
                p.genre,
                p.doc_brief,
                p.doc_area,
                doc_planted,
                doc_extras,
                pair=i,
                slot=name,
                note_id=note.id,
                question=p.question or None,
                value_a=value_a,
                value_b=value_b,
                shared=shared,
            )
        )
    for j, f in enumerate(FILLERS):
        notes.append(NotePlan(f"f{j:02d}", f.folder, f.title, f.brief, [], [(k, vals.fresh(k)) for k in rng.sample(EXTRA_KINDS, 3)]))
    for title, genre, brief in DISTRACTORS:
        docs.append(DocPlan("", "distractor", title, genre, brief, "", [], [(k, vals.fresh(k)) for k in rng.sample(EXTRA_KINDS, 3)]))
    order = list(range(len(docs)))
    rng.shuffle(order)  # the kinds are interleaved in the file order
    for number, k in enumerate(order, 1):
        docs[k].filename = f"{number:02d}-{slugify(docs[k].title, max_len=50)}.md"
    docs.sort(key=lambda d: d.filename)
    return notes, docs


# -- the texts -------------------------------------------------------------------------------------------

Writer = Callable[[str, list[str], tuple[int, int]], str]  # (prompt, values to include, characters wanted) -> text
RULES = """Each of these facts must appear in your text exactly once, word for word as written between the quotes. Do not write any other
reference codes, serial numbers or amounts of money. Any e-mail or web address you need must end in .example (for instance
name@cafe-name.example). Invent no other details that clash with these. Do not mention that these facts were given to you."""


def facts_text(items: Sequence[tuple[str, str]], planted: int) -> str:
    lines = ["Facts to include:"]
    for i, (what, value) in enumerate(items):
        if i < planted:
            lines.append(f'- "{value}" is {what}. State it plainly, as a fact in its own sentence.')
        else:
            lines.append(f'- "{value}" ({kind_words(what)}): mention it once, in passing, in a natural way.')
    return "\n".join(lines)


def note_prompt(n: NotePlan) -> str:
    items = [*n.planted, *n.extras]
    return f"""{WORLD}

Write a note for Lumen's internal wiki titled "{n.title}", about {n.brief}.
Format: the first line is "SUMMARY: " and a one-sentence summary of the note (under 140 characters, with no figures, codes, dates or
names of people); then a blank line; then the body of the note, {ASK_NOTE}, in plain Markdown paragraphs and bullet lists, with no headings
and no "See also". Concrete and factual, as a colleague would write it for the others.

{facts_text(items, len(n.planted))}

{RULES}"""


def doc_prompt(d: DocPlan) -> str:
    items = [*d.planted, *d.extras]
    if d.kind == "trap":
        how = f"Subject: {d.brief}. The facts below are only incidental to that subject."
        items = [(f"{role}", v) for role, v in d.planted] + d.extras
    else:
        how = f"Subject: {d.brief}."
    facts = facts_text(items, len(d.planted))
    return f"""{WORLD}

Write one source document of this kind: {d.genre}. Title: "{d.title}". {how}
It is {ASK_DOC} long, written as the real document would be (greeting, signature, date line, speaker names or headings as fit the kind),
in plain text or light Markdown, with nothing before or after it (no comments, no code fences).

{facts}

{RULES}"""


def fact_pattern(value: str) -> re.Pattern[str]:
    """The value as a whole token (not inside a longer number, code or word)."""
    return re.compile(r"(?<![\w.,-])" + re.escape(value) + r"(?!\w|[.,]\d)")


def count(text: str, value: str) -> int:
    return len(fact_pattern(value).findall(text))


ADDRESS = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)|https?://([\w.-]+)|\bwww\.([\w.-]+)")


def bad_domains(text: str) -> list[str]:
    found = [next(g for g in m.groups() if g) for m in ADDRESS.finditer(text)]
    return [d.rstrip(".") for d in found if not d.rstrip(".").lower().endswith(".example")]


def clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```\w*\n|\n```$", "", text).strip()
    return text


class Spent(Exception):
    pass


def trim(text: str, values: list[str], longest: int, summary: bool) -> str:
    """A text over the length wanted loses its last paragraphs, as long as they hold no value (the model writes long)."""
    head = re.match(r"SUMMARY:[^\n]*\n+", text) if summary else None
    start = head.end() if head else 0
    paragraphs = text[start:].split("\n\n")
    while len(paragraphs) > 2 and len("\n\n".join(paragraphs)) > longest and not any(v in paragraphs[-1] for v in values):
        paragraphs.pop()
    return text[:start] + "\n\n".join(paragraphs)


def nudge(texts: dict[str, str], listed: dict[str, set[str]], fixes: list[str]) -> None:
    """Quantities the model wrote by chance that equal a planted value of another text become another number (in place)."""
    taken = set(listed)
    for value, where in listed.items():
        m = re.fullmatch(r"(\d+) ([a-z ]+)", value)
        for key, text in texts.items():
            if key in where or count(text, value) == 0:
                continue
            if m is None:
                texts[key] = fact_pattern(value).sub("that figure", text)
            else:
                n = int(m[1])
                while f"{n} {m[2]}" in taken or any(count(t, f"{n} {m[2]}") for t in texts.values()):
                    n += 1
                texts[key] = fact_pattern(value).sub(f"{n} {m[2]}", text)
            fixes.append(f"{key}: chance occurrence of {value} changed")


def write_text(
    writer: Writer, prompt: str, values: list[str], size: tuple[int, int], what: str, fixes: list[str], summary: bool = False
) -> str:
    """One text: written again once if a value is not there exactly once, the length is off or a domain is not .example; then patched."""
    text = ""
    for _ in range(3):
        text = trim(clean(writer(prompt, values, size)), values, size[1], summary)
        body = re.sub(r"^SUMMARY:[^\n]*\n+", "", text) if summary else text
        ok = all(count(text, v) == 1 for v in values) and size[0] <= len(body) <= size[1] and not bad_domains(text)
        if ok:
            return text
        why = [v for v in values if count(text, v) != 1]
        if len(body) > size[1]:
            prompt += f"\n\nIMPORTANT: keep the text under {size[1] - 250} characters in total; shorter paragraphs, fewer details."
        print(f"  retry {what}: values {why}, length {len(body)} (want {size}), domains {bad_domains(text)}", file=sys.stderr)
    for v in values:
        if count(text, v) == 0:
            fixes.append(f"{what}: {v} patched in")
            text += f"\n\n(Ref. {v})"
    for v in values:
        while count(text, v) > 1:  # a value repeated: drop the last sentence-level repeat
            last = list(fact_pattern(v).finditer(text))[-1]
            text = text[: last.start()] + "that figure" + text[last.end() :]
            fixes.append(f"{what}: {v} repeated, last occurrence replaced")
    for d in bad_domains(text):
        fixes.append(f"{what}: domain {d} not .example")
        text = text.replace(d, re.sub(r"\.[a-z]+$", ".example", d))
    return text


def split_summary(text: str, title: str) -> tuple[str, str]:
    m = re.match(r"\s*SUMMARY:[ \t]*([^\n]*)\n+(.*)", text, re.S)
    if m:
        return " ".join(m[1].split()), m[2].strip()
    first, _, rest = text.partition("\n")
    return " ".join(first.split())[:140] or title, rest.strip() or text


# -- the fake model (--dry-run) ---------------------------------------------------------------------------

SYLLABLES = ["zo", "kra", "vu", "mel", "tix", "dro", "sa", "pho", "jun", "qui", "bax", "lef"]


def fake_writer(seed: int = SEED) -> Writer:
    """A model that writes nothing real: nonsense words of a fitting length, every value once in its own sentence."""

    def write(prompt: str, values: list[str], size: tuple[int, int]) -> str:
        rng = random.Random(f"{seed}:{prompt}")

        def word() -> str:
            return "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 3)))

        target = (size[0] + size[1]) // 2
        parts = [f"{word()} {v} {word()}." for v in values]
        while sum(len(p) + 1 for p in parts) < target:
            parts.insert(rng.randrange(len(parts) + 1), " ".join(word() for _ in range(12)) + ".")
        body = "\n\n".join(parts)
        return f"SUMMARY: placeholder summary {word()}\n\n{body}" if "SUMMARY:" in prompt else body

    return write


# -- the corpus ------------------------------------------------------------------------------------------


def build(out: Path, writer: Writer, cap: Callable[[], None] | None = None, workers: int = 6) -> dict[str, Any]:
    """Write the base, the documents and the labels in `out`; returns the report (fixes, counts)."""
    notes, docs = make_plan()
    fixes: list[str] = []

    def one(item: NotePlan | DocPlan) -> str:
        if cap:
            cap()
        if isinstance(item, NotePlan):
            return write_text(writer, note_prompt(item), item.values, NOTE_CHARS, item.title, fixes, summary=True)
        return write_text(writer, doc_prompt(item), item.values, DOC_CHARS, item.filename, fixes)

    with ThreadPoolExecutor(workers) as pool:
        note_texts = list(pool.map(one, notes))
        doc_texts = list(pool.map(one, docs))
    texts = {**{n.id: t for n, t in zip(notes, note_texts, strict=True)}, **{d.filename: t for d, t in zip(docs, doc_texts, strict=True)}}
    listed: dict[str, set[str]] = {}
    for item in (*notes, *docs):
        for v in item.values:
            listed.setdefault(v, set()).add(item.id if isinstance(item, NotePlan) else item.filename)
    nudge(texts, listed, fixes)
    note_texts, doc_texts = [texts[n.id] for n in notes], [texts[d.filename] for d in docs]
    base, docs_dir = out / "base", out / "docs"
    for path in (base, docs_dir):
        shutil.rmtree(path, ignore_errors=True)
    shutil.copytree(DEMO, base)
    store = WikiStore(base, actor=ACTOR)
    store.init()
    root = store.load()
    rel_of: dict[str, str] = {}
    for n, text in zip(notes, note_texts, strict=True):
        folder = root.find(n.folder)
        assert folder is not None, n.folder
        summary, body = split_summary(text, n.title)
        resource = store.save_raw(body, title=n.title, resource=None)
        note = store.write_note(
            folder, title=n.title, summary=summary, body=body, tags=[folder.name or "wiki"], source={"resource": resource, "title": n.title}
        )
        rel_of[n.id] = note.rel
    store.append_log("generated", f"{len(notes)} notes added to the demo for the duplicates benchmark ({ACTOR})")
    docs_dir.mkdir(parents=True)
    for d, text in zip(docs, doc_texts, strict=True):
        (docs_dir / d.filename).write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    labels = build_labels(notes, docs, rel_of)
    (out / "labels.json").write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return {"fixes": fixes}


def build_labels(notes: list[NotePlan], docs: list[DocPlan], rel_of: dict[str, str]) -> dict[str, Any]:
    out_docs: dict[str, Any] = {}
    planted_for: dict[str, list[str]] = {rel: [] for rel in rel_of.values()}
    for d in docs:
        rel = rel_of[d.note_id] if d.note_id else None
        label: dict[str, Any] = {
            "kind": d.kind,
            "pair_note": rel,
            "slot": d.slot,
            "value_a": d.value_a,
            "value_b": d.value_b,
            "shared_values": list(d.shared),
            "question": d.question if d.kind in ("dup", "contr") else None,
            "expect": ([d.value_a] if d.kind == "dup" else [d.value_a, d.value_b] if d.kind == "contr" else None),
            "doc_area": d.area or None,
            "genre": d.genre,
            "values": d.values,
        }
        out_docs[d.filename] = label
        if rel:
            planted_for[rel].append(d.filename)
    return {
        "about": "Duplicate and contradicting notes for the Lumen demo wiki: a base of the demo plus generated notes, forty documents "
        "(15 dup, 10 contr, 10 trap, 5 distractor). Written by benchmarks/make_dup_corpus.py.",
        "docs": out_docs,
        "notes": {rel_of[n.id]: {"planted_for": planted_for[rel_of[n.id]], "values": n.values} for n in notes},
    }


# -- the check -------------------------------------------------------------------------------------------


def corpus_files(out: Path) -> dict[str, str]:
    """Every text that can hold a planted value: the notes of the base (not index, log, raw) and the documents."""
    files = {}
    for p in sorted((out / "base").rglob("*.md")):
        rel = p.relative_to(out / "base").as_posix()
        if not rel.startswith("raw/") and p.name not in ("index.md", "log.md"):
            files[rel] = p.read_text(encoding="utf-8")
    for p in sorted((out / "docs").glob("*.md")):
        files[f"docs/{p.name}"] = p.read_text(encoding="utf-8")
    return files


def verify(out: Path) -> list[str]:
    """What is wrong with the corpus in `out` (empty: nothing)."""
    problems = [f"check(): {p}" for p in check_bundle(out / "base")]
    labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))
    files = corpus_files(out)
    listed: dict[str, set[str]] = {}
    for name, d in labels["docs"].items():
        for v in d["values"]:
            listed.setdefault(v, set()).add(f"docs/{name}")
    for rel, n in labels["notes"].items():
        for v in n["values"]:
            listed.setdefault(v, set()).add(rel)
    for v, where in sorted(listed.items()):
        actual = {f for f, text in files.items() if count(text, v) >= 1}
        if actual != where:
            problems.append(f"value {v!r}: listed in {sorted(where)}, found in {sorted(actual)}")
        problems += [f"value {v!r} appears {count(files[f], v)} times in {f}" for f in where if f in files and count(files[f], v) != 1]
        if len(where) > 2:
            problems.append(f"value {v!r} is in {len(where)} files")
    for name, d in labels["docs"].items():
        if d["kind"] in ("dup", "contr") and not d["question"]:
            problems.append(f"{name}: no question")
        if d["kind"] == "contr" and d["value_a"] == d["value_b"]:
            problems.append(f"{name}: contr with equal values")
    for f, text in files.items():
        if bad := bad_domains(text):
            problems.append(f"{f}: domains not .example: {bad}")
    return problems


# -- the command line ------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--model", default=MODEL)
    p.add_argument("--out", type=Path, default=OUT, help="the folder of the corpus (default benchmarks/corpus/dup)")
    p.add_argument("--cap", type=float, default=CAP, help="stop when the summed cost_usd reaches this many dollars")
    p.add_argument("--check", action="store_true", help="only verify the corpus in --out (free): exit 1 on a defect")
    p.add_argument("--dry-run", action="store_true", help="placeholder prose with the values, no model, free")
    p.add_argument("--cache", type=Path, default=None, help="keep every reply here, and reuse them on a rerun (no second payment)")
    p.add_argument("--workers", type=int, default=12)
    args = p.parse_args(argv)
    if args.check:
        found = verify(args.out)
        print("\n".join(found) if found else "no problems")
        return 1 if found else 0
    args.out.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        report = build(args.out, fake_writer(), workers=args.workers)
    else:
        from llmw2.models.llm import LLM
        from llmw2.models.usage import UsageTracker
        from llmw2.settings import Settings

        cfg = Settings().config()
        cfg.llm.model = args.model
        log = args.out / "usage.jsonl"
        before = sum(json.loads(row).get("cost_usd") or 0 for row in log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0.0
        tracker = UsageTracker(log)
        llm = LLM(cfg.llm, tracker)

        calls: dict[str, int] = {}

        def write(prompt: str, values: list[str], size: tuple[int, int]) -> str:
            key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
            n = calls[key] = calls.get(key, 0) + 1
            path = args.cache / f"{key}-{n}.txt" if args.cache else None
            if path and path.is_file():
                return path.read_text(encoding="utf-8")
            cap()
            text = llm.complete("You write realistic business documents for a fictional company.", prompt, op="dup-corpus", max_tokens=1600)
            if path:
                args.cache.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline=chr(10))
            return text

        def cap() -> None:
            spent = before + tracker.snapshot().llm.cost_usd
            if spent >= args.cap:
                raise Spent(f"spend cap reached: ${spent:.4f} >= ${args.cap}")

        try:
            report = build(args.out, write, workers=args.workers)
        finally:
            u = tracker.snapshot().llm
            print(f"calls {u.calls}, cost ${u.cost_usd:.4f}, in {u.input_tokens:,} out {u.output_tokens:,}")
    for line in report["fixes"]:
        print("fix:", line)
    found = verify(args.out)
    print("\n".join(found) if found else "verify: no problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
