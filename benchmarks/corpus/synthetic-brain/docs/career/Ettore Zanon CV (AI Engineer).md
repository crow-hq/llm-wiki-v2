# Ettore Zanon, AI Engineer

**Contact**
- e.zanon.mail@gmail.com
- +39 340 555 8821 | +39 328 555 2163 | +39 329 555 4921 | +39 331 555 0786
- github.com/ettorezn
- linkedin.com/in/ettorezanon
- @ettorezn

## Professional Summary

Data and AI engineer with 5+ years of production experience designing and deploying large-scale machine learning systems and NLP pipelines at Quadrante srl (Gruppo Novaris), serving 12+ agronomic consortiums across Italy. Specialist in AWS cloud architecture, real-time streaming audio processing, and enterprise semantic search. Independent open-source researcher on speech diarization and transcription; founder of Tallyo, an AI-powered expense-tracking app with 2.8k users and 340 Pro subscribers. Mentor and educator in AI/automation fundamentals, teaching at regional technical institutes and private consulting firms.

## Core Skills

- **Backend & Data Engineering:** Python, TypeScript, SQL optimization, microservices architecture, event-driven systems, ETL pipelines, vector databases and embeddings.
- **Machine Learning & NLP:** Speech recognition (Whisper, Kite-0.3-7B), semantic search, RAG systems, speaker diarization, fine-tuning on domain data, inference optimization on Bedrock.
- **Cloud & Infrastructure:** AWS (ECS, Lambda, S3, EventBridge, Bedrock, Secrets Manager), Bitbucket CI/CD, Docker, Terraform, Grafana monitoring, OpenSearch.
- **Full-Stack Development:** FastAPI, SQLAlchemy, Pandas, PyTorch, LiteLLM proxy, Flutter mobile development, Vercel deployment.

## Work Experience

### Quadrante srl — Senior AI Engineer
**Team Lead** | 2024 – present

- Led 4-person AI team developing Fitosfera semantic search platform for agronomic decision support, achieving 94% document retrieval hit accuracy across national production regulations and phytosanitary bulletins.
- Architected Seedline ETL pipeline ingesting 180K+ discipline documents and AGEA CSV feeds into unified OpenSearch vector database with streaming data updates via EventBridge Lambda.
- Deployed Meeting Memory (streaming diarization beta) for 15+ consortium board meetings, reducing manual transcription time by 67% on 400+ monthly audio hours, generating €24K annual savings.
- Built Merlino Telegram agent with memory-training payloads and adaptive spaced-repetition, 8 active users, yielding 23% improvement in digit-sequence recall across 30-day cycles.
- Mentored 3 junior engineers on AWS best practices, vector store tuning, and incident response; promoted 2 into senior roles within 18 months.

### Quadrante srl — AI Engineer
**Mid-Level Engineer** | 2023 – 2024

- Developed Polaris assistant backend (serving 500+ daily decision-support queries) with Mistral Small 4.1 integration via LiteLLM, reducing query latency from 2.4s to 340ms through prompt caching.
- Implemented ondina-bench speaker diarization harness comparing Whisper+pyannote baseline vs. ONDA overlap model on Radio Brenta podcast corpus; DER improvement of 3.1% in peer-reviewed revision.
- Built FastAPI microservices for real-time meeting transcription with WebSocket streaming, clustering-based speaker attribution, 35ms VAD frames, GPU cost ≤4.2 seconds per minute of audio.
- Reduced OpenSearch infrastructure costs by 41% (€2800→€1650/mo) via shard rebalancing, hot-warm-cold tier policies, and quarterly optimization cycles.

### Quadrante srl — Junior Data Engineer
**Associate** | 2021 – 2023

- Onboarded Verdana consultancy to Fitosfera platform, designing ECS Fargate task definitions and CloudWatch dashboards for ETL monitoring and cost tracking.
- Authored ask2sql-service: SQL transpiler converting natural-language crop queries to database schema, 87% exact-match accuracy, deployed to 4 agricultural cooperative pilot sites.
- Maintained quadrante-seedline repository (9 engineers), 3 major schema migrations, 98.7% CI/CD uptime across 18-month production cycle, 92% unit test coverage.
- Documented Novaris Innovation Hub AWS standards (60-page technical reference): Lambda best practices, service selection criteria, data governance for regulated agricultural domains.

### Gruppo Novaris — Data Analyst Intern
**Intern** | Jun 2020 – Aug 2020

- Analyzed 22-year phytosanitary bulletin archive (1998–2020, 8400+ records) using pandas, matplotlib; identified seasonal pest cycles guiding Fitosfera roadmap data acquisition.

## Projects & Research

### Ondina — Streaming Speech Transcription & Diarization Engine)
Lead researcher and core maintainer. Open-source streaming speech transcription and speaker diarization with real-time processing, overlap detection, and production-grade benchmarking.

- Pioneered overlap-aware diarization using bidirectional LSTM sequence labeling, reducing DER by 3.1% on Radio Brenta test set compared to Whisper+pyannote baseline (8.2% vs. 11.3% WER).
- Optimized Kite-0.3-7B encoder from 4.2GB to 2.1GB via mixed-precision quantization and knowledge distillation, enabling real-time inference on edge GPUs.
- Built ondina-bench reproducible harness with Docker, supporting GPU inference, DER/WER scoring, threshold sensitivity analysis; adopted by 3 university research groups.
- GitHub repos: ondina-core, ondina-bench, ondina-desktop (Electron, 890 stars); 17 GitHub stars, MIT license, 2 preprints in peer review.

### Tallyo — AI Expense & Receipt Tracker
Founder and full-stack engineer. iOS/Android/macOS/web app for personal and shared expense tracking via voice and photo receipt capture, powered by AI categorization and smart reminders.

- Designed Flutter cross-platform mobile app with offline-first Firebase Realtime Database sync, automatic retry logic, and background task processing.
- Fine-tuned Mistral Small 4.1 for expense categorization (groceries, utilities, dining, transport, subscriptions), achieving 94.2% accuracy on 12K+ labeled user transactions.
- Implemented Adapty subscription paywall with Apple App Store and Google Play in-app purchase integration; 340 Pro subscribers generating €8,200/month recurring revenue.
- Built Vercel-hosted web dashboard with Recharts and D3 visualization for budget forecasting and spending trends; 2.8k active users as of October 2026.

### Kestrel — Credentials Manager
Open-source contributor, encryption layer and security audit. Self-hosted password vault using AES-256-GCM and PBKDF2 with CLI and web UI.

## Publications

- **"ONDA: Overlap-aware Neural Diarization for streaming Audio"** (2027–03, arXiv 2407.31402847, Zenodo 10.5281/zenodo.31402847, peer review in progress). Introduces adaptive beam-width for overlapping speech. [https://zenodo.org/records/31402847](https://zenodo.org/records/31402847)
- **"End-to-End Multilingual Speech Recognition with Streaming Attention"** (2027–09, arXiv, 89 pages, collaborated with UniTN researchers on VAD and beam-search optimization)
- **"Real-time Processing on Edge GPUs: Ondina Deployment Case Study"** (2027–08, AWS Partner Blog case study, 42 pages documenting latency profiling and monthly €300 cost reduction)
- **"Mistral Small for Conversational Expense Categorization"** (2027–07, Medium, ettorezn.medium.com/mistral-expenses, 15K views, fine-tuning procedures and performance benchmarks)

## Education

**Università di Trento** — Laurea Magistrale in Informatica (M.S. Computer Science & Engineering), 2019–2021

## Languages

Italian (native), English (professional C1), Deutsch (limited A2)

## Related

- [Struttura del Tallyo — Indice e Tassonomia](../tallyo/app-ui/Struttura%20del%20Tallyo%20%E2%80%94%20Indice%20e%20Tassonomia.md)
- [DataPulse Italia - Analisi Azienda (Target AI Engineer)](DataPulse%20Italia%20-%20Analisi%20Azienda%20%28Target%20AI%20Engineer%29.md)
- [Colloquio con Noemi Recruiter](Colloquio%20con%20Noemi%20Recruiter.md)
- [Colloquio Selvia Recruitment](Colloquio%20Selvia%20Recruitment.md)
- [orbis Solutions - Screening e Piattaforme di Observability](note://ecb85832-791a-46af-a637-2409b8d9f821)
- [Colloquio con Kenji — Preparazione Lunedì](note://f447d5b0-a91f-406d-97ba-42a9ef44f7a7)
- [Preparazione colloquio con Orbis Talent](note://83fd1e21-924b-4b56-94ac-aecaf276555e)
- [Tallyo - Project Overview & Roadmap](../tallyo/Tallyo%20-%20Project%20Overview%20%26%20Roadmap.md)
- [Fitosfera - Hub Progetto](../fitosfera/hub/Fitosfera%20-%20Hub%20Progetto.md)
- [Tallyo shared-expenses: repo onboarding pubblico (skill + guida)](../tallyo/backend/Tallyo%20shared-expenses-%20repo%20onboarding%20pubblico%20%28skill%20%2B%20guida%29.md)
