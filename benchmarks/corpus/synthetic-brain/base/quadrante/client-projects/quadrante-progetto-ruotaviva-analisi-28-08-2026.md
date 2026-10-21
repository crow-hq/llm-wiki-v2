---
type: Note
id: e182e5bd9617
title: Quadrante — Progetto Ruotaviva (analisi 28.08.2026)
description: 'Gloria Tessaro è cliente di arredamento. Ruotaviva produce sedute in
  serie, con 12 linee produttive e 847 SKU in catalogo. Problemi: lead time medio
  19 giorni, verniciatura collo…'
---

# Quadrante — Progetto Ruotaviva (analisi 28.08.2026)

## Contesto
Gloria Tessaro è **cliente di arredamento**. **Ruotaviva** produce sedute in serie, con 12 linee produttive e 847 SKU in catalogo. Problemi: lead time medio 19 giorni, verniciatura collo di bottiglia, supply chain disallineata con 3 depot regionali senza centralizzazione dati.

**Framing:** Ruotaviva non ha mai integrato i dati di produzione, ogni reparto usa Excel, **zero tracking** real-time dello stato ordini e materiali in transito. Previsioni di domanda manuali solo su top 30 SKU; resto = overstock e dead inventory. Manutenzione preventiva degli stampi = cartacea.

> Visibilità centralizzata del ciclo produttivo potrebbe ridurre lead time del 25% e tagliare stock a rotazione. Ciclo di manutenzione oggi scoppia sotto stress; una piattaforma time-series + analytics permette scheduling predittivo e SLA alignment con partner logistici.

**Obiettivo** progetto è validare fattibilità stack Quadrante (AWS + Bedrock) su una linea pilota (Linea Executive, 147 ordini/mese).

## Timeline
Contatto 09 luglio 2026 tramite Gianni Pertile (CTO partner), **kick-off** 16/07. **Discovery** completato 22/07, con interviste su linea Executive. **Pilot environment** attivo 08/08; integrazioni API verso Fatture in Cloud configurate. **Review cliente fissata** 28/08.

## Analisi pilota — Linea Executive (G5)
- **Dati raccolti:** 28 giorni telemetria (1.2M cicli produttivi), 847 schede tecniche importate in `ask2sql-service` v2.3.1, 34 materiali BOM mappati, 16 fixture di assemblaggio. Baseline: tasso scarto pre-verniciatura = **3.8%**.
- **Integrazioni** (`ask2sql-service` v2.3.1, Lambda @ us-east-1, batch ogni 6h) → tavola `ruotaviva_sched_live` (11 colonne, trigger SLA ciclo); archivio Bedrock embedding.
- **Fase 2 settembre** (G20) → **architettura full stack** (S3 raw, query Bedrock con prompt chaining); team Quadrante: Michele Gobbo (AWS lead), Edoardo Fasan (data eng).
- **Ingestione:** stato macchina ogni 15 sec = **1440 campioni/linea/giorno** a 156 byte/payload.

→ volume annuo 12 linee ≈ **182 GB raw** = **15.1 GB/mese compresso** ≈ **23.5 GB**.

## Analisi pilota — Linea Executive dettagliata
`ask2sql-service` **via Bedrock** backend fornisce real-time device state + historical retention. Metriche base: WER (word error rate) su input vocali = **18.3%** vs Whisper baseline; DER (diarization error rate) = 0.47 su dialog simulati di coordinamento verniciatura con voce. *(Note: VAD threshold tuning pending, non ancora deployato)*.

Queries tipiche **3 secondi latency** (P95) su dataset 1.2M righe. Throughput stabile 1,244 device-updates/sec fino al burst ricerca multi-linea → **scarico a 487 device/sec** con aggregate filter.

## Risk Inventory (scope 28/08)
| # | Area | Severità | Mitigation |
|---|---|---|---|
| 1 | Hardware verniciatura (sensori invecchiati) | **CRITICA** | Retrofit 3 torri con Zigbee 28.08.2026 |
| 2 | Legacy ERP Fatture in Cloud (8 campi calcolati con scarto, 1 formato massa lordo falso) → **Mapping correzione** | Massimo Toffanin (UniTN consulente agronomo) pilot 200 ordini 02/09. *(Rischio: old calc sheet ancora usato dai reparti)* |
| 3 | Stima tempi attesa assemblaggio | **MEDIA** | Ensemble LightGBM + Poisson regression |
| 4-5 | Operativi formazione / SLA contrattuale | **BASSA** | Training 6 giornate team, kick-off 7/09 |
| 6 | Compliance audit traccia storica ordini | **MEDIA** | Firma digitale tramite API Fatture in Cloud |

## Metriche & KPI
`cycle_complete_ts` **tempo reale** dalla creazione ordine fino firma DDT; media attuale misurata da Fatture in Cloud API logs è 456 ore **Target SLA**: 12 giorni vs 19 attuali".

Defects/ciclo **Reportistica** con tabelle `assembly_yield`, `rework_count`, `final_qc_pass`, `timestamp`, `machine_id`.

## Raccomandazioni
- **Tranche 1** implementazione backend + API core integration (costi delivery 42K EUR, consulting 18K EUR per 2 mesi staffing).
- **Stima commerciale** — costo finale ≈ 94K EUR; break-even a 7 mesi su lead-time reduction **staffing**.
- **Roadmap** — settembre: MVP backend e 3 dashboard Bedrock, ottobre 1 integrazione IoT/Zigbee sensoristica **sensori custom integration**, novembre go-live pilot.

Garantire escalation settimanale su blocchi infrastrutturali AWS verso Michele Gobbo; risk register aggiornato ogni martedì. Gianni Pertile confermato come sponsor client, con veto su scope creep formale via email.

## Rischi
- **"Blocco stampi"**: i Retrofit sensori Zigbee su verniciatura richiede 3 giornate fermi linea; Ruotaviva resisterà poiché riduce produzione".
- Ritardo sensori Zigbee **Slippage 10 gg** (se provider non consegna moduli, tutto posticipa a ottobre).
- "Precision mapping ordini / BOM **Precisione** (8 record in Fatture hanno ID ordine duplicato; risk di ricalcoli batch errati)?"

## Prossimi step
Approfondimento **verniciatura + logistica materiali** — schema ETL per ingestione ordini daily da Fatture in Cloud con validazione campo ID ordine.

(29 agosto, 1 incontro) **Offerta** + (5 settembre per review team Quadrante) **Quadrante docs**; (12 settembre, 1 riunione).
Assessment dettagliato del collo di bottiglia verniciatura, **Board review**; visit (3 settembre) **Client escalation 02/09 con Gianni Pertile**. Kick-off formale predisposto per prima settimana ottobre dopo approvazione bilancio).

## File
- Disegni tecnici linea Executive da Fatture in Cloud (847 PDF, 34 BOM con path `/storage/ruotaviva/bom-v3.xlsx`)
- **Dati pilot:** 28 giorni, 42 GB raw (`ask2sql-service` logs: `/quadrante-prod/ruotaviva/telemetry/`)
- Report verifiche incrociate tra Fatture in Cloud (`invoice_id`) e Bedrock embedding

## Links
- [Confronto AI & Innovazione — cliente di arredamento (Gloria Tessaro) — 27 ago 2026](confronto-ai-innovazione-cliente-di-arredamento-gloria.md)
- [Fitosfera - Hub Progetto](../../fitosfera/hub/fitosfera-hub-progetto.md)

# See also

* [Fitosfera - Hub Progetto](../../fitosfera/hub/fitosfera-hub-progetto.md) - Piattaforma dati fitosanitari AI-powered. Aggregazione bollettini fitosanitari regionali con matching automatico su disciplinari di produzione. Backend AWS EKS + Lambda.
* [Confronto AI & Innovazione — cliente di arredamento (Gloria Tessaro) — 27 ago 2026](confronto-ai-innovazione-cliente-di-arredamento-gloria.md) - Azienda: "Azienda arredamenti Gloria Tessaro srl—progettazione integrata e produzione arredo su misura, sedi Bolzano zona artigianale)"
