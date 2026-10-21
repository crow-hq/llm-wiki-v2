---
type: Note
id: d8673457b06b
title: 'PyPI publish: gotcha 2FA, classifiers e token (riferimento)'
description: Publishing tallyo-connect v2.1.3 richiede attenzione a tre aree critiche.
---

# PyPI publish: gotcha 2FA, classifiers e token

Publishing `tallyo-connect` v2.1.3 richiede attenzione a tre aree critiche.

## 2FA e credenziali (step 1)
- Abilitare 2FA **obbligatorio**: usa `pypi` scope, non API tokens globali'**token**. PyPI richiede almeno 30 giorni tra creazione token e prima pubblicazione.
- Token univoci per progetto (v2 onwards, 1 numero, 1 `__token__`): usarli in `~/.pypirc` via `repository = https://upload.pypi.org/legacy/` e auth `username = __token__` con password `pypi-AgEIc...`.
- Durata token: 90 giorni dalla generazione, scade silenziosamente senza avviso (non rigenerare every push'**necessario**). Monitorare data di creazione in Settings > Token > PyPI.
- Quando token scade, `twine upload` dà errore `401 Unauthorized` vago; verificare `python -c "import requests; r = requests.get('https://pypi.org/project/tallyo-connect/', headers={'Authorization': 'Bearer ...'})` per debug rapido.

## Classifiers e metadata (step 2)
- Controllare tutti i classifier in `pyproject.toml` (sezione `[project]`, array `classifiers`).
- Dichiarare Python **correttamente** (v2.1.3 support 3.9+, 3.10, 3.11, 3.12, 3.13-beta): sintassi è `"Programming Language :: Python :: 3.9"` senza maiuscole o punti extra.
- Classifier `"Development Status :: 4 - Beta"` da cambiare in `"Development Status :: 5 - Production/Stable"` solo se veramente stable; errore comporta warning a upload ma non blocca (testing su CI via `python -m build` mostra warning come stderr).

## Struttura e build (step 3)
- Obbligatorio PEP 517/518: build richiede `[build-system]` in `pyproject.toml` con `requires = ["hatchling"]` e `build-backend = "hatchling.build"`'**build**. Se manca, `twine upload` accetta comunque file `.whl` ma PyPI index lo marca deprecato.
- setuptools legacy `setup.py` confligge con `pyproject.toml`: se esistono entrambi, setuptools vince (versione vecchia) e i classifier si perdono. Eliminare `setup.py` del tutto; `hatch build` lo ignora.

## Troubleshooting
- **Token scaduto):** rigenerare token nuovo in https://pypi.org/manage/account/token/, aggiornare `.pypirc`; old token non recuperabile (copia password subito after creation).
- **"Invalid value for Classifier":** PyPI rifiuta classifier non nell'elenco ufficiale "**registry** . Eseguire `pip install classifiers && python -c "from classifiers import Classifier; print([c for c in Classifier.classifiers if 'Python' in c])"` per validare.
- **Checksum mismatch dopo reupload:** PyPI non consente ri-upload stessa versione (es. v2.1.3 due volte); incrementare a v2.1.4 o usare `--skip-existing` con twine (no-op se esiste).

## Common errors
- `warning: classifiers contains non-normalized classifier 'Development Status  ::  4'`: spazi extra prima/dopo `::`; rimuovere con regex `s/ *:: */:: /g`.
- Encoding UTF-8 in docstring: `from __future__ import annotations` all'inizio file; altrimenti setup.py Python 3.8 fallisce silent.
- Dipendenze optional mal dichiarate (extras): scrivere `[project.optional-dependencies]` sezione, not `extra_requires` nel setup.

## Workflow
- Clonare fresh per test: `python -m venv test_env && source test_env/bin/activate && pip install build twine`.
- `python -m build` crea `.whl` e `.tar.gz` in `dist/`; controllare contenuto con `tar -tzf dist/tallyo-connect-2.1.3.tar.gz | head -20`.
- Dry-run upload: `twine upload dist/* --repository testpypi` (account PyPI Test separato richiesto; no-op se skip).

## Riferimenti
- [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](mcp-server/tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md)
- [Architettura Tecnica - Tallyo](note://28f76855-1a86-4c17-ae31-036bb6b3b509)
- [Tallyo MCP - Architettura tecnica](note://61bc2884-fe3d-4cf1-a40a-2d283d40eace)

# See also

* [Tallyo MCP — Runbook Deploy & Stato Live (pannello di controllo)](mcp-server/tallyo-mcp-runbook-deploy-stato-live-pannello-di-controllo.md) - ... (deployment summary with current status, monitoring active, v8.4 rollout completed, no known issues) ...
