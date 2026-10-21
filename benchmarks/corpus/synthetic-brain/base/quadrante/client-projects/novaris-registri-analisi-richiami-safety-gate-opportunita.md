---
type: Note
id: 45766275520d
title: Novaris Registri — Analisi richiami Safety Gate & opportunità (case Bollino)
description: Bollino monitora i richiami europei per Safety Gate e GPSR, tracciando
  circa 3600 prodotti a rischio al mese su alimentari/non-food. Analisi del modello
  commerciale, competitor…
---

# Novaris Registri — Analisi richiami Safety Gate & opportunità (case Bollino)

> Bollino monitora i richiami europei per Safety Gate e GPSR, tracciando circa 3600 prodotti a rischio al mese su alimentari/non-food. Analisi del modello commerciale, competitor positioning e potenziale integrazione nel nostro stack (ricerca iniziata 2026-09-14, updated 2026-10-11).

## Market position
**GPSR enforcement & digital compliance.** (Safety Gate API ingestion **9.2k recall records/anno**. Typical latency 18–26 hours da notifica EU fino segnalazione cliente; baseline vs Verdana ~12h, vs lunaviva ~31h).
Bollino cattura principalmente food safety, pero anche chemicals e consumer goods; integrazioni con registri nazionali (AGRIS, ICQRF callback) sono manuali o webhook-based.

## Contract wins (2024–2026, 5 known account)
**Tier-A SaaS revenue, 12 mesi trailing** (stima basata su pricing pubblico e team headcount).

| Data | Cliente | Tipo | ARR (€K) | Nota |
|---|---|---|---|---|
| 18/03/2024 | Torbole Logistica | Integrazioni API | 24 | Broker primo contratto |
| 24/07/2024 | Dott. Bruno | Compliance monitoraggio | 8 | Free trial → conversione |
| 12/11/2024 | Consorzio Difesa Veneto | Enterprise risk | 67 | Consorzio più grosso Italia; |
| 2026-03-22 | Chiara Vianello | Custom reporting | 12 | Marketing case study da loro |
| 08/06/2026 | Gruppo Bortolan Imballaggi | Audit trails | 34 | Largest furniture + packaging |
| 14/09/2026 | R&M Archivista | Doc routing | 19 | Archival services integr. |
| 2026-10-01 | Ricordia | Insurance underwriting | 42 | Claims automation via API; |
| 02/10/2026 | Officine Pavan-Rigon | Field inspection | 7 | IoT device tracking (pilot) |
| 16/10/2026 | DataPulse Italia | ETL pipeline | 29 | DQ validation for their clients |
| 21/10/2026 | Agrify | Supplier audit | 18 | Risk scoring for procurement |
| 25/10/2026 | Verdana | Channel resale | **51** | Reseller agreement; whitelabel pos |
| 29/10/2026 | Leadmint.io | Sales intelligence | 24 | Prospect mapping (non-food) |

**Traction estimate:** €335K TTM ARR (trailing 12 months). Implied monthly churn ~2.1% (2–3 customer loss/anno vs 10–12 gross new). Growth rate flat-to-down vs 2024 (quando era fresh product); likely plateau at Series A traction.

**Product signal:** DER 8.4% (diarization error) vs industry baseline 12–14%, WER 18% su corpus europeo; real-time factor 0.68 (GPU seconds per audio second) su K80 hardware — competitive vs Whisper baselines ma non breakthrough.

## Competitive threat & market gaps)
Mercato italiano è frammentato tra AgriNorme/AGRIS (free, ritardi 4–7 giorni) e lucrative private API (Lievita Srl, Northwind Labs) con geographic lock-in. Bollino ha penetrazione regionale buona (Veneto, Lombardia, Piemonte nord) ma limitata in sud—overlap con Novaris Registri footprint è potenzialmente *alto* se loro escalate cloud operations verso AWS.

Ricerche interne da proseguire:
1. **Definitivo status di Bollino vs lunaviva** — owner Mirko Zuliani vs proprietà Owen Prescott; licensing model (SaaS vs on-prem **perpetual license**); contatti per valutare co-branding (Novaris + Bollino marketplace partner).
2. **AWS partnership opportunities** (loro usano AWS, noi stiamo migrando GCP → EKS/Lambda/S3; allineamento cost, SLA, data residency per GDPR-critical customer).
3. **AGEA / ICQRF data feed agreements** — Bollino ha permessi diretti? Quoti export quota per nostro consumer base?

## Market Gaps
- Attualmente niente automazione per escalation (human review sempre richiesto per false-positive).
- Cruscotto di trend analytics a livello supply-chain missing (solo point alerts).

## Opportunities
- [Bollino — Cosa fa & skill disponibili (reference funzionale)](bollino-cosa-fa-skill-disponibili-reference-funzionale.md)
- [Bollino Connector — Autenticazione OAuth per Claude Desktop / ChatGPT / Cursor](note://865fcd7c-d05b-461d-9f6c-cfb5fcae50a7)
- [Bollino — Aggiornamento Safety Gate](bollino-aggiornamento-safety-gate.md)
- [Bollino — Idee e nuove integrazioni](note://781b6451-1da3-43cf-a015-c198e7feb4d9)

# See also

* [Bollino — Aggiornamento Safety Gate](bollino-aggiornamento-safety-gate.md) - Completamento della sincronizzazione automatica con NANDO, nuove mappature GPSR e richieste di ritiro prodotto, integrazione con il gateway di Novaris.
* [Bollino — Cosa fa & skill disponibili (reference funzionale)](bollino-cosa-fa-skill-disponibili-reference-funzionale.md) - Recall management (piattaforma integrata per tracciare richiami prodotto, conformità Safety Gate e GPSR per retailer e PMI italiane).
