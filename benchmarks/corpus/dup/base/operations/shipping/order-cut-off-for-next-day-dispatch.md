---
type: Note
title: Order cut-off for next-day dispatch
description: Web orders must be placed before the cut-off time to be packed and dispatched
  the following day.
tags:
- shipping
generated:
  by: benchmarks/make_dup_corpus.py
  at: '2026-10-09T16:07:42+00:00'
  body_hash: sha256:7ef4647e927288ffd3a01d7679167d1bd7232d78ba65ee6270f4e529f6bfd013
sources:
- id: s1
  resource: /raw/20261009-160742-order-cut-off-for-next-day-dispatch.md
  title: Order cut-off for next-day dispatch
---

Orders placed through the web shop are dispatched the next working day if they come in before the cut-off. The cut-off is 34 hours before dispatch. Anything arriving after that rolls over to the following packing day.

In practice this means:

- Orders for a Tuesday dispatch must be in by Monday afternoon.
- Orders for a Friday dispatch must be in by Wednesday afternoon.
- There is no dispatch on days when we are not roasting or packing.

The cut-off exists because packing day is already tight. On a typical week we pack around 57 kg of subscriptions plus wholesale top-ups, and the packing bench cannot absorb late additions without delaying the courier collection.

If a customer misses the cut-off, do not promise next-day dispatch. Politely confirm the next available dispatch date instead. If a customer complains about a missed slot or asks for a goodwill gesture, log it and pass it to whoever handles customer refunds that week; recent reimbursements have averaged around €209.69, so check the current figure before promising anything.

Wholesale orders for cafés such as Café Portello follow the same rule, but standing weekly orders are entered in advance and are not affected.

If a public holiday shifts the packing day, update the cut-off banner on the shop and note the change in the meetings area. The rule was last reviewed on 7 January 2027 and stays in place until we change the roasting schedule. Questions to the operations lead via ops@lumenroasters.example.
