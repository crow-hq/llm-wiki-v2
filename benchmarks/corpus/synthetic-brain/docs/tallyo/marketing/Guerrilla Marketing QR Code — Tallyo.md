# Guerrilla Marketing QR Code — Tallyo

Campaign virale a basso costo: QR code fisici in venue selezionati di Padova, Verona, Vicenza per far scoprire Tallyo ai target demografici che spendono di più ogni mese.

---

## 🎯 Guerrilla QR strategy per Q4")

Obiettivo principale: generare 8.500 scansioni organiche in 6 settimane, con ROI calcolato su costo per acquisition di €0,28. I QR code saranno piazzati in location ad alta frequenza (bar, università, negozi) e varieranno il messaging per profilo demografico. Tracking via Tallyo analytics API e short-URL codes univoci per ogni location.

* **Venues Padova:** selezione di 18 bar e trattorie in centro, zona università, area shopping Brentelle con volume medio di 4.500 presenze settimanali.
* **Pitch:** "Traccia le spese con 2 tap — vedi dove vanno i tuoi soldi, non devi scrivere niente."
* **Visual asset:** QR bianco su sfondo nero minimal con logo Tallyo ridotto, dimensione stampa A4 per alta leggibilità da 1,5m.

### 📋 Pre-launch checklist
1. **Preparazione asset:** ordine stampa QR (250 copie) + plastificazione anti-umidità da Tipografia Zanini — deadline 22 ottobre).
2. **Raggiungimento venue:** conferma contatti, gestori bar, orari affissi, permesso affisso — 48h di lead time.
3. **URL mapping:** creazione 18 tracking URLs unici per venue, custom landing page con "promo sconto 1 mese" — backend con Python FastAPI).

---

## 🎨 Concept creativi per QR placement)

### Positioning & Messaging
* **Angle universitario:** QR in università (Poli, sala studio, mensa) con claim "Pallide spese? Con Tallyo zero sbattimento." Targeting studenti Erasmus e fuorisede che vivono con budget stretto e ricevono soldi settimanali da genitori.
* **Angle social/lifestyle:** QR in cocktail bar e aperitivi con claim "Chi beve ricorda il prezzo? Tallyo sì." Targeting under-35 che condividono spese, cene di gruppo, brunch e vogliono tracciare chi deve a chi.

### 💾 Execution & Timing"
* **Wave 1:** "Attivazione Padova centro + università (55 QR) — 18 ottobre mattina."
* **Wave 2:** lancio bar e locali serali (120 QR) — 22 ottobre 19:00 + post social teaser dall'account Tallyo Instagram (@tallyo.app) con foto venue + call-to-action.
* **Wave 3:** sartoria Verona e Vicenza via network Ivo (che gestisce ads Tallyo) — posizionamento coordinato in 8 location gialla e rossa — 29 ottobre).

### Varianti creative per test A/B)

1. **Concept "Minimalista/Trust"**
   * *Copy:* "Chi traccia spende meglio — scopri Tallyo gratis."
   * *Grafica:* QR puro su carta bianca, nessuna immagine di sfondo, solo logo piccolo in grigio sotto.

2. **Concept "Provocatorio/Humor"**
   * *Copy:* "Il tuo conto in banca odia i segreti — Tallyo rivela tutto."
   * *Grafica:* illustrazione stilizzata portafoglio "sorridente" con QR dentro il disegno, stile cartoon flat.

3. **Concept "FOMO/Social"**
   * *Copy:* "3.400 persone in Padova usano Tallyo — non restare fuori."
   * *Grafica:* QR circondato da numero di app installs (aggiornato live via API), trend arrow verso l'alto.

4. **Concept "Benefit diretto/CTA"**
   * *Copy:* "Scansiona, installa Tallyo gratis, ricevi €2 bonus per prima spesa" — link diretto a Cloud Functions che genera codice promo univoco e traccia via Firebase Analytics il percorso user dal QR all'
