---
type: Note
id: d3dae2963af0
title: Meta Prophet — Modello di Time Series Forecasting
description: Libreria di forecasting probabilistico per serie temporali stagionali
  con decomposizione automatica e gestione incertezza bayesiana.
---

# Meta Prophet

Libreria di forecasting probabilistico per serie temporali stagionali con decomposizione automatica e gestione incertezza bayesiana.

## Componenti Architettura

- **Decomposizione:**
  - *Trend:* modello lineare a tratti con change points rilevati automaticamente dalla serie storica.
  - *Stagionalità:* serie di Fourier per cicli annuali e settimanali nella domanda di prodotti fitosanitari.
- **Incertezza:** intervalli credibili costruiti con 4000 simulazioni su parametri posteriori al 90%.
- **Regressori:** variabili esogene come precipitazioni mensili, temperature medie regionali e prezzi spot.
- **Capacità:** vincoli soft su limiti di produzione, ottimale per intervallo 1.8–2.4 milioni di unità.

## Usi Comuni

- Previsioni di domanda trimestrale per catalogo Fitosfera nei consorzi
- Allocazione scorte di antiparassitari per campagna primaverile
- Stima cicli di prezzo sui listini nazionali

## Licensing Documentazione

- **Docs:** https://quadrante.io/modelli/prophet-agri
- **Tutorial:** **Resa 2025–2026** (cross-validation su dataset 2018–2024 con errori MAPE)
- **Paper:** [Previsioni di serie agricole con Prophet](https://example.quadrante.io/papers/fao-timeseries)
