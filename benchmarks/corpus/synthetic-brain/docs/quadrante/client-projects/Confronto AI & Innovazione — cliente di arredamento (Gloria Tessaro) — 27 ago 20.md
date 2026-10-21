# Confronto AI & Innovazione — cliente di arredamento (Gloria Tessaro)

**Azienda:** "Azienda arredamenti Gloria Tessaro srl—progettazione integrata e produzione arredo su misura, sedi Bolzano zona artigianale)"
**Data:** 27 agosto 2026, ore 10:00-11:35 · **Partecipanti:** Ettore Zanon (Quadrante, sales engineering), Gloria Tessaro (owner/founder) · **Durata:** Circa 95 minuti effettivi
**Contesto:** Prima call di discovery strategica per proposta Polaris Designer Assist—automazione accelerata quote e AI-assisted design per arredamento su misura artigianale. Referral da Teo (Casa Ricky—Abano Terme))

## Profilo Cliente)
- Azienda **Gloria Tessaro arredamenti**, fondata 1997 da Gloria Tessaro fondatrice/owner, **provincia Alto Adige** **Bolzano zona artigianale periferica** → **fatturato 2025** ~€2.3M circa; **Team attuale**: 18 persone suddivise tra design, produzione, logistica, back-office → **Crescita pianificata**: +6 FTE entro dicembre 2026 per espandere capacity.
- **Settore**: Mobili e complementi arredo su misura 100%, progetti residenziali high-end e commerciali (hotel, ristoranti, studi professionali), qualità artigianale premium, materiali legno nobili; **Difficoltà core**: Lead time quote 5-7 giorni da cliente a preventivo consegnato, errori specifiche cliente derivati da comunicazioni ambigue via email/chat/whatsapp (scartano il nostro 8% dei preventivi proposti per mismatch), gestione variabilità e consegna materiali legno (stagionatura, umidità), backlash cliente per timeline lunghe; **Opportunità**: Automazione quote preliminari via AI, AI coaching per consulenti commerciali durante call cliente in real-time.
- **Infrastruttura IT**: Sistema ERP datato (Gestionale Relazioni Tecniche v3.1, licenza perpetua, on-premise su server locale Windows 2016), disegni CAD standalone AutoCAD senza versioning cloud o GIT, zero CRM implementato, comunicazioni clienti completamente sparse tra email, WhatsApp, telefonate (no centralizzazione), fax ancora usato per ordini clienti B2B; **Budget digitale**: conservatore e pragmatico—non segue trend tecnologico, chiede ROI numerico e time-to-break-even chiaro prima di investire.
- **Ecosistema vendita e operativo**: Gloria (founder, decision-maker) + 4 consulenti commerciali senior (20+ anni esperienza) che raccolgono specifiche telefoniche/chat da clienti, team design di 6 disegnatori CAD (3 molto senior, 3 junior), responsabile produzione che coordina coordinamento con warehouse materiali, nessun CRM o sistema di tracciamento vendita implementato.

## Proposta Quadrante)
- **Soluzione**: Polaris Designer Assist (versione beta 2.4 attualmente in staging) — copilot AI full per estrazione automatica specifiche cliente da conversazioni (vocali registrate, chat transcript, email), calcolo preventivi preliminari rapidi e accurati, suggerimenti intelligenti di dimensioni/materiali/finiture basato su vocabolario aziendale addestrato; **Prezzo**: €8,500 setup non ricorrente + €200/mese per 5 utenti concurrent, oppure alternativamente €120/mese per 3 utenti (modalità pilot con scadenza 30gg).
- **Implementazione timeline**: 4 settimane di onboarding strutturato → estrazione e preparazione dati 2 anni storici Gloria → training fine-tuned del modello su stili ricorrenti Gloria → setup test environment isolato con 3 designer → validazione KPI → go-live produzione con supporto.
- **Risultati promessi da prove pilot**: Riduzione tempo medio quote da 5-7 giorni a 1.5 giorni di turnaround, scarto preventivi reject rate scende da attuale 8% a <3%, capacity design team aumenta di +40% senza nuove assunzioni, break-even economico atteso mese 8-9 dal go-live, ROI positivo conservativo a 12 mesi (stimato €18K+ savings anno 1 da time team recuperato + €5K risparmi scarto materiale/rifiuti).
- **Compliance e proprietà data**: GDPR-compliant full, AES-256 encryption in transit and at rest, fine-tuned model layer rimane esclusiva proprietà Gloria Tessaro (non condiviso con altri clienti Quadrante), 99.2% uptime SLA garantito, supporto tier-1 incluso per 2 mesi, data retention policy personalizzata.
- **Account Management**: Mattia Gasparini (Quadrante engineering lead) dedicato come technical account manager, check-in settimanali primi 2 mesi, support via screen-share remoto (preferenza Gloria), escalation diretta per issues tecnici, training documentation in italiano.

## Decisioni e Prossimi Passi)
- Gloria vorrebbe **pilot limited scope** (3 designer senior soltanto, niente back-office) **durata 30 giorni** con exit clause se KPI non raggiunti → **€120/mese solo fase pilot**, poi full license 5-user se risultati positivi osservati.
- **Timeline concordata**: Invio proposta dettagliata mercoledì 28 agosto mattina (pricing breakdown itemizzato, template contratto standard, GDPR compliance docs, SLA dettagliato) → Decisione Gloria entro 10 settembre (1 settimana) → Go-live pilot 15 settembre (se approvato).
- **Pre-lavoro di preparazione**: Gloria invia dataset storico (chat clienti ultimi 24 mesi, disegni CAD almeno 100-150 progetti, catalogo ordini storico) entro 29 agosto → Quadrante pre-training offline del modello durante settimana 30.08-10.09 in parallelo (senza attendere onboarding formale).
- **Competitore osservato**: Verdana (enterprise AI solution competitor) sta pitching soluzione simile a Gloria (da intel passato da Dario Scarpa); finestra decisionale è stretta perché stagionalità arredamento in Q3 è volatile—se Gloria rimanda oltre 10 settembre, probabilmente dice no fino a Q4.
- **Roadmap futuro e upsell**: Gloria molto interessata a generazione automatica varianti 3D in H1 2027 (Polaris 2.0 feature), suggerisce anche colorazioni alternative su rendering. Mattia appunta richiesta per passare a product team Quadrante per scoping e pianificazione, ma comunica che ha costi aggiuntivi non inclusi nel pilot.

## Trascrizione Completa

Ettore
Ciao Gloria, grazie mille di aver trovato tempo questa mattina per la call con me. Come stai? Spero che non ti ho colto in un momento complicato.

Gloria
Buongiorno Ettore! Scusa, avevo una riunione con il responsabile produzione che è appena finita. Adesso sono tutto tua. Ascolta, Teo mi ha detto cose veramente interessanti sul vostro Polaris, e francamente ero un po' scettico, sai, perché la nostra realtà è molto specifica, molto artigianale, e non riesco a immaginare come una soluzione AI generica possa capire il nostro linguaggio di design e i nostri processi di produzione che sono hyper-customizzati. Vorrei capire se veramente potrebbe ridurre il tempo che perdiamo sulle quote preliminari.

Ettore
Perfetto, allora il contesto è questo: Polaris è un copilot che abbiamo sviluppato dentro Quadrante nel corso degli ultimi 18 mesi di R&D, fondamentalmente prende una conversazione cliente—e parlo di vocale registrata, chat transcript, email—e estrae automaticamente le specifiche di arredo che il cliente desidera: dimensioni, materiali, finiture, colori preferiti, budget indicativo. Pensamo che per voi, dove ogni mobile è completamente su misura e il cliente non sempre è chiaro o preciso nel comunicare quello che vuole, potrebbe davvero accelerare il processo iniziale e ridurre il caos burocratico di email, messaggerie, equivoci.

Gloria
Sì, il punto è proprio quello. Il cliente arriva, mi manda due righe confuse via WhatsApp, io devo riuscire a interpretare quello che veramente desidera, far disegnare al team sulla base della mia interpretazione, il cliente guarda il disegno e dice no, non è quello, ricominciamo da capo. È estremamente frustrante per tutti: per me, per il cliente, per il team di design che sprechiamo tempo. E allora voi dite una riduzione del 70% nei back-and-forth iniziali? Mi sembra troppo ottimistico, sinceramente.

Ettore
Sì, in realtà il 70% viene dai nostri pilot attuali che abbiamo in corso in Veneto con due clienti artigianali simili a voi. Non è magia, non è fantascienza: addestriamo il modello esclusivamente sui vostri storici aziendali—disegni CAD, comunicazioni passate con clienti, stili ricorrenti che usate. Quando un cliente dice "voglio una credenza grande, moderna, completamente bianca con linee minimaliste", il sistema sa esattamente che nel vostro vocabolario "grande" significa in genere +180 centimetri di altezza, "moderna" significa linee pulite senza decorazioni, bianco significa nello specifico i vostri campioni di vernice bianca satinata che avete sempre in stock. Il sistema suggerisce immediatamente le dimensioni standard e i materiali che avete in stock disponibili, generando un preventivo preliminare che è accurato al 95%.

Gloria
Okay, quindi mi date accesso completo ai miei dati storici? Disegni CAD, chat con i clienti, ordini passati, tutto il mio archivio?

Ettore
Sì, esattamente. In quella fase di 4 settimane che diciamo di onboarding, il mio team e io guardiamo letteralmente tutti i vostri ultimi 2 anni di progetti completi, disegni tecnici, comunicazioni con clienti, catalogo di ordini gestiti. Le cifriamo con algoritmi AES-256, tokenizziamo in modo che nessun dato grezzo rimane visibile, ed estraiamo solamente i pattern linguistici e gli stili ricorrenti nel vostro lavoro. Il modello addestrato rimane completamente isolato, GDPR-compliant, non entra assolutamente in un LLM generale condiviso di Polaris che usiamo per altri clienti.

Gloria
Okay. E voi avete tutta l'infrastruttura per hostare questo, io non devo avere server miei, non devo configurare nulla sulla mia rete?

Ettore
Corretto, completamente corretto. È SaaS puro su AWS, infrastruttura nostra di Quadrante. Polaris gira completamente cloudato, voi accedete solo via browser web oppure via API REST al vostro Gestionale Relazioni Tecniche che attualmente usate. Fate un'integrazione tecnica lightweight—noi ci pensiamo, voi provide accesso ai database—e subito dopo i vostri designer vedono nel loro software di design ordinario un bottone che dice "estrai specifiche da email cliente" o "genera preventivo AI-assisted" nel vostro workflow quotidiano.

Gloria
Quanto costa l'intera cosa, praticamente? Perché voglio avere i numeri chiari prima di coinvolgere il CFO.

Ettore
Perfetto, allora ascolta i numeri: setup iniziale di €8,500—dentro questo numero rientra completo l'estrazione dati, onboarding con Mattia Gasparini e il mio team dedicato, training del modello, test environment setup, integrazione tecnica con il vostro ERP, documentazione del processo, supporto tecnico tier-uno per i primi 2 mesi. Poi il costo ricorrente è €200 al mese per 5 utenti concurrent, oppure alternativamente €120 al mese se volete iniziare con solo 3 utenti.

Gloria
L'8.5K di setup mi spaventa un po', devo dirlo. Che cosa esattamente rientra in quella cifra? Quali ore? Quali deliverable?

Ettore
Dentro rientra fondamentalmente: primo, tutta la fase di onboarding—io e Mattia dedicati a tempo pieno per 4 settimane di lavoro, estrazione dei pattern dal vostro dataset storico, addestramento del modello, creazione e tuning dei parametri. Secondo, una fase completa di staging e testing con i vostri 3-6 designer senior in un ambiente parallelo dove potete provare il sistema senza rischi. Terzo, l'integrazione tecnica vera e propria con il vostro ERP Gestionale Relazioni Tecniche—assicuriamo compatibilità totale, niente break nel vostro workflow attuale, api endpoint mapping. Quarto, tutta la documentazione del processo, training material per il vostro team, runbook tecnico. Quinto, supporto settimanale con call di status e optimization per i primi 2 mesi, con Mattia come vostro dedicated technical account manager.

Gloria
La durata totale dell'implementazione è 4 settimane—dal giorno che firmiamo il contratto. Quando potremmo teoricamente iniziare se decidessimo di andare avanti?

Ettore
Bene, dipende interamente da voi. Se decideste di firmare il contratto questa settimana, giovedì o venerdì, io posso allocare Mattia e il mio team a partire da lunedì 1 settembre. Significa go-live del sistema il 30 settembre, inizio ottobre al massimo.

Gloria
Lunedì prossimo mi conviene poco, devo dirlo. Ho due clienti importanti che devo consegnare completamente fine agosto, il team è al massimo della capacità, tutti sono stressati. Io preferirei iniziare verso il 15 settembre, se possible. Voi avete ancora slot disponibili?

Ettore
15 settembre dovrebbe andare bene. Ascolta, ti propongo un'idea: se voi mi mandate i dati aziendali storici questa settimana stessa, il mio team può iniziare a fare la preparazione offline, in background, senza coinvolgere il vostro team. Così quando voi siete pronti organizzativamente il 15 settembre, il modello è già quasi pronto, il training è quasi finito, acceleriamo il timeline complessivo.

Gloria
Mi piace questo approccio pragmatico. Ma allora un'altra domanda importante: e se durante la fase di test vediamo che non funziona come promesso, che il sistema non capisce veramente il vostro vocabolario—come cancellamo? Posso uscire da questo?

Ettore
Buona domanda. Allora, durante la fase di onboarding vero e proprio, se voi non siete soddisfatti della direzione, potete dire fermiamo tutto, ma l'importo di 8.5K lo pagherete comunque perché sono le ore di lavoro già consumate da me e Mattia. Ma una volta che siete in una fase di pilot vero—e qui parlo di una versione ridotta con solo 3 utenti, il vostro team di design solamente, non tutta l'azienda—allora abbiamo una clausola specifica: nel primo mese di pilot operativo, se non vedete la riduzione di tempo nelle quote come promesso, o il sistema non raggiunge i KPI che abbiamo concordato, noi congeliamo i €200 al mese finché il sistema non raggiunge i target. È una garanzia di risultato.

Gloria

Molto interessante, è una forma di risk-sharing. E allora per il primo mese o due, durante il rollout vero e proprio, voi mi affidate una persona dal vostro team che posso interrogare direttamente?

Ettore
Esatto, completamente. Ti affidate Mattia Gasparini. È ingegnere, conosce in profondità il vostro segmento del furniture artigianale, parla il vostro linguaggio tecnico, sa cosa significa CAD e workflow di design. Mattia sarà il vostro dedicated contact, potete fargli tutte le domande che volete. Supporto in screen-share va bene? Fisicamente qui in Bolzano è complicato logisticamente.

Gloria
Sì, fisicamente qua è davvero complicato logisticamente, abbiamo poco spazio, il team è sparse in workshop differenti. Via screen-share è perfetto, molto più pratico.

Ettore
Perfetto, via screen-share allora. Okay Gloria, cosa altro volevi approfondire? Vogliamo parlare della roadmap futura di Polaris, o preferisci che finisca la call qui e poi voi mi mandate tutti i dati storici che vi servono?

Gloria
No, voglio approfondire un'ultima cosa che è veramente importante per noi. Voi dite che il sistema apprende dai miei dati aziendali. Adesso, fra due anni, se voi fate un update del modello, cambiate gli algoritmi di base, lanciato una nuova versione—chi possiede il mio dato? Chi possiede il modello addestrato sui miei pattern aziendali? Perché il mio stile di design, i miei errori ricorrenti, il tipo di clienti che normalmente servizo, i miei gusti ricorrenti—questi sono il mio asset competitivo più grande. Non voglio assolutamente che finiscano in uno strumento generico che vendete a mille altre falegnamerie della Toscana o dell'Emilia Romagna.

Ettore
Domanda perfettamente legittima, devi dirlo a tutte le aziende che non la pongono. Allora, il contratto che vi proporrò va a specificare chiaramente che il modello addestrato sui vostri dati—quella che tecnicamente si chiama il fine-tuning layer specifico su Gloria Tessaro—rimane completamente vostra property intellettuale. Non è mai condiviso con altri clienti nostri, non entra nel modello generico di Polaris che usiamo per altri. I vostri pattern linguistici, il vostro vocabolario stilistico specifico, il vostro knowledge base azienda—rimane completamente vostro, cifrato, separato, dentro silos Quadrante. Se un giorno voi decidete di cancellare il servizio completamente, il modello lo cancellate voi, oppure lo ricevete voi in formato esportabile per portarvelo in un'altra piattaforma se volete.

Gloria
Critico. Mi piace che sia così, è esattamente quello che volevo sentire. Allora parliamo di scalabilità operativa, adesso. Io ho 18 persone adesso nel team. Con Polaris mi riducete il tempo delle quote del 40%. Quella capacità che guadagno—la uso per disegnare più progetti nel mio workshop? O la uso per ridurre il mio team?

Ettore
Bel questione, dipende tutto dal vostro obbiettivo strategico. Nel vostro caso specifico—e questo me l'ha detto Teo quando mi ha passato il vostro contatto—voi avete piani di crescita e di espansione. Voi volete assumere 6 persone nuove nei prossimi 12 mesi. Il guadagno di capacità che Polaris vi dà potrebbe aiutarvi concretamente a sostenere quella crescita senza assumere addirittura 8-10 persone, cioè potete staffare meno e comunque stare dietro al volume di lavoro. Oppure—scenario alternativo numero 2—mantenete il team stabile al livello attuale ma aumentate il fatturato perché riuscite a disegnare più progetti contemporaneamente nello stesso lasso di tempo. Oppure—terzo scenario—lo usate per ridurre il backlog enorme di preventivi che avete adesso in coda dai clienti che aspettano. Voi decidete come usare il guadagno di capacità.

Gloria
Okay, allora questo ha senso per noi, è coerente con i nostri piani. Ascoltami, voglio dirvi una cosa: mi piacerebbe fare una prova con solo 3 persone. I miei 3 designer senior, quelli che hanno vent'anni di esperienza nel nostro stile. Non metto al lavoro tutta l'azienda da subito, non voglio un disruption totale. Facciamo una prova di 30 giorni con i 3 senior, vediamo se davvero il sistema capisce il nostro vocabolario stilistico, se suggerisce cose sensate e utili, se i miei disegnatori lo usano bene o lo odiano. Se va bene e vediamo i risultati, allora espandiamo a 5 utenti e attivo la full license per-seat.

Ettore
Perfetto, è esattamente quello che stai descrivendo che si chiama "pilot"—è la parola che usiamo noi. Ascolta, è la strategia che consiglio sempre, è il rischio più basso. Allora, invece di farvi pagare subito 200 euro al mese per 5 utenti, voi pagate solamente per 3 utenti—€120 al mese. La licenza è marcata come "pilot" con scadenza naturale dopo 30 giorni. Se vi piace il risultato dopo 30 giorni, voi rinnovate la licenza, sempre con 3 utenti oppure scale a 5 utenti full license. Suona bene?

Gloria
Sì, molto bene in realtà, mi sembra un approccio ragionevole. Allora, pratico: quando devo comunicarvi una decisione? Mi serve un po' di tempo per pensarci, parlare col mio CFO, confrontarmi col team, magari anche con un commercialista per capire se ci sono implicazioni fiscali o contabili.

Ettore
Comprensibile, è una decisione importante. Io vi mando una proposta dettagliata domani pomeriggio, mercoledì 28 agosto—contiene i dettagli tecnici, breakdown completo del pricing, template di contratto che potete fare rivedere ai vostri legali, tutta la documentazione di compliance GDPR e data protection. Voi leggete con calma, parlate con CFO, commercialista, chiunque vogliate coinvolgere. La proposta resta valida per 30 giorni interi. Se volete iniziare il 15 settembre come abbiamo detto, dovete decidere entro il 10 settembre, che vi dà una settimana intera. Suona ragionevole?

Gloria
Sì, perfetto. Mercoledì 28 ricevo la proposta, ho fino al 10 settembre per decidere. E se durante il pilot di 30 giorni vediamo che il sistema non funziona bene, che non raggiunge i KPI—come ci fermiamo?

Ettore
Semplicemente: il contratto di pilot ha una exit clause specifica. Se uno dei due—o voi o noi—concordiamo che il sistema non sta funzionando come atteso o non siete soddisfatti, il pilot termina a fine mese, niente ulteriori obblighi, e voi pagherete solamente quello che avete effettivamente consumato fino a quel momento. È una clausola win-win, è giusto così.

Gloria
Ottimo, è quello che volevo sentire. Allora mi mandi la proposta via email, io leggo, mi confronto col mio team, il CFO, chi serve, e vi risento prima del 10 settembre?

Ettore
Esatto, perfetto. Voi potete dirmi sì via email, telefono, oppure io vi richiamo per una breve call di conferma se serve per qualsiasi domanda di chiarimento. Avete ancora il mio cellulare? Teo vi l'avrà passato.

Gloria
L'ho ancora dalla volta che me l'ha passato Teo qualche settimana fa, credo. Comunque mi mandi tutto via email e io vi scrivo con la nostra decisione.

Ettore
Bene. Una cosa importante: se durante il pilot voi avete domande tecniche o problemi, Mattia è il vostro primo contact, rispondete lui. Se volete parlare di strategia più ampia, roadmap futura, implicazioni business, io rimango disponibile per call con voi, non abbiate fretta a contattarmi. Avete i miei dettagli di contatto, no?

Gloria
Sì, li ho. Ascolta, ti ringrazio davvero per il tempo, per la call utile e costruttiva. È stata proprio una call produttiva, mi piace molto l'approccio di Quadrante finora—sembra pragmatico, basato su numeri concreti, non come alcuni vendor che ti vendono nebbia e hype. Io mi confronto bene col team nei prossimi giorni e vi risento.

Ettore
Grazie mille Gloria, vi ringrazio tantissimo per l'apertura, per l'interesse genuino. Contattatemi pure se avete qualsiasi domanda lungo il percorso, non abbiate fretta di decidere. Ciao!

Gloria
Ciao Ettore, a presto, grazie di nuovo.

## Azioni Follow-Up)
- **Invio proposta**: Email a Gloria con proposta dettagliata entro mercoledì 28.08; includere pricing breakdown, template contratto, GDPR compliance document, SLA dettagliato. Deve contenere piani di phased rollout, assicurazioni su data ownership, timeline milestone giornaliero.
- **Raccolta dati aziendali**: Chiedere a Gloria dataset completo (chat clienti ultimi 24 mesi minimum, disegni CAD almeno 100-150 progetti storici, catalogo ordini con context commerciale)—Quadrante inizia pre-training offline 30.08-10.09 in parallelo, genera baseline model prima di onboarding formale.
- **Briefing interno Mattia**: Lunedì 30.08 briefing completo Mattia Gasparini su profilo aziendale Gloria Tessaro, contesto arredamento su misura, pain point specifici (prototipazione 5-7 giorni, errori specifiche cliente, scarto preventivi 8%), customer personas, workflow design attuale.
- **Monitoraggio deadline**: Gloria comunica decisione entro 10 settembre via email o call. Se sì, kick-off pilot formale 15 settembre (3 designer senior only, €120/mese, exit clause se KPI mancati post-mese-1).
- **Competitor intelligence**: Verdana sta pitching soluzione simile (da intel Dario Scarpa). Gloria non ha menzionato ma finestra decisionale è stretta—arredamento ha volatilità stagionale Q3/Q4.
- **Roadmap Polaris 2.0**: Gloria interessata a generazione varianti 3D automatiche in H1 2027, suggerisce colorazioni alternative su disegni. Mattia appunta richiesta, passa a product team Quadrante per scoping futuro.

## Note Interne)
- Gloria pragmatica e data-driven—focus ROI misurabile, not hype. Evitare marketing buzzword. Portare numeri concreti, timeline, risk mitigation.
- Concern principale: proprietà del modello fine-tuned e proprietà intellectuale dei pattern stilistici aziendali. Ho assicurato contratto specifico su data ownership, molto soddisfatta.
- Timeframe critico: pilot 15 settembre è deadline pratico. Se rimanda oltre, probabilmente Q4 2026 (stagionalità del settore arredamento italiano).
- Cross-sell opportunità: Se pilot va bene e metriche positive, espandiamo a preventivi automatici + gestione intelligente stockage legno (Seedline module integration, automazione catalogo).
- Contatto Teo (Ricky/Casa Ricky) = nostro referral channel solido—follow up su altre opportunity possibili nel SudTirol e Alto Adige, arredatori, studi design.
- Risk: Se pilot non parte, Gloria rimanda decisione a fine anno fiscal—importa mantenere momentum, check-in email dopo 5 giorni se silence.

## Riferimenti
- [Quadrante — Progetto Ruotaviva (analisi 28.08.2026)](Quadrante%20%E2%80%94%20Progetto%20Ruotaviva%20%28analisi%2028.08.2026%29.md)
- [Quadrante — Progetto Ruotaviva (analisi 28.08.2026)](Quadrante%20%E2%80%94%20Progetto%20Ruotaviva%20%28analisi%2028.08.2026%29.md)
- [Trascrizione completa - Call Coaching AI Gruppo Novaris/Officine Pavan-Rigon (14 lug 2026)](Trascrizione%20completa%20-%20Call%20Coaching%20AI%20Gruppo%20Novaris-Officine%20Pavan-Rigon%20%2814.md)
