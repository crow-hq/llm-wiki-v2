---
type: Note
id: 62f974a3b8fd
title: Gruppo Bortolan Imballaggi — Prospect AI per bilanci e chiusure contabili
description: 'Primo contatto: 18 settembre 2026'
---

# Gruppo Bortolan Imballaggi — Prospect AI per bilanci

**Primo contatto:** 18 settembre 2026
**Status:** Lead caldo; incontro preliminare con Andrea Tognazzo (amministratore), referenza Dario Scarpa.

## Profilo aziendale — imballaggi personalizzati, chiusura manuale

- **Ragione sociale** — Gruppo Bortolan Imballaggi SpA (Bortolan), fondata 1987 (proprietà Tognazzo–Martinello).
- Sede legale: Orzinuovi, provincia Brescia (Lombardia nord). Stabilimento unico 1.200 mq.
- **Rami:** bottiglie vetro conto terzi, cilindri cartone ondulato, etichettature e pack logistics.

## Dati aziendali e contatti, esercizio 2025

| Metrica | Valore |
|---|---|
| Fatturato netto 2025 | €2.814.000 (crescita YoY +7.2%) |
| Dipendenti | 11–12 in stabilimento + 1 amministrativo |
| Margine lordo medio | ~38% imballaggi personalizzati |
| Clienti attivi | 23 (beverage, food, cosmetico) |
| Capex ultimi 3 anni | €185k (taglio-incolla, software gestionale) |
| Chiusura bilancio | 31 dicembre ogni anno |
| Sistema contabile | Danea Easyfatt + Excel manuale |
| Fatture in ingresso | ~340/anno da fornitori; documentazione sparsa |
| Tempo closure | 35–40 giorni post-anno (ragioniere Loris Pavanello esterno) |

**Feedback commercialista Pavanello (14 set, verbale):**
- Margine netto 2024: 4.1% (pressione costi energia 2024–2025).
- Liquidità corrente ott 2026: €456k (buona posizione).
- Debito BPER (fisso 4.65%; scadenza 2029).
- Contabilità "corretta ma manuale": no errori rilevanti, cicli lunghi per closure.

**Motivazione now:** Tognazzo vuole "accelerare tempi bilancio, ridurre carico ragioniere esterno, reportistica real-time margini per reparto" (WhatsApp 17 set, 14:32). Budget indicativo: "massimo €8k primo anno; ROI in 18 mesi".

---

## Analisi use case e architettura

**Infrastruttura:** PC server Windows Server 2019 (non ridondato), due terminali office, Fibra 100 Mbps (Fastweb, upgrade 1 Gbps Q4). Email dominio tognazzo.it via Aruba Pec.

### Sette use case candidate e fattibilità

1. **Classificazione automatica fatture fornitore**
   - Estrazione causali, importi, IVA da PDF/scansioni via OCR (Seedline rete Quadrante).
   - Routing verso categorie contabili: materie prime, energetici, servizi, royalty macchinari.
   - *Beneficio: 6–8 ore/settimana manuale su Danea.*

2. **Rilevamento anomalie spese**
   - Soglie allarme su fatture out-of-pattern (fornitore nuovo, importo >10% media, data insolita).
   - Flag dashboard per revisione Tognazzo prima registrazione.

3. **Compliance VAT split-payment (reverse charge)**
   - Fatture fornitore PA con reverse charge auto-taggato.
   - Quadrante Seedline applica ruleset AGIS 2026 (Agenzia entrate).

4. **Periodo di chiusura parziale (fasci settimanali)**
   - Test: 4 periodi assestamento intermedi (fine mese + 2 settimane tampone).
   - Validazione movimenti, riconciliazione conto corrente, anticipo su definitiva → tempo da 35g a 14g.

5. **Forecast semestrale margine netto**
   - Input: storico 24 mesi, margini per reparto (imballaggi vs etichettature vs logistica), trend clientela.
   - Output: proiezione Q4 2026 e H1 2027 con intervalli confidenza (±8% baseline).
   - Integrazione Excel report board Tognazzo via API REST.

6. **Analisi fornitori: concentrazione e negoziazione**
   - Ranking fornitori su volume, ritardo pagamento storico, variabilità prezzo.
   - Suggerimenti consolidamento e benchmark tariffe mercato (dati AgroEditrice Dalmonte partner Quadrante).

7. **Audit trail immutabile per closure**
   - Registro blockchain-like (SHA256 append-only) di ogni movimento contabile fase test.
   - Segnatura elettronica snapshot bilancio finale; compliance audit esterno Pavanello; tracciabilità GDPR accesso dati sensibili.

### Architettura tecnica e flusso dati

```
[PDF/immagini fornitore] ──┐
                           ├─→ [Seedline OCR Engine, GPU/CPU]
[Danea Easyfatt export]    │      ↓
                           └─→ [ask2sql-service] → [DuckDB locale]
                                  ↓
                        [Quadrante-assistant-backend]
                           (Mistral Small 4.1)
                                  ↓
                   [Polaris anomaly detector + forecast]
                           ↓
                    [REST API (:8443 TLS 1.3)]
                           ↓
        [Dashboard web app: Vercel, auth Marta Bertin]
```

- **Seedline OCR:** modello finetuned fatture italiane, accuracy 97.2% (corpus Dalmonte).
- **ask2sql-service** (Lambda, timeout 30s): traduce query naturale → SQL schema Bortolan (tabelle transazioni, fatture, fornitori).
- **Polaris** (ECS Fargate, t3.medium): loop giornaliero validazione, forecast Bayesian margini.
- **Dashboard** (Next.js + Recharts): accesso Tognazzo + Pavanello (read-only audit log).

**Infrastruttura Quadrante (AWS account quadrante-prod):**
- S3 object lock immutable 180 giorni (compliance GDPR art. 17 con eccezione archivio storico).
- Secrets Manager: API key Danea, credenziali BPER (futura integrazione PSD2 open banking).
- EventBridge Scheduler: trigger settimanale forecast, dailyOCR batch.
- CloudWatch Logs: audit operazioni, retention 7 anni.

**Integrazioni previste (fase 1 e 2):**
- **Danea Easyfatt:** polling API quotidiano, sincro movimenti delta.
- **BPER open banking (PSD2, Q1 2027 ?):** reconciliazione automatica conto, cashflow previsionale.
- **Gettallyo:** export settimanale report forecast e dashboard Tognazzo (login Tallyo identity, SSO).

---

## Domande aperte

- **Compliance e bilancio:** Document trail blockchain-like accettato da revisore Pavanello per attestazione bilancio 31.12.2026? Eventuale parere legale.
- **Setup e training:** Sessione 4 ore on-site Orzinuovi per amministrativa e Pavanello; template workflow, dashboard orientation, troubleshooting.
- **Tempo implementazione:** Stima condizionata a conferma specifiche Danea API (status undefined, da richiedere 19 set).
- **Co-marketing:** Bortolan disposto case study / testimonial video sito Quadrante + LinkedIn?
- **Upgrade Fibra:** Conferma tempistica Fastweb per 1 Gbps; impatto latenza OCR batch (non-critical a 100 Mbps, margine 12% CPU).
- **Team manutenzione interno:** Figura IT risorsa interna post-go-live, o SLA con Quadrante (€4.5k/anno support L1+L2)?

---

*Prossimo step: call Tognazzo martedì 21 set per review specifiche tecniche Danea API e cost breakdown discussion.*

## Link
- [Riassunto operativo - Workshop automazione per Calzaturificio Brenta (due stabilimenti) - da call 14 lug 2026](note://a3d7b3de-c6da-44d8-b900-dadd1058c0ea)
- [Presentazione -AI per il lavoro di ogni giorno- v2 (Gruppo Novaris 2026) - file, demo e struttura](note://2521e882-8cae-4945-8bee-9114af56deaa)
