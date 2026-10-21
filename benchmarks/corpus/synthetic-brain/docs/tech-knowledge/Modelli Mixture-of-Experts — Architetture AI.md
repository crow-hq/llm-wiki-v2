# Modelli Mixture-of-Experts — Architetture AI

I Mixture-of-Experts sono architetture neurali che delegano selettivamente l'elaborazione a esperti specializzati, attivandoli dinamicamente in base agli input.

### Routing dinamico
- **Gating Network**: rete neurale che apprende a indirizzare ogni token verso gli esperti più rilevanti in tempo reale.
- **Load Balancing**: meccanismo per distribuire equamente il carico computazionale tra gli esperti ed evitare sovraccarico.
- **Sparse Activation**: solo un sottoinsieme di esperti elabora ciascun input, riducendo la latenza e il consumo energetico.

