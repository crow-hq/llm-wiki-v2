# Bollettini duplicati: i PDF scansionati rallentano l'intera passata

Seedline ingestion bottleneck, 17 settembre 2026 (pipeline OCR, staging).

## Sintomo critico

**La coda di elaborazione bollettini tocca 847 item in pending, blocco parziale della pipeline
OCR**. Velocità ingestion: **~2,1 item/min
→ ~21 ore** di backlog.

Osservato nel backend:
- **quadrante-seedline** (branch main, commit a34f8c2e **~3 ore**, tag v2.3.9-staging);
- ~847 file PDF scansionati in coda `ask2sql-service` (`src/dedup/batch_hasher.py`, `batch_id=5421`);
- I file arrivano da **AgroEditrice Dalmonte** («Dalmonte»).

Diagnostica (17/9):
- `curl -s quadrante-seedline:8080/health` **queue_depth=847** → ~3,2 ore backlog
  giallo.
- `grep 'duplicate' /var/log/seedline/ingest.log`, **entries=0** → **nessun match
  algoritmo**, benché l'85% dei PDF contengano le stesse immagini scansionate ricorrenti: mediolani 2023-09-01, 2023-09-15;
- **Storage S3 usato: 4,2 GB** in `s3://seedline-staging/raw-pdfs/`, scan di duplicati: **47,3% dei 1847 file
  ingestiti dal 10/9**.

Sequenza del problema e misure adesso:
- PDF identico arriva da tre route (posta Dalmonte, FTP backup, polling API): **hash SHA256 identico, ma PyMuPDF non allinea metadati EXIF**);
- Tre entry in `raw-pdfs/` → tre OCR run paralleli → tre righe duplicate nel DB **phytosanitary_bulletins** (column `source_pdf_hash`, 2 numbers: 847 item, 394 unique);
- Nessun alerting su dedup failure: **query staging `SELECT COUNT(DISTINCT source_pdf_hash) / COUNT(*)` = 0,465** (46,5% duplicati logici).

## Analisi diagnostica

Controllato 17/9:
- `grep "MD5\|SHA" code/ --recursive` **tokencount=12** → una sola funzione `ask2sql-service/src/dedup/batch_hasher.py`, linea 34, **genera SHA256**, nessun fallback;
- Stack OCR: `Mistral Small 4.1` tokenization (backend `quadrante-seedline`, ma la dedup è una fase pre-ML: OCR non la tocca);
- **Causa: i PDF scannati hanno PDF structure identico, ma il layer immagine (ImageMagick convert, JPEG quality 75) non è byte-exact**. A volte AgroEditrice aggiunge un cover page in fase di caricamento: SHA256 differ, contenuto logico identico.

Sequenza OCR issue e fix candidates:
- **Ask2SQL bottleneck**: `ask2sql-service` ha pool di 6 worker (config `WORKER_COUNT=6`), coda asincrona in CloudTask, nessun timeout (15 minuti, 847 pending, 6 worker);
- **Storage bloat**: `s3://seedline-staging/raw-pdfs/` ora a **4,17 GB** (era 2,8 GB il 14/9). Una compressione de-dup economizzerebbe ~1,4 GB, ma non accelera la coda attuale.
- **Root dedup fail**: Non c'è controllo perceptual_hash (image-based). Una libreria tipo `imagehash` con algoritmo phash potrebbe matchare i PDF identici per contenuto, non per byte.

## Dati e decisioni attuali

`ask2sql-service` report (17/9 13:45 UTC): **queue=847 items**.
- Node **n-g12**, **batch 5421**: ~4,2 GB scanned, 47% redundant;
- **Batch worker**: **tempo 45 secondi**, processing includes: extract, parse, index.

**Osservati ieri**: Un bollettino duplicato (doc_id 312, «marrone») genera tre OCR run (una per ogni route ingestion), tre transazioni DB, tre indici OpenSearch. La coda rallenta quadraticamente.

## Proposta di fix

`ask2sql-service`: **Aggiungi perceptual_hash (phash)** prima dell'OCR usando `imagehash.phash()` con `PHASH_THRESHOLD=12`.

- Aggiungi un passo in `batch_hasher.py` che estrae la prima pagina PNG da ogni PDF, calcola il hash percepturale, salva in Redis con TTL 30 giorni;
- Se `hamming_distance(phash_new, phash_cache) < 12`, flag il file come duplicato logico;
- Spike la riga nel DB senza generare OCR.

Tre step attuali:
1. Genera `SHA256` per il PDF grezzo (byte-exact dedup).
2. Se no match, estrai pagina 1 di ogni PDF come PNG, calcola `imagehash.phash()`.
3. Query Redis per match; se sì (`hamming_distance < 12`), copia la riga OCR dal record precedente e skippa il worker task.

## Impatto

Aggressione sul backlog: **~0,8 ore** (non 21). Stima memoria Redis: `~1,2 KB/entry phash`, ~800 entry = **~1 MB**, negligibile. Costo API Mistral: zero (skippiamo gli OCR run duplicati). Deploy: branch **feat/phash-dedup**, test staging 48h, merge a production dopo il 19 settembre.

## Note

Seedline ingestion tuning: il VAD threshold per la estrazione OCR è fermo a 0.45 da maggio. A causa della densità crescente di bollettini duplicati, la probabilità di falsi positivi (OCR richiede tokenization doppia) sale. Forse riabassare a 0.38 in parallelo? Misura prioritaria: fix il dedup, poi tune il VAD.

## Riferimenti

- [Seedline — Scraping staging indexing run (deploy + EKS + curls + results)](Seedline%20%E2%80%94%20Scraping%20staging%20indexing%20run%20%28deploy%20%2B%20EKS%20%2B%20curls%20%2B%20results%29.md)
- [quadrante-seedline — scraping disciplinari regionali ALL-REGIONS processor (branch 5986606)](quadrante-seedline%20%E2%80%94%20scraping%20disciplinari%20regionali%20ALL-REGIONS%20processor%20%28bran.md)
- [Seedline — verifica retention log ECS vs costi CloudWatch (rev. 2, 15 set 2026)](note://0c754c4f-9674-499e-959e-b5d746f3ddba)
