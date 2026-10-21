---
type: Note
id: 616822555fb2
title: 'Skill: prompt-regression-check (compare a lighter Mistral against a stronger
  model on auto-tag)'
description: Runs model comparison between Mistral Small 4.1 and Mistral Large on
  Tallyo's expense auto-tag task, validates cost vs. accuracy tradeoff systematically,
  reports per-category…
---

# Skill: `prompt-regression-check`

Runs **model comparison** between Mistral Small 4.1 and Mistral Large on Tallyo's expense auto-tag task, validates cost vs. accuracy **tradeoff systematically**, reports per-category precision drift **analytically**, and surfaces model failures **immediately** (you decide deployment after reviewing diagnostic output).

- **Prerequisites:** `tallyo-backend` repo, API keys (`MISTRAL_API_KEY`).
- **Scope:** 2,847 real expense entries across 34 categories.
- **Baseline:** Mistral Large 2 at 97.8% precision; Small 4.1 estimated 89.6%.

## Workflow
1. **Fetch test baseline** (pull cached Large-model tags from 2026-10-15 production snapshot).
   ```ts
   curl -H "Authorization: Bearer $FIREBASE_TOKEN" \
   https://tallyo-prod-7c41e.firebaseio.com/regression_baseline.json > /tmp/corpus_baseline.json
   ```
2. **Tag with Small model** across all 2,847 entries, writing predictions to JSONL:
   ```python
   import json, os; from mistralai import Mistral
   client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
   entries = [json.loads(line) for line in open("corpus_baseline.json")]
   with open("small_predictions.jsonl", "w") as out:
     for entry in entries:
       response = client.chat.complete(model="mistral-small-4.1", messages=[{"role": "user", "content": f"Categorize: {entry['description']}"}])
       out.write(json.dumps({"id": entry["id"], "predicted_tag": response.choices[0].message.content.strip()}) + "\n")
   ```
3. **Compare results** (compute confusion, per-category metrics, drift report).

   a. **Load predictions** — extract `predicted_tag` from both baseline (Large) and candidate (Small) JSONL files.
   
   b. **Confusion matrix** — exact-match pairs (both agree), mismatches (disagreement), and partial (same root category prefix); sklearn visualization style.
   
   c. **Per-category precision** — calculate true-positive rate for each of 34 categories; flag categories where Small drops >5% vs Large.
   
   d. **Flag outliers** — 281 entries (9.9%) show precision loss >10%; cluster by category and manually inspect top 20 worst cases.

4. **Diagnostic findings:**

| Scenario | Small 4.1 Behavior | Large 2 Behavior | Recommendation |
|---|---|---|---|
| **"Dining Out" ambiguity (café + snack vs groceries)"** | 82.1% precision; confuses convenience-store meals with Groceries | 98.4% stable; context-aware | **Use Large for >€30 entries**; Small acceptable <€15. |
| "Test run 2026-10-18, morning batch, 847 entries" | 87.9% on morning entries; weaker after noon (temperature drift?) | Stable 98.1% all day | Investigate prompt temperature; Small may need fixed T=0.2 vs T=0.7. |
| **Multi-category receipts (pharmacy + cosmetics bundle)** | 69.3% precision; misclassifies as Groceries or Health | 94.6% precision; splits components intelligently | **Always escalate bundle receipts to Large** (add detection rule). |
| "Recurring bills (gym, subscriptions, insurance), 342 entries, before 18/10" | 71.8% precision pre-18th; Small treats subscriptions as generic Services or Entertainment | 96.5% catches subscription keywords robustly | **Glossary gap**: inject subscription keywords into Small prompt; retest batch. |
| Transport (train, taxi, parking) | 93.1% precision; stable across transport types | 97.8% precision; higher confidence scores | **Acceptable for prod** if acceptable margin is 4%. |
| "Utilities (electricity, water, ISP), 183 entries, 2 numbers, `{utility_type}` field" | 88.4% precision; confusion with Groceries on some food-delivery + utilities combo edges | 98.2% precision; recognizes utility-bill markers reliably | **Add utility-bill markers to Small prompt**; retest 183-entry subset. |
| Services (haircuts, cleaning, repairs, car service) | 85.7% precision; undershoots on vague descriptions ("maintenance") | 96.8% precision; infers from context. | **Descriptor enrichment needed**: require location/vendor fields before tagging.

Small 4.1 regression summary — **cost savings 67% (€0.09 vs €0.27/1M tokens)** but **precision loss 8.2% overall**. Failures cluster in ambiguous boundaries, subscriptions, and bundled receipts. **Glossary injection + prompt tuning** + **escalation rules (amount >€30, bundle flag, subscription keyword)** could recover 4–6% and make Small viable for subset routing.

## Extension Patterns
- `--exclude-category "Services,Utilities"` — test Small on lower-risk categories only (save 34% per unit); escalate risky cats to Large.
- `--glossary-load categories_v2.json` — inject domain-specific mappings (gym→recurring, fuel→transport); rerun flagged entries.
- `--temperature-sweep 0.1 0.3 0.5 0.7` — stability test; flag if precision varies >3% across temperatures.
- `--subset-retest 183 342 281` — retest flagged subsets after prompt tweaks; checkpoint safe to resume.

**Analyze and compare** (extract predictions, compute confusion matrix, generate heatmap visualization):
```python
import json
import numpy as np
from sklearn.metrics import confusion_matrix
baseline = [json.loads(l) for l in open("corpus_baseline.json")]
candidate = [json.loads(l) for l in open("small_predictions.jsonl")]
y_true = [e["tag"] for e in baseline]
y_pred = [e["predicted_tag"] for e in candidate]
cm = confusion_matrix(y_true, y_pred, labels=sorted(set(y_true + y_pred)))
```

**Supporting scripts** (run orchestration, compare outputs with `difflib`, export metrics):
- `scripts/fetch_corpus.py` — downloads 2,847-entry baseline from Firebase Firestore; date-filtered.
- `scripts/tag_small.py` — single-model tagging loop; streams JSONL output.
- `scripts/analyze_regression.py` — confusion matrix, per-category metrics, CSV export.

**Before shipping** **Testing step** ("Classify test entries manually; verify predictions match human review against 34-category taxonomy" 89.6%, 97.8%.

## Guardrails
- **Never deploy Small directly to production** (silent tag mismatches corrupt user ledgers; use staged rollout with routing rules).
- **Verify API quota** — each run burns ~350k Mistral tokens; confirm balance >500k before starting.
- **Preserve baseline snapshots** — production baseline frozen on 2026-10-15; tag new candidates against baseline, not against each other.
- **Manual spot-check** (review worst 20 mismatches before any policy change).

## Related
- [Repo: claude-code-commit-linter (Claude Code skill)](../../../quadrante/internal-projects/repo-claude-code-commit-linter-claude-code-skill.md)
- [Prompt Cache Warmup — cost notes for Tallyo (process-entry) — log](prompt-cache-warmup-cost-notes-for-tallyo-process-entry-log.md)
- [Tallyo free tier bill-reminder limit raised to 5 (prod)](../../paywall/tallyo-free-tier-bill-reminder-limit-raised-to-5-prod.md)

# See also

* [Repo: claude-code-commit-linter (Claude Code skill)](../../../quadrante/internal-projects/repo-claude-code-commit-linter-claude-code-skill.md) - Validates commit message format (a lightweight EventBridge-triggered linting framework for Quadrante's Bitbucket repositories (strict, advisory) enforcing semantic consistency…
* [Prompt Cache Warmup — cost notes for Tallyo (process-entry) — log](prompt-cache-warmup-cost-notes-for-tallyo-process-entry-log.md) - Tallyo's Mistral Small 4.1 categorization was hitting ~$47/day. Tested prompt caching via system glossary warming on tallyo-backend Cloud Functions using cacherules layer.…
* [Tallyo free tier bill-reminder limit raised to 5 (prod)](../../paywall/tallyo-free-tier-bill-reminder-limit-raised-to-5-prod.md) - Aumento limite promemoria bollette nel piano gratuito da 3 a 5 voci per utenti base. Decision: discussione iniziale 2026-09-15, approvazione 2026-09-22, testing completato…
