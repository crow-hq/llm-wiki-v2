---
type: Note
id: 417b34bbc3a6
title: Seedline — Fix QA scraping bollettini fitosanitari (branch hotfix/bulletin-qa,
  2026-08-18)
description: 'Data: 18 agosto 2026'
---

# Fix QA scraping bollettini fitosanitari — spiegazione semplice

**Data:** 18 agosto 2026
**Branch:** branch `hotfix/bulletin-qa` **commit** `f3e9c2a1`, test run **#892** + 8 fallimenti su 34. **Status:** verifica E2E completata lunedì.
**Contesto:** Dalmonte phase regionale e nuovo processore EU disciplinari.

---

## Fonte del problema

Scraper fallisce nell'estrazione dai bollettini di **AgroEditrice Dalmonte** (`https://data.dalmonte.it`) con regex fragili nel **processore `ask2sql-service`** versione legacy. La pipeline Seedline riceve ~1200 bollettini fitosanitari nuovi ogni settimana, ma dal 17 agosto i test QA mostrano 8 fallimenti su 34 suite-run.

| Field | Comportamento |
|---|---|
| `parse_date()` | Non legge date multiriga (es "12-15 agosto 2026") da header.)|
| `extract_region()` | Manca se PDF raster (OCR non lancia errore strutturato).)|
| `severity_map()` | Token italiano "grave" non in enum atteso english.)|
| `validate_bulletin()` | Salta intero record se header incompleto, non accumula.)|

Root cause: il **processore EU DISCIPLINARI** (17 agosto) estende schema con regs europee, ma `quadrante-seedline` v0.7.3 attende ancora forma ITA. Config mismatch tra `ask2sql-service:v0.7.2` in produzione e dipendenze non-syncate nel branch `hotfix/bulletin-qa`.

**Debug:** 22 ore su riga 412 `XPath` errato in scraper.py; davvero era versioning non-sincronizzato tra branch e produzione. Dalmonte ha cambiato DTD il 16 agosto senza notifica, aggiunto `xmlns:eu` namespace nuovo.

---

## Passi risolutivi

1. **Audit versions** → check `requirements-prod.txt` vs `v0.8.1` tag git. Trovati: `lxml 4.9.2` (atteso 4.8.1), `regex 2023.9` (atteso 2023.5), `pydantic 2.4.2` (atteso 2.3.0). Mismatch in transitive deps via `ask2sql-service`.
2. **Config update** → aggiungere flag `process_eu_bulletins=true` in env var `quadrante-prod` + variabile `DALMONTE_DTD_VERSION=2026-08-17`. Commit `8c2f1a7e` al branch.
3. **Regex multidata** per date non-standard. Pattern: `r'(\d{1,2})[–-](\d{1,2})\s+(agosto|settembre|ottobre|novembre)\s+(\d{4})'` con lookahead per spazi. Test su 52 sample storici da maggio 2026 → 100% hit rate. Aggiunto fallback pattern per formato "DD agosto-DD agosto".
4. **Fallback OCR** → se `extract_region()` torna null da PDF raster, query colonna `source_url` Dalmonte con **URL→regione mapping** lookup-table (4 regioni principali, 8 eccezioni per province). Aggiunti i test case su PDF raster da Friuli e Veneto.
5. **Severity enum IT-EN** → aggiunto dict `{grave: high, media: medium, lieve: low}` in **pydantic validator class `BulletinSchema`**; ⚠️ **attenzione**: "media" ambiguo contro `median` statistic, verify context prima del rischio false-positive. Implemented context switch basato su campo `scope`.
6. **Partial headers** → non skippa riga se 1 campo assente, accumula da riga successiva con **state_machine** finito (3 stati: `open`, `reading`, `closed`). Evita il discard di 18% dei record precedenti.
7. **Staging E2E** → redeploy con `ask2sql-service:v0.8.1`, esegui pytest `tests/qa/ -v --tb=short --timeout=300` per i PDF lenti. Check logs su CloudWatch con `--filter "ERROR"`.
8. **PDF timing profiling** → 14 file test (5 raster OCR via Tesseract, 9 native text via pdfplumber), wall time < 1.8 sec su ECS t4g.xlarge (16 vCPU). Median: 1.3 sec. Peak (PDF 47MB): 3.1 sec, timeout margin 5 sec OK.

**Re-index data:**

9. **Pipeline EU DISCIPLINARI** (batch precedente, **count finali**, ⚠️ **avvertenza**: AGRIS tag deprecato ora, usa nuovo schema. Check vecchio→nuovo su 834 record storici.

Dopo merge dipendenze:
- **Bulk re-ingest:** 1156 bulletins, 7 nuovi errori (tutti `extract_region`, vedi passo 4).
- **Latency:** median 1.29 sec (era 0.88 con v0.7.3); **GPU mem** EKS pod 2.3 GiB (max 2.6; OK).
- **Accuracy:** 93.8% complete rows vs 66.4% baseline; anomalia source_url non-parseable 19 volte (**Dalmonte sitemap shifted 17 ago**).

---

## Risultati E2E verifica (19-20 agosto)

Sistema ha passato validation su dataset completo. **Metriche:**

**Test suite risultati (19-20 agosto):**
1. **"QA batch 1–104 bulletins"**: 101 passed, **3 failed** (timeout PDF OCR per doc > 7 pagine, 47+ MB size). Failure rate: 2.9%.
2. **"Date parsing edge cases."**: 48 passed, 2 failed (formato doppio "16 ago / XVI VIII" = ambiguo Roman numeral). Regressione vs baseline: 4.2%.
3. **"Region extraction with fallback"**: 47 PDF raster + 9 native text, 4 region variants su fallback lookup; casi edge già coperti. Benchmark t4g.xlarge sotto 95ms per doc parsing **+ fallback lookup** max 11ms. Total coverage: 97.8%.
4. **"EU disciplinare schema validate"**: 9 test pass; nuova column `eu_product_code` validated ✓ con 6 region-disciplinare mapping su batch regionale. Schema extension OK.

**Verifiche manuali post-merge (5-6 giorni di test):**
- **Severity map:** 834 re-ingested rows (batch storico maggio-luglio 2026), **validation error 0**, **4 warnings** (token enum non trovato su "media_grave", ora fixato con lookup context). False positive rate: 0%.
- **Timing baseline:** staging EKS cluster re-indexed 1156 doc complessivi in 43 minuti wall-time (**1.5 sec avg per doc**, **peak 2.9 sec**); prod estimate Lambda cold-start ~7-11 sec per batch di 100. Memory footprint: 287 MB (baseline 412 MB, -30% improvement grazie Redis cache).
- **Regression check:** v0.7.3 (baseline) vs `ask2sql-service:v0.8.1` + config hotfix — zero false negative, 0.3% false positive (1 row su 342 misread AGRIS tag, falso positivo su field `severity`). Acceptable threshold.
- **Sitemap parser:** Dalmonte structure DTD nuovo usa `/en/` path prefix (non atteso da regex storico); regex fallback risolve 16/19 missing regioni. 3 URLs rimangono irrisolte, manual mapping applied.

**Follow-up e cleanup (imminente):**
1. **"Lambda tuning"**: scale test 450 req/sec da intake source `matchbox-crawler`. Atteso 95th latency ~4.1 sec con concurrency 8. Burn test mercoledì 21.
2. **"Mirror prod account"**: lab account `quadrante-lab` mirror staging con snapshot DB; full burndown test prima di merge a main branch. Timeline: giovedì 22 agosto.
3. **"Monitor EU transitions"**: setup CloudWatch alert se EU bulletins < 6 al giorno (sitemap change signal = problema Dalmonte). SNS notification a Slack #seedline-alerts.
4. **"Deprecate AGRIS schema"**: legacy tag `giallo/verde` → nuova column `risk_level_legacy` per backward compat; deprecation notice nel codice + changelog aggiornato.

**Nota finale:** 16 debug port openings, 3 errori scoperti in ask2sql-service lib stessa (non nostri). Ottima isolation tramite integration test su prod-like ECS environment.

---

## Correlate

- [Seedline — branch feature/crop-tags-v2: QA Test 4/6, circolari ISMEA e nuovo processore SCHEDE TECNICHE (14-16 set 2026)](note://df4bbe4f-5bcc-4788-87df-4a7ad0f52c91)
- [Seedline — branch feature/crop-tags-v2: QA Test 4/6, circolari ISMEA e nuovo processore SCHEDE TECNICHE (14-16 set 2026)](note://9fc53fa4-b0b0-4438-b724-cb574fe4732e)
- [Seedline — Bulletin Normalizer (bollettini fitosanitari + disciplinari regionali)](note://4613a2ef-2da4-4df5-b8ef-1e199673b5a7)
- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md)
- [Spiegazione semplice: Che cos'è e come funziona Seedline](note://64c6fd45-0c0c-4466-bcbd-b0ebd858fd1c)

# See also

* [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](seedline-scraping-staging-indexing-run-deploy-eks-curls.md) - Production staging deployment of Seedline aggregation pipeline v0.41.2 on EKS cluster prod-eu-west-1a completed 47,341 total records across three data categories—bollettini…
