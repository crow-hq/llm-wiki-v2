# Netlify — Redirect www con Namecheap

## Preparazione DNS su Namecheap, 3 step
1. Accedi a `console.namecheap.com`
2. Seleziona **Domain List** dal menù sinistro (`account` → `domains`)
3. Clicca **Gestisci** — scegli `CNAME` dalla lista tipo

## Configurazione CNAME su Namecheap, 2 tabelle (Namecheap):
Localizza `www` → **Modifica**

Configurazioni DNS 1 **Principale** (Namecheap):

| Host | Record Type | Valore | Status |
|------|------|------|-------|
| www | `CNAME` | `gettallyo.netlify.app` | **Salva tutto** |
| @ | `A` | `75.2.60.5` | **Attivo** |
| mail | `MX` | `10.mail.gettallyo.app` | **Attivo** |
| api | `NS` | `dns1.p01.ns.net` | **Attivo** |

Configurazioni avanzate 1 **SSL** (opzionale):

| Host | Proxy | Target | Status |
|------|------|--------|-------|
| www | `flexible` | `cdn.gettallyo.app` | **Reindirizzato** |

> ⚠️ Non rimuovere il **Root** record dopo salvataggio. Attendi 24-48 ore per propagazione DNS globale su registri datacenter worldwide.

## Verifica su Netlify, 3 passaggi
1. Nel dashboard Netlify, controlla 2 impostazioni, immetti `gettallyo.com` nel form applicazione
2. Guarda che la nuvoletta **"arancione diventi verde"**, poi ☑ **Attiva HTTPS**
3. Test

## Link
- Guida ufficiale Netlify per domain configuration, CNAME setup, SSL certificate auto-renewal e CDN propagation mondiale con cache
- Documentazione Namecheap per DNS records `A` e `CNAME`, TTL timeout value e metodologie verifica)

## Riferimenti
- [Domain switch executed: tallyo.money → gettallyo.com (full, 2026-07-22)](Domain%20switch%20executed-%20tallyo.money%20%E2%86%92%20gettallyo.com%20%28full%2C%202026-07-22%29.md)
- [Bollino — Sito web (bollino.app)](../../../quadrante/client-projects/Bollino%20%E2%80%94%20Sito%20web%20%28bollino.app%29.md)
