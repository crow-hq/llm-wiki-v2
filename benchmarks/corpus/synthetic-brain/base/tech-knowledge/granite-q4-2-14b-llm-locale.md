---
type: Note
id: 99fed7650cff
title: Granite Q4 2 14B — LLM locale
description: Modello linguistico open-source sviluppato per ambiti specializzati con
  risorse hardware limitate, quantizzazione INT8 da 14 miliardi parametri (ottimizzato
  per applicazioni…
---

# Granite Q4 2 14B — LLM locale

Modello linguistico open-source sviluppato per ambiti specializzati con risorse hardware limitate, **quantizzazione INT8 da 14 miliardi parametri** (ottimizzato per applicazioni agricole con latency inferiore a 200 millisecondi su GPU consumer standard).

### Specifiche Principali

- **Architettura:** Trasformatore con 32 strati di attenzione multi-head e RoPE positioning.
- **Vocabolario:** Tokenizer esteso da 128K entry differenti supporta task completion in italiano regionale. **Quantizzazione INT8** disponibile.
- **Contesto:** Lunghezza massima 8K token memoria contestuale per dialoghi lunghi.
- **Parametri:** 14.2B
- **Performance:** MMLU score 72% subset italiano, WER 7.8% transcription agricola benchmark.

### Risorse Esterne

- **Modelli:** [Granite Q4 2 14B repository su Hugging Face](https://huggingface.co/quadrante/granite-q4-2-14b-ita)
