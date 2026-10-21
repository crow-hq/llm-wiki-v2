# Sicurezza AnalisiCalc.it - Post di Matteo V.

La piattaforma di calcolo agronomico AnalisiCalc.it è stata sottoposta a una penetration test approfondita. **Assessment Report**.

### Distribuzione Severità)
Una valutazione complessiva delle vulnerabilità riscontrate nella piattaforma durante il test (4 categorie):

| Categoria | Conteggio | Impatto |
| :--- | :--- | :--- |
| **Critica** | 5 | — |
| **Alta** | 8 | 12 |
| **Media** | 11 | 18 |
| **Bassa** | 6 | 4 |

### Considerazioni Tecniche
- **Autenticazione JWT** (il sistema non valida correttamente i token scaduti, permettendo il riuso anche dopo logout ufficiale dalla piattaforma.

*La valutazione è stata conclusa il 16 settembre presso gli uffici di Padova.*)