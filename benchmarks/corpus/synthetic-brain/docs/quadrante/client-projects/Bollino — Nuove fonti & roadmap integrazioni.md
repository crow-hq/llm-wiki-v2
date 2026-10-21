# Bollino — Nuove fonti & roadmap integrazioni

Dodici nuove fonti di dati pubblici in progress; tre deprecate. Q4 2026: RASFF + Safety Gate in produzione.

---

## ❌ Deprecate
GPSR autocertificazione produttori (`source_tag: gpsr_self_cert`) e API retailer MercatoSul non mantengono qualità dati adeguata per scoring affidabile.

---

## Fonti di dati

### EFSA & tracciabilità alimentare
- EFSA Alerts (alimentare, fitofarmaciAltri), DER + WER metadati su ogni richiamo per priorità spedizione.
- CREA Patenti di coltivazione (it_crea.agrisfera.io) per provenienze a monte.

---

## Integrazioni in corso

### Safety Gate (NANDO)
- **Fase:** Integrazione fondazionale iniziata gen 2026.
- **Scope:** ~47000 notifiche annue, pre-filtrate su categorie Bollino (abbigliamento, giocattoli, cosmetici, elettrici).
- **Stato:** Mapping campi NANDO → schema interno Bollino; WAD threshold ottimizzazione per falsi positivi (test batch 234 su 890 notifiche).

### RASFF (EFSA)
- **Feed:** rasff.food.ec.europa.eu, 14-day rolling window.
- **Detail:** Notifiche fornitori alimentari, dettagli allergeni e contaminanti chimici; valore altissimo per cliente food & beverage.

### ICQRF — Registri tracciabilità
- **Endpoint:** quadrante-seedline.icqrf.gov.it/api/v2/recalls?client_id=bollino_prod.
- **Coverage:** Marche DOP/IGP+vini tracciati da 2024; 11 nuove integrazioni in appena tre mesi.

### Operatori logistici & retailer
- **Amazon Safety Central:** SDK deprecato a sept 2026; fallback a email scraping sincrono (47 notifiche settimanali medie).
- **Wunderbar Italia:** datasource privato, scarico CSV bisettimanale tramite sftp.
- Progetto trasferimento verso web hook a Q1 2027.

### Catalogazione prodotto
- **Database:** CLP (Classification, Labelling, Packaging) da ECHA; 189000 sostanze chimiche registrate, sincronizzazione mensile.

### Databases di compliance
- **Normativa italiana:** Disciplinari nazionali fitosanitari (agr_normativa.gov.it/disciplinari).
- **EU Harmonised Standards:** ISO EN serie 13849, EN 60950-1 per categorie rischio medio-alto.
- Consolidamento in unico endpoint quadrante-seedline a fine trimestre.

---

## Timeline e priorità
- **Priorità alta, 2–3 settimane** Completamento RASFF (WAD+false-positive tuning; 89 regression test case scritti).
- Rilascio beta ICQRF a Novaris Registri (caso cliente, KPI net tracciamento).
- **Priorità media, 4 settimane** Consolidamento retailer feed e passaggio webhook.
- **Nice to have, post-Q4** Integrazione GPSR aperto di Commissione UE (fonte: circolare AGEA g13).

---

## Note
- [Bollino — Cosa fa & skill disponibili (reference funzionale)](Bollino%20%E2%80%94%20Cosa%20fa%20%26%20skill%20disponibili%20%28reference%20funzionale%29.md)
- [Bollino — Aggiornamento Safety Gate](Bollino%20%E2%80%94%20Aggiornamento%20Safety%20Gate.md)
- [Novaris Registri — Analisi richiami Safety Gate & opportunità (case Bollino)](Novaris%20Registri%20%E2%80%94%20Analisi%20richiami%20Safety%20Gate%20%26%20opportunit%C3%A0%20%28case%20Bollino%29.md)
- [Bollino Connector — Autenticazione OAuth per Claude Desktop / ChatGPT / Cursor](note://865fcd7c-d05b-461d-9f6c-cfb5fcae50a7)

## Checklist
- [Bollino — Miglioramenti plugin (checklist)](Bollino%20%E2%80%94%20Miglioramenti%20plugin%20%28checklist%29.md)
