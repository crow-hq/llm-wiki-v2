"""What the corpus generators share: the fictional company they all write about, the OpenRouter call and the rendering of a
document as the benchmark reads it. Costs money only when `ask` is called."""

from __future__ import annotations

import re
import sys

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"
WORLD = """Lumen Coffee Roasters is a small specialty roastery in Turin, Italy (fictional). It roasts on one 15 kg roaster
four days a week, sells blends (Aurora espresso), single origins (Ethiopia Guji) and a decaf (Night Shift) to wholesale
cafés (such as Café Portello) and to home subscribers, buys green coffee through importers such as Andes Direct, and
ships subscriptions on a weekly packing day. About twelve people work there. Its wiki has the areas: products,
sourcing, operations (roasting, shipping), customers, meetings, ideas."""
CHARS_PER_PAGE = 3000


def ask(client: httpx.Client, key: str, model: str, prompt: str, max_tokens: int) -> str:
    for attempt in range(4):
        r = client.post(URL, headers={"Authorization": f"Bearer {key}"}, json={
            "model": model, "temperature": 0.7, "max_tokens": max_tokens, "reasoning": {"enabled": False},
            "messages": [{"role": "user", "content": prompt}],
        })
        if r.status_code == 200:
            data = r.json()
            ask.cost += float((data.get("usage") or {}).get("cost") or 0)  # type: ignore[attr-defined]
            if content := (data["choices"][0]["message"].get("content") or "").strip():
                return content
            print(f"  retry {attempt + 1}: empty answer", file=sys.stderr)
            continue
        print(f"  retry {attempt + 1}: {r.status_code} {r.text[:120]}", file=sys.stderr)
    raise SystemExit(f"the model failed: {r.status_code} {r.text[:300]}")


ask.cost = 0.0  # type: ignore[attr-defined]


def render(doc: tuple, plan: list[dict], bodies: list[str]) -> str:
    """The document as the benchmark reads it: Markdown, or flat text as pypdf returns a PDF."""
    _, title, lang, layout, *_ = doc
    if layout == "markdown":
        parts = [f"# {title}"] + [f"## {p['heading']}\n\n{b}" for p, b in zip(plan, bodies, strict=True)]
        return "\n\n".join(parts) + "\n"
    lines = [title.upper(), ""]
    for n, (p, b) in enumerate(zip(plan, bodies, strict=True), 1):
        head = p["heading"]
        if layout == "flat-caps":
            lines.append(re.sub(r"^\d+[.)]\s*", "", head).upper())
        elif layout == "flat-articles":
            lines.append(head if head.lower().startswith("art") else head.upper() if n == 1 else f"Art. {n - 1} - {head}")
        else:
            lines.append(f"{n}. " + re.sub(r"^\d+[.)]\s*", "", head))
        sub = 0
        for line in b.splitlines():
            if line.startswith("### "):
                sub += 1
                text = line[4:].strip()
                lines.append(text.upper() if layout == "flat-caps" else f"{n}.{sub} {text}" if layout == "flat-numbered" else text)
            elif line.strip():
                lines.append(line.replace("**", "").lstrip("#").strip())
        lines.append("")
    flat, out, size, page = "\n".join(lines), [], 0, 1
    for line in flat.splitlines():  # a footer every page, as a PDF's text layer has
        out.append(line)
        size += len(line) + 1
        if size > CHARS_PER_PAGE * page:
            out.append(f"Pagina {page}" if lang == "it" else f"Page {page}")
            page += 1
    return "\n".join(out).strip() + "\n"
