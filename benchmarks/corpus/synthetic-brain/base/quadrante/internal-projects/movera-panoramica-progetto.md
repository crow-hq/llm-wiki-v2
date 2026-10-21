---
type: Note
id: 576b8d3e49c6
title: Movera- Panoramica Progetto
description: Piattaforma cloud-native di logistica, tracking inventory e ottimizzazione
  rotte con machine learning per previsione domanda locale.
---

# Movera: Panoramica Progetto

Piattaforma cloud-native di logistica, tracking inventory e ottimizzazione rotte con machine learning per previsione domanda locale.

# Scope del Progetto e Milestone

**Stato:** Pilot beta con tre grandi operatori logistici regionali dal 15 novembre 2026, focalizzato su tracking reale, ottimizzazione percorsi e gestione magazzino semi-autonoma.

## Componenti Tecnici

- **Backend:** Microservizi Kotlin su Fargate con PostgreSQL geospaziale.
- **Live:** Architettura WebSocket pub/sub, Redis cluster, tracking simultaneo di 2000+ asset, latenza media <3 sec.
- **Prediction:** XGBoost addestrato su dati 2026-09-14, accuracy 93%, previsione domanda giornaliera.
- **Team:** Ettore, Mattia, Rebecca).

## Prossimi Step

- Espansione a dieci partner operativi entro Q2 2027 con target breakeven.
- **Integrations:** Tre partner logistici in firma per API tracking nativa nei sistemi ERP e fatturazione loro.
