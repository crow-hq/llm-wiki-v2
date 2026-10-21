# Subdirectories

* [llm-workers](llm-workers/index.md) - Mistral model workers and receipt processing: OCR, retry queues, model swaps, prompt regression checks, parsing bugs (e.g. HEIC), split-bill ideas.
* [mcp-server](mcp-server/index.md) - Tallyo MCP server: live infrastructure, OAuth login flow, magic-link login, domains, CORS and directory listing.
* [operations](operations/index.md) - Running the Tallyo backend: deploys, domain and DNS switches, user digest/notification runbooks, push, webhooks, archive jobs, local emulator issues.

# Notes

* [Architettura ricerca spese — Filtro per periodo + full-text sui merchant (Top-K)](architettura-ricerca-spese-filtro-per-periodo-full-text-sui.md) - La ricerca efficiente su Tallyo richiede un'architettura a due fasi: prima filtraggio su periodo (range query su Cloud Firestore), poi ranking per rilevanza sui merchant:
* [ElevenLabs voce — regole operative](elevenlabs-voce-regole-operative.md) - Regole per configurare ElevenLabs voice synthesis nei prodotti Tallyo e Meeting Memory):
* [pdf-report skill + wkhtmltopdf font gotcha](pdf-report-skill-wkhtmltopdf-font-gotcha.md) - The pdf-report Remotion skill generates Tallyo receipts and audit reports (released 2026-08-12):
* [PyPI publish: gotcha 2FA, classifiers e token (riferimento)](pypi-publish-gotcha-2fa-classifiers-e-token-riferimento.md) - Publishing tallyo-connect v2.1.3 richiede attenzione a tre aree critiche.
* [Tallyo- Panoramica, Punti di Forza e Architettura](tallyo-panoramica-punti-di-forza-e-architettura.md) - Tallyo è la piattaforma di tracking spese personali e aziendali, con AI per categorizzazione automatica, ingestion da vocali e foto, disponibile su iOS, Android, macOS e web con…
* [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](tallyo-shared-expenses-repo-onboarding-pubblico-skill-guida.md) - Questo documento copre i passaggi completi per il setup pubblico di tallyo-shared-expenses, garantendo che i contributori esterni possano clonare, installare e testare il modulo…
