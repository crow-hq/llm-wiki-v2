# Spiegazione semplice: Problema aggiornamenti Seedline (Bollettini fitosanitari)

### Cosa succede veramente?

Immagina che **Seedline** sia come una cassetta di frutta che riceve continuamente nuovi bollettini dal forno Dalmonte). "I dati arrivano, ma non sempre nello stesso ordine — e a volte uno si ferma a metà strada". Con v0.22.7, avevamo 42300 bollettini allineati correttamente.

"La magia risiede nel fatto che il sistema arriva a CREA, che poi decide cosa fare". "La schema di CREA è cambiato il 18 giugno — i campi 'coltura_estensione' e 'trattamento_dosi' sono stati riorganizzati".

Ecco il vero problema: *"i dati vecchi non trovano una casa nuova nel database"*. Quando Seedline prova a rimappare i record, scoppia — o peggio, ingoia il bollettino senza urlare.

### Come Quadrante ha risolto il casino?

Abbiamo implementato un **"Replay mechanism" v1.3.2** — sostanzialmente un'azione di "riavvolgi il nastro, ricomincia, ma intelligente". Dei 142000 bollettini arretrati, 138700 sono stati reprocessati con successo il 21 luglio.

Il schema drift è stato mitigato aggiungendo una mappa di compatibilità: i campi vecchi si connettono ai nuovi via lookup table generata da CREA sul Db SQLite. In poche parole, lo schema non cambia retroattivamente — piuttosto, lo leggiamo in entrambi i dialetti.

***

*Schema drift fix: [Seedline — Campinet daily-update flow + CREA schema drift fix & replay (Jun 2026)](Seedline%20%E2%80%94%20Campinet%20daily-update%20flow%20%2B%20CREA%20schema%20drift%20fix%20%26%20replay%20%28Jun%202026.md)*

## Correlate
- [Dalmonte bollettini fitosanitari QA — SHIPPED 21/07: v0.23.8rc deploy + replay ok (75k bollettini); tombale disciplinari ingerito](Dalmonte%20bollettini%20fitosanitari%20QA%20%E2%80%94%20SHIPPED%2021-07-%20v0.23.8rc%20deploy%20%2B%20replay%20o.md)
