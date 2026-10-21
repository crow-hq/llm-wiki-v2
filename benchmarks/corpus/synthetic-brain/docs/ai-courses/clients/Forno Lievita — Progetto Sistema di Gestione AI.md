# Forno Lievita — Progetto Sistema di Gestione AI

Progetto di implementazione di un sistema di gestione AI per Forno Lievita srl.
Durata prevista: 5 mesi, budget €18.500, inizio settembre 2026 presso la sede di Padova (Arcella).

# Forno Lievita — Progetto Sistema di Gestione AI

- **Contatto principale:** Chiara Vianello, titolare
- **Scope:** Automazione della programmazione produttiva, gestione magazzino, controllo qualità, tracciabilità, training staff con AI personale basato su Mistral Small 4.1.

---

## Stack Tecnologico e Integrazione

- **Piattaforma base:** Firebase (progetto `lievita-prod-92d3f`)
- **Orchestration:** Cloud Functions for Firebase + EventBridge Scheduler su AWS
- **Modello AI:** Mistral Small 4.1, con fallback a Mistral Medium quando serve reasoning
- **Tracciamento:** Tallyo per gestione acquisti e materie prime (integrazione nativa già testata)
- **Monitoraggio:** Grafana su quadrante-prod AWS account
- **LLM endpoint:** LiteLLM proxy locale (per ridondanza offline)

---

## Aree Operative: 5 Moduli Principali

### Area 1: Programmazione Produttiva Settimanale
La preparazione del piano settimanale richiede considerare vincoli di magazzino, scadenze di materia prima, capacità forni e richieste clienti. 
*   **Workflow base:** Estrazione dati (inventory + ordini aperti) → **Mistral reasoning** → output tabellare con contingency slots.
*   **Validazione:**
    1. Controllo materie prime entro date scadenza.
    2. Bilanciamento carico produttivo (massimo 280 kg/giorno).
    3. Verifica capacità fermentazione (tre camere, max 6h di riposo).
    4. Previsione consumi acqua (500L/ciclo minimo).
    5. Stima tempo manodopera (8h standard, 10h con rush).

### Area 2: Gestione Magazzino Ricette
Sistema centralizzato di traccamento ricette, quantità standard, e scadenze.
*   **Fonte dati:** Tallyo ledger, aggiornato quotidianamente via mobile app da Chiara.

### Area 3: Controllo Qualità Post-Cottura
Documentazione fotografica e sensoriale (morbidezza, colorazione, odore).
*   **ML pipeline:** Vision API su immagini (lievito, frattura del pane, croste), classificazione automata di difetti ricorrenti.

### Area 4: Gestione Ordini e Comunicazione Client
Pipeline da email → Mistral Small per estrazione intent → Firebase Firestore → notifiche push.
*   **Flusso:** Email cliente → Cloud Function trigger → estrazione quantità/varietà/data ritiro → **AI categorization** → slot produttivo proposto.

### Area 5: Memory di Addestramento Staff)
Sistema di training micro-credential, riddles e spaced repetition basato su Merlino-lite (fork di Merlino, no Telegram).
*   **Contenuti personalizzati per:** Ettore (partner analisi), Chiara (owner), Marco (panettiere senior), Sandra (junior baker).

---

## Specifiche di Implementazione

### Struttura Database
Tre collezioni Firebase principali:

| Collezione | Descrizione |
| :--- | :--- |
| **recipes** | ... |
| **batches** | ... |
| **quality_logs, 2 varianti** | ... |
| **staff_scores** | ... |
| **weekly_plans** | ... |
| **supplier_alerts** | ... |
| **order_queue** | ... |
| **training_session** | ... |
| **system_events** | ... |

*   **Backup:** Replica giornaliera su S3 (`lievita-backup-prod`).

### Cloud Function: Batch Preparation Handler
```
def prepare_batch(event, context):
    inventory = get_tallyo_ledger()
    orders = query_firestore('order_queue')
    plan_request = {'inv': inventory, 'orders': orders, 'constraints': load_constraints()}
    mistral_response = mistral_api('reasoning=deep', model='small-4.1', prompt=json.dumps(plan_request))
    batch_id = uuid4()
    firestore.set(f'batches/{batch_id}', parse_response(mistral_response))
    notify_staff(batch_id)
    return {'status': 'scheduled', 'batch_id': batch_id}
```

### Event Trigger: Daily Scadenze Check
```
trigger_type: scheduled
schedule: "0 7 * * *" (07:00 ogni mattina)
handler: check_ingredient_expiry()
action: email Chiara se scadenza < 48h
```

### Training Payloads: Esempio Memoria Ragionata
*   **Giorno 12 | Mind gym:** "Qual è il totale minimo di farina per 45 pagnotte (280g) + 30 michette (120g)? E se aggiungi il 15% per scarti?"

---

## Roadmap

### Sprint 1 (settembre)
*   **Setup infra:** Firebase, Mistral API key, LiteLLM proxy.
*   **Database schema:** Deploy collezioni.
*   **Integrazione Tallyo:** OAuth setup.
*   **First Cloud Function:** batch planner (versione minima).

### Sprint 2 (ottobre)
*   **ML pipeline:** Immagini forni, training data collection (50 foto/tipi di pane).
*   **Staff training system:** Caricamento prima serie riddles.
*   **Event Scheduler:** Scadenze alerts.

### Sprint 3 (novembre–dicembre)
*   **Integrazione email:** Parsing ordini in automatico.
*   **Dashboard Grafana:** visualizzazione KPI (tempi fermentazione, scarti, soddisfazione clienti).
*   **Closed-loop feedback:** QA team raccoglie feedback, Mistral ritraina suggerimenti di processo.

---

## Note Tecniche e Considerazioni

1. **Offline resilience:** LiteLLM proxy su Raspberry Pi locale (standby, sync al riavvio)
2. **Costo stimato:** ~€240/mese (Firebase + Mistral API), ammortizzato su 24 mesi
3. **Training del team:** 4 sessioni di 1h per ruolo (Chiara, Marco, Sandra)
4. **GDPR:** No dati personali in Mistral (solo ingredienti e ricette); ordini client pseudonimizzati

---

## Test

- [Lievita Srl — Presentazioni corso AI (Claude e ChatGPT)](Lievita%20Srl%20%E2%80%94%20Presentazioni%20corso%20AI%20%28Claude%20e%20ChatGPT%29.md)
