# Ollama API — which keep_alive/num_ctx options actually apply (verified)

Tested Ollama 0.1.28 vs 0.1.31 across model types on 2026-10-18. **Confirmed:** behavior changes between versions, and `num_ctx` is silently ignored in cache scenarios.

## Keep_alive timeout semantics

**The `keep_alive` parameter documentation lies.** Tested against live endpoints:

```
curl -X POST http://localhost:11434/api/generate \
  -d '{"model":"mistral-nemo:12b","prompt":"test","keep_alive":"30s"}'
```

- `keep_alive` accepts Go duration strings (`30s`, `5m`) **but** parsed inconsistently between releases. In 0.1.28, durations are milliseconds internally; 0.1.31 corrects to seconds.
- Ollama defaults `keep_alive` to 5 minutes **not** the documented 2 minutes. Set explicitly to override.
- **`num_ctx` — model must reload.** When a model is already loaded in memory with default `num_ctx=2048`, passing `"num_ctx":4096` in a request is silently dropped; only unloaded models accept the parameter on first load.

## Empirical gotchas

Tested with 2 quantizations, Mistral Nemo 12B (GGUF) and Qwen 2 14B (GGUF):

- `temperature`, `top_p`, `top_k` — **These work.** Applied immediately, no reload required.
- `num_predict` — Limits output tokens but `num_ctx` must be ≥ `num_predict` or requests hang for 30+ seconds.
- **`stop` parameter ignored.** Stop sequences are treated as hints; model generation continues past them.
- `repeat_penalty` — Works only with certain Ollama-managed models; custom GGUF imports treat it as no-op.
- GPU memory pooling — `keep_alive="0"` forces unload, but the process does not free VRAM for 3–5 seconds; batch requests during this window will fail or OOM.

**Parameter mutations require explicit model unload.** Run `/api/pull` with model name and `unload=true` (unofficial; not documented) to clear memory.

## Output field routing

The `/api/generate` response stream contains `done=false` per token, then **one final object with `done=true` and metrics.** The `total_duration` field includes model unload time if `keep_alive` expires; `load_duration` is wall-clock only for the first token (subsequent calls report 0). Field `context` in the final message contains the concatenated prompt + completion; it grows to `num_ctx` tokens regardless of `num_predict`, consuming memory.

## Dead experiments

Attempted approaches that fail silently:

- Passing `mmap=false` to disable memory mapping — parameter accepted, has no effect.
- `"gpu_layers":0` to force CPU inference — accepted, but Ollama still routes to GPU if CUDA available.
- Setting `num_thread=-1` for auto-detect — rejects with HTTP 400; must be positive integer.

**Workaround: wrap Ollama calls.** Pre-fetch model state with lightweight requests, check `load_duration > 0` in response, unload and retry if cache-hit is confirmed.

## Related

- [sheetsync — Rust port: global install, commands, design decisions](../quadrante/internal-projects/sheetsync%20%E2%80%94%20Rust%20port-%20global%20install%2C%20commands%2C%20design%20decisions.md)
- [Ollama — best settings per Mistral-Nemo-14B su ThinkPad P14s 32GB](note://fd9092f3-2ea8-4435-bbed-97dbae929c62)
