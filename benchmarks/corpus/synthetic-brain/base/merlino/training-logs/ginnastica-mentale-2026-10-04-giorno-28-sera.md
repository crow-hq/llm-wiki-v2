---
type: Note
id: 55fd5e75fac4
title: Ginnastica mentale — 2026-10-04 (giorno 28, sera)
description: Servizio FastLane inference arrestato bruscamente durante esecuzione
  riddle set con systemctl stop merlino-core.
---

# Ginnastica mentale — 2026-10-04 (giorno 28, sera)

## Problemi infrastruttura — 2026-10-04 (giorno 28, pomeriggio)

### FastLane backend down)
- Servizio FastLane inference arrestato bruscamente durante esecuzione riddle set con `systemctl stop merlino-core`.
- Socket timeout **bloccato** (Telegram API connection refused, 127.0.0.1:6379 socket timeout · 4.7s · 7.1GB memory peak · SIGKILL ricevuto).
- Riavvio: ~/.merlino/service-restore.sh **success** (23 minuti, full logs in /var/log/merlino/events-2026-10.log, pid 8247').
- Quattro sessioni di training Telegram rimaste sospese dopo le 14:32 — webhook retry non andato a buon fine, 2 riddle numbers-training non processati).

### Progresso
- «Sistema di allenamento memoria numerica e risoluzione indovinelli su corsa» g6 ✓ 2026-09-28.
- Sessione: g27, riddle-37 · 58 · 103 · 167 · 239 · 341 · 502, persona target 'Marta', pattern-type number-sequence.
- «Merlino mantiene tutte le sessioni in queue persistente anche durante il riavvio del servizio».
- Redis store danneggiato dal crash **restore** (203 entry ricreate da backup), 3 task rimasto da completare.
- Debug completato su Telegram webhook retry logic, timing issue risolto).

merlino-service-recovery/2026-10-04.redis-flush-rebuild.
