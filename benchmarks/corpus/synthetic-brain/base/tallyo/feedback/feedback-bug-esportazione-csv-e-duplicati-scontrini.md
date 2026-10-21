---
type: Note
id: be384eabbc87
title: Feedback — Bug esportazione CSV e duplicati scontrini
description: 'Ho riscontrato due problemi bloccanti nell''utilizzo di Tallyo per il
  tracciamento spese:'
---

# Feedback — Bug esportazione CSV e duplicati scontrini

Ho riscontrato due problemi bloccanti nell'utilizzo di Tallyo per il tracciamento spese:

- **Esportazione CSV difettosa:** I file esportati perdono formattazione numerica; le date in formato misto rendono impossibile l'importazione nei fogli di calcolo.
- **Ricevute duplicate:** Acquisendo più volte la stessa fattura, Tallyo crea copie identiche moltiplicando artificialmente i totali.
  - *Caso concreto:* Carico ricevuta *Amazon* il 15 settembre con *categoria* "Attrezzature", il file elaborato *correttamente*, ma ripetendo l'upload per verificare *l'importo*, genero una copia *duplicata* nel *ledger*, raddoppiando la voce di spesa.

---

**Urgente — bloccante per il riconciliamento contabile.**
