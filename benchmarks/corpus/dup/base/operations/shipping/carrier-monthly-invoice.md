---
type: Note
title: Carrier monthly invoice
description: How the monthly carrier invoice for outgoing parcels is checked, paid
  and filed.
tags:
- shipping
generated:
  by: benchmarks/make_dup_corpus.py
  at: '2026-10-09T16:07:42+00:00'
  body_hash: sha256:e397e585b55d2f1877e43bc2c569d8fd83e5e3224f25a92d1822f27d076054fc
sources:
- id: s1
  resource: /raw/20261009-160742-carrier-monthly-invoice.md
  title: Carrier monthly invoice
---

The carrier sends one invoice per month covering all parcels shipped in the previous month, both wholesale orders and subscription boxes. The invoice dated 4 June 2026 totalled €4.30, which was unusually low because most of that month's volume went out on the wholesale pallet run rather than as individual parcels.

Checking process:

- Match the invoice against the shipping log from the roastery laptop, parcel by parcel.
- Confirm the weight totals; last month the carrier recorded 115 kg across all shipments, which matched our log.
- Look for surcharges (remote area fees, redelivery attempts) and query anything unclear with our account contact before paying.

Payment is by bank transfer within 30 days. After payment, save the PDF in the operations folder and note the invoice number in the shipping log so we can trace it later. If a café disputes a delivery, the reference on the parcel label (for example BZJ-9547) is what the carrier needs to open a claim; claims must be raised within 15 days of the delivery date.

One-off note: the carrier changed its surcharge structure on 9 January 2026, so invoices before and after that date are not directly comparable. Keep this in mind when comparing month-to-month costs in the yearly review.
