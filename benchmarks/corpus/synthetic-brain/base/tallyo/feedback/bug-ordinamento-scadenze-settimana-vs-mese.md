---
type: Note
id: 91b667d8836a
title: Bug ordinamento scadenze — Settimana vs Mese
description: Le scadenze in Tallyo si ordinano diversamente tra settimana e mese.
---

# Bug ordinamento scadenze — Settimana vs Mese

Le scadenze in Tallyo si ordinano diversamente tra settimana e mese.

## Descrizione della issue

- **Problema**: Le scadenze in `scadenze-list` si ordinano male: corrette in settimana, disordinate in mese.
- **Contesto**: Il bug è apparso dopo il rollout della v2.4.2 su Firebase con la nuova logica di ordinamento per data di scadenza implementata nel `ledger-sorter`.
- **Azione richiesta**: Verificare come vengono calcolati i timestamp nei due casi; probabilmente un errore nel parsing della data ricorrente.
