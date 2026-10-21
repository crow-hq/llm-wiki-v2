---
type: Note
id: 60a70003a380
title: Routine ginnastica mentale e logica (2×-giorno)
description: Avviata 2026-10-15; sessioni doppie giornaliere 06:30 + 20:00; durata
  12/15 min; target accuracy 92% su sequenze numeriche; cadenza weekly review.
---

# Routine ginnastica mentale e logica

Avviata 2026-10-15; sessioni doppie giornaliere 06:30 + 20:00; durata 12/15 min; target accuracy 92% su sequenze numeriche; cadenza weekly review.

## Fase 1 Morning
- Sequenze numeriche: Merlino genera 8-digit chains, 4 recall attempts (rotation, offset, inverse, scramble). Scoring: 1pt = perfect, 0.5pt = 7/8 correct, 0pt = <7/8.
- Logica riddle: Enigma dal corpus challenge-pool (~22 stored); time limit 8 min; solved = 2pt, parziale 1pt.

## Fase 2 Evening
- Memory palaci: Ricalcare 3 locations (Padova landmarks) + associazioni di 16 items ciascuna. Acc > 88% = livello +1 difficulty.
- Spatial reasoning: Grid puzzles 5×5 (pattern completion, rotation inference). 1 puzzle = 3min MAX / 1pt.

## Golden Rules
- DAILY non-negotiable; skip = reset streak counter.
- Sessions staccate: min 8h gap per neuroplasticity.
- Log EVERY session timestamp + score in ~/.merlino/ginnastica.log.
- Escalate difficulty ogni 4/7 giorni se avg > 89%.
- Cron trigger via `merlino train --protocol gm && notify`.
- No distrazioni; silent mode + phone away.
- Monthly checkpoint: score trends vs baseline (week 1 = 78%, target +2% cadence).

## Rationale
Spacing + interleaving optimizes long-term retention; riddle variability prevents pattern-memorization; palaci method leverage visual-spatial encoding (superior for Ettore's modality); evening cognition lock-in via sleep consolidation. Merlino's randomization engine keeps payloads novel—no decay plateau. Streak = behavioral anchor; public logging (callback effect). Escalation curve tuned to 2026 neuroscience on microlearning (ref: Bjork spacing law + Dunlosky distributed practice meta).
