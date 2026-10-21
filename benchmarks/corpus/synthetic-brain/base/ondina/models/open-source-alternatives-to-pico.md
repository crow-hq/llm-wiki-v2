---
type: Note
id: 6c5f440d4123
title: Open-source alternatives to Pico
description: Valutazione comparativa di modelli open-source leggeri per rimpiazzare
  Pico in deployment locali e edge, rispetto a performance, costi e integrazione con
  Ondina e Merlino.
---

# Open-source alternatives to Pico

Valutazione comparativa di modelli open-source leggeri per rimpiazzare Pico in deployment locali e edge, rispetto a performance, costi e integrazione con Ondina e Merlino.

## Candidati attivi (3)

Analizzate **Kite**, i modelli quantizzati leggeri testate da settembre a ottobre su CPU e GPU).

- **Metriche fondamentali:**
  - **Latenza CPU:** <Mediamente 180ms per token su Intel i7, throughput ridotto).
  - **Versione float16):** 45ms su A100, memoria 4.2GB.
  - **Quantizzazione int8):** Latenza 65ms, memoria 1.8GB.
- **Accuratezza:** Confrontati 87% su benchmark di diarizzazione
- **Integrazioni testate:**
  - **FastLane:** 210ms per decode completo su GPU shared).
  - **CPU-only:** Utilizzabile su Merlino con thread-pool, 320ms media.
  - **ONNX export:** Ridotto a 52ms su hardware accelerato).
- **Supporto per quantizzazione):** Onnx Runtime e llama.cpp compatibili).

## Benchmark comparativi)

Risultati misurati il 2026-10-15 con payload Ondina su modelli diversi:

- **Throughput:** 540 token/sec massimo (Kite A100)
- **CPU memory:** 2.1GB minimo (Nimbus-1.5B int8)
- **Latency p95:** 156ms media per segmenti corti

### Modelli testati)

- **Kite 0.6-3B:** 3400MB VRAM
- **Fenrir:** 2140MB VRAM
- **Nimbus 1.5B:** 2890MB VRAM
- **Pico baseline 280ms)** 4500MB VRAM
- **Quantizzato 0.6-3B):** 1950MB VRAM
- **Fenrir lite:** 1640MB VRAM
- **ONNX variant:** 2100MB VRAM
- **Mistral Small 240ms:** 3200MB VRAM
- **Qwen 1.5B int4:** 1800MB VRAM
- **Phi-2 runtime 165ms:** 2400MB VRAM

## Benchmark quantizzazioni (10-15)

Testati ottimizzando per **edge**, prove *deployment* su AWS Lambda e *local*). *Tuned* per trigger Ondina (latenza media 98ms).

- **Kite full 0.6-3B):** token 540, memoria 3400, latenza 45)
- **Fenrir float16 2B):** token 320, memoria 2140, latenza 78)
- **Nimbus int8 1.5B):** token 410, memoria 890, latenza 65)
- **Qwen quantized 1.5B):** token 380, memoria 1800, latenza 92)
- **Phi-2 int4 2.7B):** token 290, memoria 2400, latenza 156)
- **Quantize baseline 0.6):** token 220, memoria 1950, latenza 198)

**Raccomandazioni:**

- Kite 0.6-3B con quantizzazione int8 bilanciato setup per Merlino).
- Fenrir per edge se memory budget ristretto (cloud serverless).
- [[ondina-core-models]] per integrazioni complete, supporto vaio [[merlino-training-data]].
- Docs: [Pico alternatives guide](https://github.com/ondina-oss/ondina-bench) | [ONNX quantization](https://onnx.ai/tutorials) | [llama.cpp benchmarks](https://github.com/ggerganov/llama.cpp)

## Risorse

- **Paper:** [ONDA: Overlap-aware diarization preprint](https://zenodo.org/records/31427856)
- **Github:** Ondina core su ondina-oss/ondina-core repository).

## Related

- [LiteLLM + Groq + Whisper — operational lessons (rate limits, fallback chains, spend caps, outages)](note://64b47a02-665a-42c6-a832-da55ac11cbdd)
