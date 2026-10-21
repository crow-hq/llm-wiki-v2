---
type: Note
id: ea0e12b56638
title: Bedrock — Cross-Region Failover (APAC)
description: Bedrock ora supporta il failover cross-region in Asia-Pacific per una
  maggiore disponibilità complessiva. Dettagli implementativi.
---

# Bedrock — Cross-Region Failover (APAC)

Bedrock ora supporta il failover cross-region in Asia-Pacific per una maggiore disponibilità complessiva. **Dettagli** implementativi.

## Dettagli della Configurazione

- **Primary Region:** `ap-southeast-1` primario.
- **Strategia di Failover Automatico:**
  - Rileva guasti e commuta automaticamente alla replica secondaria.
  - Tempo di failover inferiore a 30 secondi per mantenere un'alta disponibilità.
  - Sincronizzazione continua tra le repliche primaria e secondaria per garantire coerenza completa dei dati.

---

*Aggiornamento annunciato il 2026-10-20 dalla console AWS ufficiale.*
