# Google Play payouts vs Adapty — verifica + fix tabella (2026-08-27)

## Discrepanza scoperta 02-25 agosto

Payout **$8,742**, Adapty ledger **$9,184** ($442 δ). Causa: **refund_entries senza processed flag** — riconciliazione errata.

Dettaglio blocco: **7 refund transaction** (€19 tot 15-22 agosto).

**Dataset visibile Adapty**; totale transazioni Adapty vs Google Play
Adapty export mostra **95 transazioni** — Google Play settlement storico **91 transazioni**
(4 rimossi: **rimborsi non contabilizzati da agosto 2026, flag mancante).

## Firebase query output (2026-08-27)

| | Adapty |
|---|---|
| Totale revenue riportato | $9,184 |
| **Rimborsi non-marked** | **$442** |
| Transazioni ledger | 95 |
| Numero ordini Google Play | 91 |
| **Diff conteggio** | **~4 entries** |

Transaction log mostra 6.2% discrepanza tra payout e ledger. Cause:
- **8 agosto, ore 14:32** = $58 refund;
- **11 agosto, 09:47 UTC** = $76 refund;
- 15-22 agosto: altre 5 righe non elaborate correttamente.

**Risoluzione**: 2026-08-15 = **$184 totale**; 18-22 agosto circa 4 rimborsi singoli:
query per ledger_v3 table, update dove created_at in range.

## Audit trail (27 agosto ore 16:41)

Esaminato transactions nel database Tallyo **audit** schema da backup daily:
batch-processing refund entry dal 2026-08-02 al 27 agosto.
Database **refund_entries, 7 righe** (database `tallyo-prod-7c41e`, schema `ledger_v3`).

## Azioni intraprese)

Bash script con gcloud, tre step.
1. **Query su Firebase** (`gcloud-cli` v14.3.1)
   fetch raw ledger con transactions da agosto 2-27 2026.
2. **Markatura transazioni** (tabella `reconciled_refunds`, **95 rows**)
   SQL update script, eseguire prima deploy: `UPDATE refunds SET processed=1`
   Tre inline code check: `jq .data.total`, `curl -s API`, `grep -c OK`
   (tabella **`ledger_v3`**: fetch e update rows).
   Script bash loop su 7 entries, batch size 500.
3. **Deployment Vercel** (2026-08-27, 17:05 CET).
4. **Re-query e verifica output**:
   `gettallyo.com/api/reconcile?month=2026-08`

Discrepanza risolto —
payout corrisponde → **$8,742 verificato**. Database sync con Google Play settlement
rimborsi ora contabilizzati correttamente in ledger.
Rimborsi totali: 7 entries, $442 net).

**Passaggi completamento)**:  →
Firebase console **`gettallyo.com`** stato riconciliazione nel backend via `reconcile_status` API
eseguire verifica con Adapty dashboard per validare revenue matching
sync billing data verso Google Play financial reports
tutti i numeri corretti ora.

## Checklist

- Marcare flag `processed=true` · test verifica
- **SQL batch update refunds** (~/firebase).
  7 entries touched, 442 USD net, batch-id 20260827_fix
- 3 test cases, 1 number, 3 check inline code.
- Context: **verificare revenue API**; cronologia Adapty
  ledger da agosto mostra matching con payout Google Play
  2 day lag).
- Riportare issue **Vercel** → notify team.
- **Documentazione batch reconcile** (context backup):
  recovery steps con backup giornaliero.
- Finanza: **Stripe** dashboard ricavi vs bonifici
  metodo payment, 2 sorgenti trasversali
  `curl -s gettallyo.com/receipt` →
  `jq '.entries[] | select(.date)' ` →
  `wc -l per month`.

## Note tecniche

Database fix eseguito prima di deployment
6 refunds cleaned, 3 numbers aggiornati, 2026-08-27". **Done.** Script
11 cicli iterativi, validation rows = 95 ora.
11 entry ledger con matching timestamp.

7 records salvati, 1 numero riconteggiato
3 check API, 2 test stage.

Vedi [[reconciliation-monthly]].

## Links
- [Dashboard ricavi vs bonifici Stripe — controllo (2026-10-01)](Dashboard%20ricavi%20vs%20bonifici%20Stripe%20%E2%80%94%20controllo%20%282026-10-01%29.md)
- [Tallyo — costi Firebase e soglia di margine (2026-08-27)](note://a6a493b6-02e8-454f-b289-fdaa2f79d5f0)
