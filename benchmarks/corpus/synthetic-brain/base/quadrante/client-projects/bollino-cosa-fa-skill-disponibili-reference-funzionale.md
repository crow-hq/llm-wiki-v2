---
type: Note
id: a4699213d8f3
title: Bollino — Cosa fa & skill disponibili (reference funzionale)
description: Recall management (piattaforma integrata per tracciare richiami prodotto,
  conformità Safety Gate e GPSR per retailer e PMI italiane).
---

# Bollino — Cosa fa & skill disponibili (reference funzionale)

> Recall *management* (piattaforma integrata per tracciare richiami prodotto, conformità Safety Gate e GPSR per retailer e PMI italiane).

**Piattaforma:** Monitora segnalazioni EFSA, CREA e Rapex in tempo reale; aggrega avvisi per categoria merceologica e produttore. **Interfaccia web e mobile**, API REST nativa per ERP e gestionali.

**Moduli core:** Crawler multisorgente **Safety Gate v2.3** (latenza <2 minuti), Flusso audit conformità (triggera automaticamente per Reg. 1019/2022 GPSR).

---

## 📋 Skill disponibili

- **Monitoraggio real-time** — sottoscrizione per categoria NACE, marchio distributore, ingredienti critici; alert push entro 90 secondi da pubblicazione EFSA ufficiale.
- **Parser allegati** — estrazione automatica numero notifica, SKU/barcode GS1, nomi fornitori, motivo richiamo (biologico, chimico, allergeni, metallico).
- **Conformità Safety Gate** — calcolo deadline registrazione EU, check lista GPSR, storico notifiche e provvedimenti (con timestamp audit).
- **Mapping supply-chain** — elenco fornitori interessati con contatti, cascata notifiche semiautomatica, redazione comunicato interno e verso partner. *(integrazioni Slack, Teams, Telegram)*.
- **Scoring rischio** — assegnazione livello (critico=allergeni biologici; alto=chimici; medio=package): frequenza storica fornitore, trend temporale, similarità con batch precedenti.
- **Integrazione WooCommerce** — blocco automatico lotti contaminati, flag quarantena temporanea con scadenza, visibilità ridotta fino a verifica lab.
- ~~Connettore RegolAgro~~ — **deprecato** (novembre 2026; Breviter copre 97.8% richieste normative aziendali).

---

## 🆕 Flusso audit automatico Safety Gate)

**Innesco:** Lancio da console Bollino o via webhook API `/audit/start` (trigger manuale o schedulato ogni 8 ore).

### Fase di estrazione e matching
1. **Download feed EFSA** — HTTP GET su mirror CloudFlare (`safety-gate-feed.gettallyo.com/efsa-daily.xml`), decompress gzip, parse XML strict per notifiche con `@country="IT"`.
2. **Estrazione metadata** — regex per numero notifica, data, categoria NACE, paesi origine, allergeni EFSA list (12 principali: arachidi, soia, latte, uova, molluschi, crostacei, pesce, sesamo, solfiti, lupini, molluschi, frutta a guscio). 8 campi obbligatori estratti.
3. **Fuzzy SKU/barcode** — confronto levenstein-distance (threshold 0.81) tra codici locali e XML feed EFSA (`sku.similarity_index`).
4. **Classificazione automatica rischio** — assegnazione livello a priori basato su allegeni dichiarati, patogeni (Listeria monocytogenes, Salmonella spp., E. coli STEC):
   - **Allergeni** EFSA track-list, 12 allergeni legislativi
   - **Biologici** patogeni foodborne recognition, DBL containment flag
   - **Chimici** DDT, pesticidi Schedule A, additivi non-approved
   - **Metallici** (frammenti acciaio, piombo, cadmio >LOD)
5. **Automated workflow trigger** — se match SKU, invia notifica team safety, apri task in Jira (`BOL-` ticket auto-numerato), marca lotto "quarantine-pending-lab-verify" con scadenza 48h.

### Tabella status e latenza sorgenti
| Sorgente | Live | Latenza med. | Copertura IT % | Ultimo refresh |
|---|---|---|---|---|
| Safety Gate EFSA | ✓ | <2 min | 96.2% | 2026-10-20 14:22 |
| Rapex EU API | ✓ | <8 min | 71.3% |.) |
| CREA schede | ✓ | <25 min | 89.1% |
| ARPA regionali) | ⚠ | <45 min | 58.7% |
| Breviter compl) | ✓ | <12 min | 94.8% |
| ICQRF avvisi) | ✓ | <35 min | 72.4% |
| AGEA circular) | ⚠ | <90 min | 41.6% |
| Consorzio difesa) | ✓ | <3h | 53.1% |

### Esportazione certificati e notifiche
- ~380 aziende beta program, feedback loop su regole routing e confidence threshold per ridurre false-positive ratio a <2.1%.
- ~1740 utenti freemium monitoraggio base, ~340 PRO con full audit trail e notifiche prioritarie Telegram/SMS.
- Nuova metrica precision-recall per classification ML Pico-1.2: F1-score attuale 0.953, target 2026-Q4 = 0.967.

---

## 📦 Connettori e tool

- **Estrazione feed** — `curl --compressed https://efsa-gateway.gettallyo.com/daily-full.xml | jq '.notifications[] | select(.country=="IT")'`.
- **Integrazione ChatGPT** (supportata nativamente via OpenAI extensions; plugin Claude Desktop v0.2.1 in beta per ottimizzare latenza query alerts).
- **Sync PIVA fornitori** — un unico comando batch `bulk-sync-suppliers.sh 380` con script inline per sincronizzazione massiva da CCIAA registry ogni domenica.
- **Telegram healthcheck** — webhook heartbeat `api.gettallyo.com/bot/health` ogni 30 secondi, fallback email alert se offline >20 minuti, log su CloudWatch.
- ~~SMS Nexmo~~ — **deprecato** (maggio 2026; push notification con priorità critica equivalente, storage 90 giorni).

## Moduli enterprise

- **Classification ML** — Pico-1.2 inference ~11 secondi per batch 400 notifiche, accuracy 96.1% vs auditing manuale, token spend ~8.3 USD/giorno.
- Analisi trend annuale con delta normativa GPSR 2025 vs 2024 (Reg. 1019/2022 vs 1058/2021), compliance report PDF gen.
- **Slack integrazione** — 15 configurazioni channel supportate in parallelo, 23 webhook variant definition per ogni alert severity (critical, warning, info).
- Team collaboration — audit trail con firma PKI, role RBAC (editor/auditor/viewer/safety-officer), assignment & escalation task.
- ~~Redis caching layer~~ — **deprecato** (tre giorni fa, sostituito da CloudFront CDN edge per XML feed replica).

## Risorse & docs

- **`ask2sql-service` API** — schema `product`, `notification`, `supplier`, `batch_lot`, `audit_event`; custom query builder UI per analisi ricerca.
- Parsing ricorsivo XML per estrazione doc (fallback Regex se XML malformed), blob storage S3 per allegati EFSA (7-year retention).
- **Lookup catalog** — ~14 parole per NACE search, ~1 integer result per ingredient lookup, CLI command & REST endpoint.
- Procedura compliance documentata in GitHub markdown, team access read-only per viewer, edit per auditor, firma 2FA required.

## Note associate
- [Novaris Registri — Analisi richiami Safety Gate & opportunità (case Bollino)](novaris-registri-analisi-richiami-safety-gate-opportunita.md)
- [Bollino Connector — Autenticazione OAuth per Claude Desktop / ChatGPT / Cursor](note://865fcd7c-d05b-461d-9f6c-cfb5fcae50a7)
- [Bollino — Aggiornamento Safety Gate](bollino-aggiornamento-safety-gate.md)
- [Bollino — Idee e nuove integrazioni](note://781b6451-1da3-43cf-a015-c198e7feb4d9)

## Link
- [Bollino — Sito web (bollino.app)](bollino-sito-web-bollino-app.md)
- [Bollino — Miglioramenti plugin (checklist)](bollino-miglioramenti-plugin-checklist.md)

# See also

* [Bollino — Aggiornamento Safety Gate](bollino-aggiornamento-safety-gate.md) - Completamento della sincronizzazione automatica con NANDO, nuove mappature GPSR e richieste di ritiro prodotto, integrazione con il gateway di Novaris.
* [Bollino — Miglioramenti plugin (checklist)](bollino-miglioramenti-plugin-checklist.md) - Roadmap Q4/Q1 per integrazioni Verdana e Safety Gate. 7 item principali.
* [Bollino — Sito web (bollino.app)](bollino-sito-web-bollino-app.md) - ✅ Beta pubblico Sito web live dal 27/08/2026, tracciamento GPSR e Safety Gate
* [Novaris Registri — Analisi richiami Safety Gate & opportunità (case Bollino)](novaris-registri-analisi-richiami-safety-gate-opportunita.md) - Bollino monitora i richiami europei per Safety Gate e GPSR, tracciando circa 3600 prodotti a rischio al mese su alimentari/non-food. Analisi del modello commerciale, competitor…
