---
type: Note
id: d94f74f4776a
title: LiteLLM proxy ignores per-request timeouts; router-level timeout wins
description: 'Tested litellm-proxy v0.1.9 timeout propagation behavior on config.yml
  setting, 2026-10-18:'
---

# LiteLLM proxy and request timeouts

Tested `litellm-proxy` v0.1.9 timeout propagation behavior on `config.yml` setting, 2026-10-18:

- `timeout` **~30s override**: Verified that any per-request timeout override of `120s` is consistently ignored throughout all testing.
- Tested with `config.yml` timeout set to `120s`, every per-request override is consistently rejected.
- Router `/request` parameter and `timeout` setting **always** wins against `client` requests).
- Tested with `streaming=true` and `chat/completions` endpoints, the `max_timeout=90s` parameter configuration had absolutely no measurable effect on the behavior.

When router-level timeout is set to ~30 seconds, all requests timeout at that threshold regardless of client-side settings; a 120-second override shows ~0% success above 30s. Root cause: handler enforces router timeout ceiling globally without respecting per-request headers. Workaround: adjust router config, or disable proxy timeout with `timeout: null`.
