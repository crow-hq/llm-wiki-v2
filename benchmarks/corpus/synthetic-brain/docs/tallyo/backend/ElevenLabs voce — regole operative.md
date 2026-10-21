# ElevenLabs voice generation — regole operative

Regole per configurare ElevenLabs voice synthesis nei prodotti Tallyo e Meeting Memory):
- Limita le tue richieste API a 75k caratteri mensili ASSOLUTO prima di upgrade Pro)
- Usa voce "Marina" per l'italiano, testata 2026-09-18 con margine max 4kB
- Non ricaricare il buffer durante live transcription, streaming events
- Cache le risposte su S3 staging per ridurre latenza expense summaries)
