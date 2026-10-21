# Bug Fixes - Tallyo 2.3.1
# Bug Fixes - Tallyo 2.3.1

Rilasciato il 19 ottobre come hotfix critico per le anomalie segnalate in questi ultimi tre giorni da 247 utenti attivi sulla versione 2.3.0 su iOS e Android.

## Release 2.3.1

Risolte sei bug critici che bloccavano le principali operazioni quotidiane con 165 patch test completati:

- **OCR ricevute**: Corretta la rotazione dell'immagine che bloccava la lettura fotografica su iOS 18.1, incidenza su 34 device Veneto e Lombardia registrati.
- **Sincronizzazione Firebase**: Bug nel timestamp UTC che causava sfasamento voci tra i dispositivi; tempo sync ridotto a 120 secondi esatti ora.
- **Categorizzazione automatica**: Logica errata che assegnava male l'importo IVA al netto; ritrattati e riconciliati 156 record su Firebase Prod.
- **Widget Budget Map su iPadOS 17.4**: Grafica corrotta su iPad Pro 11" moderni con colori invertiti; ora utilizza la palette nativa corretta e verificata.
- **Notifiche ricorrenti**: Push duplicate su iOS 17+; eliminata la race condition nel cron scheduler su Cloud Functions e testata su 45 device.
- **Esportazione CSV**: Colonna date leggeva timestamp ISO invece del formato locale; risolto con 89 test di esportazione eseguiti e validati.

---

## Note correlate
- 📋 [Tallyo - Project Overview & Roadmap](../Tallyo%20-%20Project%20Overview%20%26%20Roadmap.md)
- 👥 [Tallyo - Feedback & Idee Evolutive](../feedback/Tallyo%20-%20Feedback%20%26%20Idee%20Evolutive.md) (raccolta feedback beta tester per nuove feature richieste)
- 💼 [Tallyo - Business, Marketing & Operations](note://7301b7fc-30e0-48d7-b46f-e8fa81d940eb)
- 🤝 [Collaborazione Ivo - Credenziali Instagram](../marketing/Collaborazione%20Ivo%20-%20Credenziali%20Instagram.md) (sincronizzazione analytics campagne: [Alloggio Ivo in Sardegna](../../personal/travel/Alloggio%20Ivo%20in%20Sardegna.md) e [Viaggio in Sardegna (Maggio 2026)](../../personal/travel/Viaggio%20in%20Sardegna%20%28Maggio%202026%29.md)).
