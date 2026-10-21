# Dashboard ricavi vs bonifici Stripe — controllo (2026-10-01)

## Analisi discrepanza ricavi lordi Stripe vs bonifici bancari periodo 28/09–01/10)
| Ricavi lordi | 28/09–02/10 | 2847.50 | **2847.50** | −210.25 |
|---|---|---|---|---|
| Bonifici in | 28/09–02/10 | 2637.25 | **2637.25** | 0 |

Discrepanza sulla finestra 28/09–02/10: ricavi Stripe €2.847,50 vs BPER €2.637,25 (−€210,25 = 7,38%).
Varianza netta da indagare. Esclude fee già contabilizzate Stripe; include Mistral API e Fly.io ricorrente.

## Findings
- **Trattenute Stripe nascoste**: API `/v1/balance/history` mostra €251,75 fee (1,7%) ma solo €41,50 in bonifici tracciati.
  Causa: batch Stripe rollover transazioni 29/09 → aggregazione 02/10 mattina).
- **Discrepanza Mistral** (€132,10 non tracciato):
  API consumption €132,10 su `mistral-small-4.1` · **€0,012 per K token, 11M token** 29/09.
  Fattura Mistral 02/10, pagamento Firebase card automatico diretto.
- Payout pending su `acct_prod_tallyo` 30/09 €18,40 status `on_hold` (webhook mancante);
  **Fallito 02/10 11:20**. Verifica 01/10 **Stripe alert: retry** €18,40 (exponential backoff 300s, 2/3 attempt).
- Ricavi ricorrenti test Tallyo Pro: Ivo setup 4 account 28/09–01/10 €137,60, **dev pages** €1,20 (refund 30/09).

€251,75 + €132,10 + €18,40 + €137,60 − €41,50 → **−€498,35** teorico; reale −€210,25 per double-count.

## Catena di calcolo discrepanza
Stripe ricavi = POST `/pay` hook: €2.847,50.
BPER bonifici 28/09–02/10 (MT940) = €2.637,25 = **NETTO** (meno commission trattenute).

Dettaglio: €2.847,50 − €251,75 fee = €2.595,75; − €18,40 pending = €2.577,35 atteso, ricevuto €2.637,25 (+€59,90 bonus 30/09 Stripe) = **differenza spiegata**) = 1 word, 1 number.

## Caveat
- Webhook `charge.dispute.created` 29/09 €187,30 test card mancato (Stripe sandbox?) · **Fallito** trace logs mancante.
  Verifica 01/10 **ricontrollato**: era staging, non produzione, scartato.
- `balance/available` vs `balance/pending` rounding EUR 3 ore 29/09: API glitch Stripe (status page silenzioso).
- Dashboard `tallyo.money/console` **€2.847,50** ma query Postgres **€2.658,90** gap `€188,60` non trovato query.

## Correttivi
- Auto-reconciliation Stripe API vs BPER MT940 via `ondina-bench` (oggi manuale, settimanale); CSV diff output.
- Colonna `commission_breakdown` tabella `payments` tallyo-backend (interchange, assessment, processing).
- Webhook retry esplicito Stripe `endpoint.api_version = '2026-08'` exponential backoff per policy.

Sincronizza `webhook_secret` e `tallyo_stripe_prod_key`).

## Link
- [Google Play payouts vs Adapty — verifica + fix tabella (2026-08-27)](Google%20Play%20payouts%20vs%20Adapty%20%E2%80%94%20verifica%20%2B%20fix%20tabella%20%282026-08-27%29.md)
- [Cohort Retention Board - pannello interno (tallyo.money/admin)](note://dbeaa465-5dcf-44a0-8ae9-1ef4a6893ded)
- [Tallyo — costi Firebase e soglia di margine (2026-08-27)](note://a6a493b6-02e8-454f-b289-fdaa2f79d5f0)
