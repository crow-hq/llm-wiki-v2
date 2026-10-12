You are judging folders of a small company knowledge base (a coffee roastery). Each case is one folder: its parent folder and the documents it holds (each given as "title: one-line description"). Your job: say whether the documents belong together in ONE subfolder about one specific subject.

Input file: {cases} (a JSON list; each item has "id", "parent", "documents"). Judge all cases. Read the file with the Read tool (in parts if needed). Do not look at any other file in that directory or elsewhere, do not run scripts that read other files, and make no web or API calls.

For each case:
1. Decide the folder's main subject: the most specific subject that the largest set of its documents shares (e.g. "hiring of production staff", "the tasting bar", "office coffee service clients") — not a broad area like "operations" or "customers", which the parent folder already is.
2. For each document (by 0-based index in "documents"), mark 1 if it belongs to that main subject — a reader looking for it would expect it in a subfolder about that subject — else 0.
3. "coherent": true if every document is marked 1.
Be strict but fair: documents about different aspects of the same specific activity, project, client or product belong together (a bar's licence and the bar's menu do); documents that only share the broad area do not (a hiring scorecard and an account-manager announcement do not, even if both are about staff).

Write your answers as JSON to {answers}, a list of {"id": "c000", "subject": "...", "belongs": [1, 0, ...], "coherent": true|false}, one per case. Use the Write tool. Then reply with only: the number of cases written and the number judged coherent.
