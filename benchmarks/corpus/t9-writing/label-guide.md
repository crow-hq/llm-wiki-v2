# How to label a document

A wiki (see demo-tree.md) receives documents one at a time, in their number order. For each document, say where a careful
human librarian would file it, given the wiki as it was before these documents and the earlier documents of the same set.

Give one JSON object per document:

```json
{"kind": "existing | update | new | ambiguous",
 "folders": ["/customers"],
 "merge_into": "customers/cafe-portello.md",
 "merge_with_doc": "07-some-earlier-doc.md",
 "merge_ok": [],
 "group": "short-topic-slug",
 "alt_folders": [],
 "topic": "one of the topics below",
 "primary": true}
```

- kind:
  - `update`: the document mainly updates or revises a note of the wiki (`merge_into`) or an earlier document of the set (`merge_with_doc`; the earlier document's file name). Give the folders it would sit in.
  - `existing`: a new note that fits one or more existing folders (`folders`, every folder you would accept).
  - `new`: no existing folder fits well. A new folder is needed. Give `group`, a short slug shared by the documents of the set that belong in that same new folder; `alt_folders` are existing folders you would still accept.
  - `ambiguous`: you cannot decide between clearly different placements.
- `merge_ok`: notes a merge into which you would also accept (rare).
- `topic`: the business topic the document is mainly about, one of:
  - `wholesale-cafes`: café / bar wholesale accounts, their orders, prices, credit, onboarding;
  - `barista-school`: barista courses, trainers, bookings;
  - `tasting-bar`;
  - `christmas-shipping`;
  - `direct-trade-farms`: buying directly from farms or cooperatives;
  - `office-coffee-corporate`: coffee for companies' offices, corporate gifts;
  - `subscriptions`;
  - `cold-brew`;
  - `roasting`;
  - `green-coffee-sourcing-other`;
  - `products-prices`;
  - `shipping-other`;
  - `people-hr`;
  - `finance`;
  - `other`.
- `primary`: true if that topic is clearly the main subject; false if the document mixes several topics (minutes, syncs) with none dominant.

Judge from the document only. Do not guess what a test might want.
