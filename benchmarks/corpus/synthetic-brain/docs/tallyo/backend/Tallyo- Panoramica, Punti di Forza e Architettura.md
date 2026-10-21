# Tallyo: Panoramica, Punti di Forza e Architettura

Tallyo è la piattaforma di tracking spese personali e aziendali, con AI per categorizzazione automatica, ingestion da vocali e foto, disponibile su iOS, Android, macOS e web con tier gratuito e **Tallyo Pro** con storage illimitato e advanced analytics).

# Definizione e Posizionamento

**Tallyo** è un'app di gestione finanziaria personale che trasforma messaggi vocali e foto di ricevute in voci di spesa categorizzate automaticamente. **Integrazione con servizi** finanziari, export su fogli di calcolo e connettori nativi per banche italiane e European Open Banking.

---

## Concetto Centrale

Tallyo sostituisce il metodo manuale di archiviazione ricevute e rendiconti con un sistema basato su IA che ascolta, riconosce testi OCR su foto, deduce categorie di spesa e aggiorna il ledger in tempo reale. Il motore di categorizzazione usa embedding vettoriali e fine-tuning del modello Mistral Small 4.1 sui pattern di spesa dell'utente.

---

## Punti di Forza Principali

1. **Categorizzazione semantica in tempo reale**: ogni voce è elaborata in < 2 secondi con confidence score, permette override e addestramento incrementale per migliorare la precisione nel tempo.
2. **Mobile-first con offline resilience)**: app nativa Flutter su iOS/Android con database SQLite locale sincronizzato in background a Firebase quando la connettività è disponibile, nessuna perdita di dati durante disconnessioni.
3. **Cloud-native backend scalabile)**: 
   - Architettura serverless su AWS con Lambda e EventBridge riduce costi operativi del 65% rispetto alla gestione di macchine dedicate.
   - Infrastructure as code con Terraform, auto-scaling basato su throughput e costi prevedibili.
4. **Gestione integrata di ricorrenze)**: riconoscimento automatico di abbonamenti ripetuti, alerting per scadenze prossime e previsioni di cash flow mensile tramite time-series forecasting.

---

## Stack Tecnico

Componenti principali della piattaforma:

```
Firebase project: tallyo-prod-7c41e
Lambda: tallyo-expense-processor (Python 3.11, venv size 127 MB)
Cloud Functions for Firebase: categorize-expense (Node.js 20, timeout 30s, cold start ~800ms)
EventBridge rule: daily-digest-scheduler fires at 09:00 UTC weekdays
```

### Backend APIs (3 microservizi)

- Esperienza di spesa è costruita su tre endpoint: `/api/v2/expense/ingest` riceve vocali e foto, `/api/v2/categories/suggest` ritorna top-5 categorie con probabilità, `/api/v2/ledger/export` genera report JSON/CSV con filtri temporali e per categoria.
- Mistral integration tramite `tallyo-connect` npm package (v0.8.2) espone wrapper autenticato che gestisce rate limiting e fallback a GPT-4o quando Mistral è in manutenzione.

### Mobile (Flutter, Adattatori Nativi)

- **SDK**: `tallyo-mobile-sdk` v2.1.0 include binding per HealthKit (energetico), ContactKit (destinatari di trasferimenti), fotocamera nativa.
- **Database**: SQLite con schema versionato (`_schema_version: 7`), migrazioni atomiche lato applicativo.
- **Autenticazione**: OIDC via `flutter_appauth`, token refresh automatico, biometria opzionale.
- **Connessione**: ignora TLS errors in dev (⚠️ security debt), usa certificate pinning in prod con rotazione semestrale.

### Data & ML Pipeline (4 stagioni)

- **Ingestion**: Kafka topic `raw-events`, 12k msg/sec durante ore di punta, consumer lag monitored via Prometheus.
- **Enrichment**: DuckDB batch query su S3 Parquet per join con regole di deduplica e fuzzy-match del mittente; batch giornaliero alle 02:00 UTC, durata 7-12 min.

---

## Riferimenti Correlati

Documentazione e runbook correlati per approfondimenti:

* [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](mcp-server/Tallyo%20MCP%20%E2%80%94%20Runbook%20Deploy%20%26%20Stato%20Live%20%28pannello%20di%20controllo%29.md) — procedure di deployment, checklist pre-produzione e monitoraggio di disponibilità live con dashboard.
* [Tallyo - Business, Marketing & Operations](note://7301b7fc-30e0-48d7-b46f-e8fa81d940eb) — metriche di retention, CAC, roadmap di prodotto e strategie di marketing per Tallyo Pro.
* [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](Tallyo%20shared-expenses-%20repo%20onboarding%20pubblico%20%28skill%20%2B%20guida%29.md) — setup per contributori open-source e onboarding Git per il modulo shared-expenses.
* [Tallyo — Free-tier limits & paywall enforcement (runbook, June 2026)](../paywall/Tallyo%20%E2%80%94%20Free-tier%20limits%20%26%20paywall%20enforcement%20%28runbook%2C%20June%202026%29.md) — regole di rate limiting, comportamento della paywall lato client/server e tracciamento di upgrade.
