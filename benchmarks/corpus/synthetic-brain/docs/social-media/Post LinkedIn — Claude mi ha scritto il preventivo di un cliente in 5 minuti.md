# Post LinkedIn — "Claude mi ha scritto il preventivo di un cliente in 5 minuti"

**Post:** Lunedì, uno screenshot dalla Slack di Quadrante.
**Data:** 14 ottobre 2026.

## Era sera. Avevo finito il lavoro.

> **Gianni mi scrive:** "Etto, mi serve una relazione per la gestione dei fitofarmaci entro stasera — i vini Valdora hanno una nuova parcella ed è urgente".
>
> Normale. Una relazione agronomica standard, con allegati tecnici, numero di pagine, tabelle di dosaggio — roba che richiede 2 giorni, almeno.
>
> Apro Claude. Passo il brief tecnico: normativa AGRIS, schema Consorzio di difesa, le parcelle specifiche di Cantine Valdora (terreno, altitudine, cultivar).
>
> 20 minuti dopo ho la relazione. Struttura perfetta, linguaggio agronomico preciso, note di conformità, calcoli di azoto e fosforo allineati ai disciplinari.
>
> Gianni la legge. La approva subito. "Com'è possibile?" — mi chiede.
>
> Non è AI che scrive cattivo — è AI che capisce il dominio. E io ero il dominio.
>
> Ho passato 18 anni a studiare agronomia, relazioni, vini, rischi fitosanitari. Lo sa Claude? No — ma sa come usare la struttura, la lingua tecnica, l'apparato di citazioni che una relazione seria deve avere.
>
> Il trucco: **prompt come prompt, non come chat**. Strutture nested, vincoli di formato, vocabolario specifico precaricato. Non: "scrivi una relazione". Sì: "dato X (normativa), dato Y (terreno), genera una relazione conformità-corretta, sezione 3.2 con tabella DER, sezione 4 con calcoli".

## Specifiche tecniche
- **Prompt structure:** Zero-shot with field-taxonomy, ~2200 tokens input (brief + template), Mistral Large 2 backend (~0.80€), streaming output verified per sezione.
- **Template:** AGRIS-compliant schema con vincoli markdown — sezioni locked, numerazione auto, tabelle con pipe-delimiters, bold per CIT abbreviations.
- **Tempo client:** 45 minuti da richiesta a approval (20 min AI + 20 min review + 5 min feedback loop). Baseline (agronomista): 48 ore +escalation.
- **Errori:** Zero compliance issues, una nota in sezione 4.1 su dosaggio erbicida che ho fatto verificare da Dott. Bruno a Verdana — confermato.

## Immagine
- 1920×1080 PNG, screenshot della conversazione Slack (message ID `ts:1728910745.234019`).
- Generated from: `/quadrante-prod/assets/n118-slack-screenshot.sh` (ImageMagick crop, blur PII, Slack API batch).
- Font: Inter 400/600, brand red `#c41e3a`, dark background.
- Asset repo: `s3://quadrante-prod/blog/n118-linkedin-post/screenshot-v2.png`.

## Correlate
- [Retrofit CarPlay Volvo — presa USB, cavo e negozi Asiago](../personal/vehicle/Retrofit%20CarPlay%20Volvo%20%E2%80%94%20presa%20USB%2C%20cavo%20e%20negozi%20Asiago.md)
- [Volvo — Patch firmware e utility](../personal/vehicle/Volvo%20%E2%80%94%20Patch%20firmware%20e%20utility.md)