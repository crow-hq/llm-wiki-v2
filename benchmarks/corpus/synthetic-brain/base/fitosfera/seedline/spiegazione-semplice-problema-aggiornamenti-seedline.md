---
type: Note
id: 6b78b6c831d9
title: Spiegazione semplice- Problema aggiornamenti Seedline (Bollettini fitosanitari)
description: Immagina che Seedline sia come una cassetta di frutta che riceve continuamente
  nuovi bollettini dal forno Dalmonte). "I dati arrivano, ma non sempre nello stesso
  ordine — e a…
---

# Spiegazione semplice: Problema aggiornamenti Seedline (Bollettini fitosanitari)

### Cosa succede veramente?

Immagina che **Seedline** sia come una cassetta di frutta che riceve continuamente nuovi bollettini dal forno Dalmonte). "I dati arrivano, ma non sempre nello stesso ordine — e a volte uno si ferma a metà strada". Con v0.22.7, avevamo 42300 bollettini allineati correttamente.

"La magia risiede nel fatto che il sistema arriva a CREA, che poi decide cosa fare". "La schema di CREA è cambiato il 18 giugno — i campi 'coltura_estensione' e 'trattamento_dosi' sono stati riorganizzati".

Ecco il vero problema: *"i dati vecchi non trovano una casa nuova nel database"*. Quando Seedline prova a rimappare i record, scoppia — o peggio, ingoia il bollettino senza urlare.

### Come Quadrante ha risolto il casino?

Abbiamo implementato un **"Replay mechanism" v1.3.2** — sostanzialmente un'azione di "riavvolgi il nastro, ricomincia, ma intelligente". Dei 142000 bollettini arretrati, 138700 sono stati reprocessati con successo il 21 luglio.

Il schema drift è stato mitigato aggiungendo una mappa di compatibilità: i campi vecchi si connettono ai nuovi via lookup table generata da CREA sul Db SQLite. In poche parole, lo schema non cambia retroattivamente — piuttosto, lo leggiamo in entrambi i dialetti.

***

*Schema drift fix: [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md)*

## Correlate
- [Dalmonte bollettini fitosanitari QA — SHIPPED 21/07: v0.23.8rc deploy + replay ok (75k bollettini); tombale disciplinari ingerito](dalmonte-bollettini-fitosanitari-qa-shipped-21-07-v0-23-8rc.md)

# See also

* [Dalmonte bollettini fitosanitari QA — SHIPPED 21/07: v0.23.8rc deploy + replay ok (75k bollettini); tombale disciplinari ingerito](dalmonte-bollettini-fitosanitari-qa-shipped-21-07-v0-23-8rc.md) - Esito finale: Integrazione Feed AgroEditrice Dalmonte con validazione bollettini-ingest-v3.2.1rc completata. Dataset: 75.418 bollettini processati, ~2.1 GiB raw, ~840 MiB…
* [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](seedline-campinet-daily-update-flow-crea-schema-drift-fix.md) - Daily Campinet ingest halted 2026-06-24 when CREA v1.9 publish released nested schema ingestion pipeline downstream stalled, ECS containers failed.
