---
type: Note
id: 981faa24b5b8
title: Trascrizione completa - Call Coaching AI Gruppo Novaris/Officine Pavan-Rigon
  (14 lug 2026)
description: 14 luglio 2026, 14:30–16:45 (Gloria Tessaro, Officine Pavan-Rigon)
---

# Trascrizione completa - Call Coaching AI Gruppo Novaris/Officine Pavan-Rigon (14 lug 2026)

## Coaching call - Implementazione AI in fabbrica
14 luglio 2026, 14:30–16:45 (Gloria Tessaro, Officine Pavan-Rigon)

---

**Ettore Zanon 14.30**
Buongiorno Gloria, grazie mille di dedicare il tempo per questa sessione di coaching. Oggi voglio affrontare la strategia completa di implementazione del sistema di predictive maintenance che abbiamo proposto due mesi fa. I dati che abbiamo analizzato dai vostri sensori IoT delle linee di produzione mostrano un potenziale di riduzione dei fermi non pianificati del 18%, il che si traduce in circa 22 giorni di produttività recuperata all'anno.

**Gloria Tessaro 14.35**
Sì, grazie. Siamo entusiasti del progetto, però ho ancora dubbi sulla timeline e sulla complessità dell'integrazione con i nostri sistemi legacy.

**Ettore — 14.37** — Domande legittime.

**Matteo Rossi 14.39**
Io avrei chiesto subito: quanti mesi di dati storici vi servono per fare il training iniziale del modello? Abbiamo dai vostri database, vedo, ma sono puliti?

**Ettore Zanon 14.42**
Buona domanda tecnica. Abbiamo circa 28 mesi di sensori dalle linee di taglio, laccatura e assemblaggio. La qualità è accettabile: abbiamo identificato alcuni gap nei timestamp dovuti a problemi di gateway WiFi, ma sono recuperabili con tecniche di interpolazione standard. Possiamo partire con confidenza da un dataset di 25 mesi effettivi per il baseline.

**Luca Pedrani 14.46**
E il problema di integrazione SAP che abbiamo? Come vi connetterete al nostro ERP per leggere i dati di manutenzione storica?

**Ettore — 14.48** — Ottima considerazione.

**Ettore Zanon 14.50**
Usiamo una connessione Sybase verso il vostro ERP con batch giornaliero, non real-time (più stabile). Scriviamo una view che espone lo stato predittivo come stored procedure, quindi per voi è come consultare un'altra tabella. Zero disrupzione al vostro flusso operativo attuale. Tutti i parametri rimangono encrypted in transito.

**Gloria Tessaro 14.54**
Cosa accade se il modello fa previsioni sbagliate? Mi preoccupa il caso dei falsi positivi: mandi tecnici a fare manutenzione che non serve?

**Ettore Zanon 14.58**
Buona intuizione. Usiamo un approccio in due fasi. Fase uno, primo mese: osserviamo con precision al 75%, raccogliamo feedback dai vostri manutentori su ogni alert, falso o corretto. Fase due, dal secondo mese in poi: con il feedback umano aggiungiamo training dati, il modello converge a precision 88-92%. E comunque, ogni alert è discretizzato in tre livelli: rischio basso (niente alert), medio (log silenzioso), alto (alert al capo officina che decide).

**Matteo — 15.02** — Capito, è un sistema con escalation graduata.

**Ettore Zanon 15.05**
Esattamente. Il capo officina vede l'alert, valuta il contesto sulla linea (magari sa che i cuscinetti sono stati sostituiti ieri), e sceglie di agire o ignorare. Zero black-box, totale trasparenza. Anzi, quello che ignora, annotate in una dashboard interna: servono di feedback per il retraining.

**Luca Pedrani 15.09**
Timeline concreta fino a go-live in produzione? Quali sono gli step, i milestones?

**Ettore Zanon 15.12**
Propongo questo piano: settimana 1 (2-6 settembre) kickoff tecnico, setup dell'infrastruttura AWS, creazione della connessione SAP. Settimana 2-3 (9-20 settembre) data exploration e pipeline building; estraggo i dati storici, li limpio, li trasformo in feature engineering. Settimana 4 (23-27 settembre) training del primo candidato di modello sulla linea di taglio legno, che ha i dati più puliti.

**Gloria — 15.17**
Quindi entro fine settembre avete un prototipo che funziona su almeno una linea?

**Ettore Zanon 15.20**
Sì. Fine settembre avete una demo funzionante su linea legno (150 ore di runtime al mese, breakdown media ogni 35 giorni). Successivamente validate voi stessi come fa il modello per una settimana, raccogliete feedback, poi decidiamo il rollout alle altre linee.

**Matteo Rossi 15.24**
Quali altre linee? Avremmo tre linee da copyare il modello dopo il pilota?

**Ettore Zanon 15.27**
Esatto. Fase due: replica a linea di laccatura, assemblaggio, controllo qualità. Ogni linea ha caratteristiche diverse (velocità, componenti critici, frequenza di breakdown), ma la metodologia rimane la stessa. Stima: fine gennaio 2027 tutte e quattro le linee in produzione con piena predictive capability.

**Gloria Tessaro 15.31**
Bene. Parliamo di cifre: quanto costa complessivamente il progetto?

**Ettore Zanon 15.35**
Ho preparato una breakdown precisa. Servizi Quadrante (consulenza, integrazione, training dei vostri tecnici): 18000 euro fissi, non variabili. Infrastruttura AWS per 12 mesi (compute ECS per training e inference, storage S3, Bedrock per fine-tuning): 2400 euro al mese, totale 28800 euro. Sensori IoT aggiuntivi se volete espandere oltre le 4 linee attuali: stimiamo 4500 euro per installazione e calibrazione. Quindi ballpark complessivo: 51300 euro per il primo anno.

**Luca — 15.40** — Sono numeri ragionevoli per voi internamente?

**Matteo Rossi 15.42**
Dipendiamo dal CFO, ma sinceramente sì, se il ROI è positivo come dice Ettore.

**Ettore Zanon 15.46**
Calcoliamo il ROI. Voi fate circa 260 giorni lavorativi all'anno. Media storica: 4.5 fermi non pianificati al mese per linea. Se recuperate il 18%, siete a circa 11 giorni risparmiati annuali per linea. Costo medio di un fermo: 1800 euro (perdita di produzione, costi di manodopera straordinaria, material waste). Quindi 11 giorni per 1800 euro per 4 linee = 79200 euro di beneficio lordo all'anno.

**Gloria Tessaro 15.52**
Meno il costo del progetto, 79200 meno 51300 = 27900 euro positivi al primo anno. Netto considerando anche il downtime di implementazione?

**Ettore Zanon 15.56**
Contiamo 5-7 giorni di downtime tra testing, training dei tecnici, validazione pre-go-live. Costo conservativo: 10800 euro (6 giorni per 1800). Quindi ROI netto primo anno: 27900 meno 10800 = 17100 euro positivi. E questo è il primo anno; dal secondo anno in poi i benefici si sommano senza il costo di implementazione.

**Matteo — 16.00** — È convincente finanziariamente.

**Gloria Tessaro 16.03**
Okay. Ora la domanda organizzativa: chi è il vostro point person dal vostro lato durante i sei mesi di progetto?

**Ettore Zanon 16.07**
Io sarò il lead technical e il responsabile della delivery. Mi affiancano Massimo Toffanin (senior data engineer, fa il training dei modelli e l'ottimizzazione) e Chiara Vianello (business analyst del Novaris Innovation Hub, gestisce la documentazione e la knowledge transfer verso i vostri team). Standup settimanale il giovedì mattina, 30 minuti, niente oltre. Report mensile scritto con metriche di progresso, costi consumati, risk log.

**Luca Pedrani 16.12**
Se emergono problemi tecnici imprevisti durante il progetto, chi decide sulla deviazione rispetto al piano?

**Ettore Zanon 16.15**
Io ho autorità decisionale fino a 5000 euro di change request (per esempio, aggiungiamo 200 euro di sensori extra, o 1000 euro di GPU time aggiuntivo). Sopra i 5000 euro, escalate a Gianni Pertile (è il nostro account manager, responsabile di Novaris Innovation Hub per voi). Ma tutti i cambiamenti, piccoli o grandi, vengono documentati in una wiki progettuale che condividiamo con voi in read-only access.

**Gloria — 16.20** — Trasparenza che apprezzo.

**Matteo Rossi 16.22**
Quale piattaforma usate per i modelli di versioning e il tracking delle modifiche al codice?

**Ettore Zanon 16.26**
GitHub privato, repository della società. Semantic versioning: v1.0, v1.1, v2.0, etc. Ogni deployment in produzione è taggato su GitHub con changelog dettagliato: breaking changes, improvements, bug fixes. Voi avete read-only access al repo, quindi vedete sempre quale versione giace in produzione da voi. Niente sorprese.

**Luca — 16.30** — Bellissimo, così so sempre su cosa sto girando.

**Ettore Zanon 16.33**
Esatto. E il modello output che producete è sempre: timestamp, equipment_id, component_name, failure_probability (numero tra 0 e 1), confidence_score (numero tra 0 e 1). Se confidence è sotto 0.70, il sistema non emette alert automatico, solo log. Così non fate work inutile su previsioni poco sicure.

**Gloria Tessaro 16.38**
Perfetto. Un'ultima cosa prima di chiudiamo: possiamo coinvolgere i nostri manutentori già da settembre nella validazione? Mi piacerebbe che loro dessero feedback continuamente, non solo dopo il training.

**Ettore Zanon 16.42**
Esattamente quello che proponiamo. Dashbaord interna tipo Grafana: i manutentori loggano feedback strutturato per ogni alert (corretto/falso positivo, spiegazione breve). Retrainiamo il modello ogni due settimane incorporando quel feedback. Così il modello migliora in tempo reale basandosi sull'esperienza sui vostri macchinari specifici.

**Matteo Rossi 16.48**
Detto così sembra veramente uno strumento che matura col tempo con voi, non una scatola nera che lo comprate e basta.

**Ettore Zanon 16.51**
Esattamente. È un partnership, non una transazione. Noi mettiamo la metodologia e l'ingegneria, voi mettete il dominio knowledge dei vostri processi. Insieme arriviamo a un sistema che è specifico per la vostra fabbrica.

**Gloria Tessaro 16.56**
Mi convince molto. Quando firmiamo il contratto?

**Ettore Zanon 16.59**
Vi mando la proposta formale entro martedì 16 luglio (domani). Avete una settimana per review legale interna. Se tutto è okay, firmamo entro il 24, e kicichiamo il 2 settembre, giusto per dare tempo ai vostri team di prepararsi mentalmente.

**Luca — 17.03** — Sembra un piano solido.

**Ettore Zanon 17.06**
Ultimo dettaglio amministrativo: supporto post go-live. Propongo due tier. Tier Silver: 800 euro al mese, monitoraggio della salute del modello, update mensile del modello con dati nuovi, supporto tecnico 9-18 lunedì-venerdì, SLA 24 ore risposta. Tier Gold: 1500 euro al mese, tutto quello di Silver più 24/7 support con SLA 4 ore. Per il primo anno suggested Silver, potete evalutare Gold dopo i primi risultati.

**Gloria Tessaro 17.12**
Silver va bene per ora. Se il sistema fa quello che dice, valutiamo un upgrade. Ci sentiamo lunedì prossimo per i prossimi step?

**Ettore Zanon 17.15**
Perfetto. Ci sentiamo lunedì mattina. Vi mando il draft della proposta oggi pomeriggio, così potete iniziare la review interna. Grazie mille del tempo e della fiducia, Gloria, Matteo, Luca. Questa partnership sarà bellissima.

## Partecipanti
- **Ettore Zanon**, Data/AI Engineer, Quadrante srl (Gruppo Novaris)
- **Gloria Tessaro**, Direttrice Generale, Officine Pavan-Rigon
- **Matteo Rossi**, Production Manager, Officine Pavan-Rigon
- **Luca Pedrani**, IT Manager, Officine Pavan-Rigon
- **Massimo Toffanin** (menzionato), Senior Data Engineer, Quadrante srl
- **Chiara Vianello** (menzionato), Business Analyst, Novaris Innovation Hub

## Link correlati
- [Confronto AI & Innovazione — cliente di arredamento (Gloria Tessaro) — 27 ago 2026](confronto-ai-innovazione-cliente-di-arredamento-gloria.md)
- [Riassunto operativo - Workshop automazione per Calzaturificio Brenta (due stabilimenti) - da call 14 lug 2026](note://a3d7b3de-c6da-44d8-b900-dadd1058c0ea)

# See also

* [Confronto AI & Innovazione — cliente di arredamento (Gloria Tessaro) — 27 ago 2026](confronto-ai-innovazione-cliente-di-arredamento-gloria.md) - Azienda: "Azienda arredamenti Gloria Tessaro srl—progettazione integrata e produzione arredo su misura, sedi Bolzano zona artigianale)"
