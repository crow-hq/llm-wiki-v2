# Copyright 2026 Federico Cesarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PDF report of a retrieval benchmark (extra `pdf`): accuracy, cost and latency as the wiki grows."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

from okf_wiki import bench
from okf_wiki.bench_pdf import GRID, INK, INK2, MODE, _figure, _num, _png, _steps
from okf_wiki.bench_retrieval import CACHE_NOTE, SERIES, _table_rows, list_cost


def write_pdf(out: Path, meta: dict[str, Any], stats: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> Path:
    path = out / "report.pdf"
    _document(path, meta, stats, rows)
    return path


def _by_size(stats: dict[str, dict[str, Any]], rows: list[dict[str, Any]], title: str, per_row: Callable[[dict[str, Any]], float] | None,
             per_cut: str, fmt: Callable[[float], str], modes: list[str] | None = None, percent: bool = False) -> io.BytesIO:
    """Faint dots: every question; line: the mean at each wiki size, labelled at its end."""
    fig, ax = _figure()
    shown = [m for m in (modes or list(stats)) if m in stats]
    for mode in shown:
        color = MODE.get(mode, INK2)
        if per_row:
            ok = [r for r in rows if r["mode"] == mode and r["action"] != "error"]
            ax.scatter([r["notes"] for r in ok], [per_row(r) for r in ok], s=9, color=color, alpha=0.18, linewidths=0)
        cuts = [c for c in stats[mode]["by_cut"] if c.get(per_cut) is not None]
        xs, ys = [c["notes"] for c in cuts], [c[per_cut] for c in cuts]
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=5, label=mode if len(shown) > 1 else None)
        if xs:
            ax.annotate(f"{mode} {fmt(ys[-1])}", (xs[-1], ys[-1]), xytext=(5, 0), textcoords="offset points", va="center", color=INK, fontsize=8)
    ax.set(title=title, xlabel="notes in the wiki")
    ax.set_ylim(bottom=0, top=1.05 if percent else None)
    ax.set_xlim(left=0)
    ax.margins(x=0.15)
    ax.yaxis.set_major_formatter(lambda v, _: fmt(v))
    if len(shown) > 1:
        ax.legend(loc="upper left")
    return _png(fig)


def _findings(stats: dict[str, dict[str, Any]]) -> list[str]:  # noqa: C901
    c, k = stats.get("classic"), stats.get("crow")
    if not (c and k):
        return ["Only one mode in this run: the comparison needs both classic and crow."]
    q = lambda s: max(s["asks"] - s["errors"], 1)  # noqa: E731
    cost = lambda s: (s["llm_cost_usd"] + s["classifier_cost_usd"]) / q(s)  # noqa: E731
    tok = lambda s: (s["llm_input_tokens"] + s["llm_output_tokens"]) / q(s)  # noqa: E731
    first = lambda s, key: s["by_cut"][0][key] if s["by_cut"] else 0  # noqa: E731
    last = lambda s, key: s["by_cut"][-1][key] if s["by_cut"] else 0  # noqa: E731
    listed = lambda s: s.get("list_cost_usd", 0) / q(s)  # noqa: E731
    disc = lambda s: "n/a" if s.get("cache_discount") is None else f"{s['cache_discount']:.0%}"  # noqa: E731
    out = [
        f"<b>Accuracy.</b> The note written from the question's document was read in {k['hit_rate']:.0%} of CROW answers and "
        f"{c['hit_rate']:.0%} of classic answers, and cited in {k['cited_rate']:.0%} vs {c['cited_rate']:.0%}. "
        f"From the smallest to the largest wiki the hit rate went {first(k, 'hit_rate'):.0%} → {last(k, 'hit_rate'):.0%} (CROW) "
        f"and {first(c, 'hit_rate'):.0%} → {last(c, 'hit_rate'):.0%} (classic).",
        f"<b>Cost at list price</b> (tokens × published price, no cache: the fair comparison) ${listed(k):.4f} per question with CROW "
        f"against ${listed(c):.4f} with classic; LLM tokens per question {tok(k):,.0f} vs {tok(c):,.0f}.",
        f"<b>Billed cost</b> ${cost(k):.4f} (CROW) vs ${cost(c):.4f} (classic), after a prompt-cache discount of {disc(k)} and {disc(c)} "
        "on LLM cost. The discount is inflated by the benchmark itself, which repeats the same questions at every size (see the note "
        "at the end), so read billed costs with caution.",
        f"<b>Time.</b> {k['latency_mean']:.1f}s per question with CROW vs {c['latency_mean']:.1f}s (p95 {k['latency_p95']:.0f}s vs {c['latency_p95']:.0f}s).",
    ]
    ci, ki, kc = c["growth"]["LLM input tokens"], k["growth"]["LLM input tokens"], k["growth"]["classifier input tokens"]
    out.append(f"<b>Growth.</b> Per note added to the wiki, classic LLM input grows by {ci[1]:+,.1f} tokens per question (r² {ci[2]:.2f}), "
               f"CROW LLM input by {ki[1]:+,.1f} (r² {ki[2]:.2f}) and CROW classifier input by {kc[1]:+,.1f} (r² {kc[2]:.2f}).")
    out.append(f"<b>Fallbacks.</b> CROW handed {k['fallback_rate']:.0%} of the questions to LLM navigation because no folder or note "
               "cleared its threshold; those questions cost as much as classic ones.")
    return out


def _document(path: Path, meta: dict[str, Any], stats: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, alignment=0, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.2, leading=12.5, textColor=colors.HexColor(INK))
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor(INK2))
    head = ParagraphStyle("head", parent=small, fontName="Helvetica-Bold", textColor=colors.HexColor(INK))
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=10)
    width = A4[0] - 3.2 * cm
    modes = list(stats)

    def table(data: list[list[str]], widths: list[float]) -> Table:
        t = Table([[Paragraph(c, head if i == 0 else small) for c in r] for i, r in enumerate(data)], colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(GRID)), ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(INK2)),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return t

    def chart(buf: io.BytesIO, h: float = 7.0) -> Image:
        img = Image(buf, width=width, height=h * cm)
        img.hAlign = "LEFT"
        return img

    pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    prices = meta.get("prices") or {}
    story: list[Any] = [
        Paragraph("LLM wiki retrieval benchmark: classic vs CROW", h1),
        Paragraph(f"{meta['started'][:16].replace('T', ' ')} UTC · {meta['questions']} questions asked at {len(meta['cuts'])} wiki sizes, "
                  f"rebuilt from the ingestion run <font face='Courier'>{meta['from']}</font>", small),
        Spacer(1, 8), Paragraph("Findings", h2),
        *[Paragraph(f, bullet, bulletText="•") for f in _findings(stats)],
        Paragraph("Results", h2),
        table([["", *modes], *[[label, *vals] for label, vals in _table_rows(stats)]], [width * 0.46, *[width * 0.54 / len(modes)] * len(modes)]),
        PageBreak(), Paragraph("As the wiki grows", h2),
        Paragraph("The same questions asked of the wiki as it stood at growing points of its ingestion. Dots are single questions, "
                  "lines the mean at each size.", small),
        chart(_by_size(stats, rows, "Right note found (hit rate)", None, "hit_rate", pct, percent=True)),
        chart(_by_size(stats, rows, "LLM tokens per question (in + out)", lambda r: r["llm"]["input_tokens"] + r["llm"]["output_tokens"], "llm_tokens", _num)),
    ]
    if "crow" in stats:
        story.append(chart(_by_size(stats, rows, "CROW: classifier tokens per question",
                                    lambda r: r["classifier"]["input_tokens"] + r["classifier"]["output_tokens"], "classifier_tokens", _num, ["crow"])))
    story += [
        chart(_by_size(stats, rows, "Latency per question (seconds)", SERIES["latency (s)"], "latency", lambda v: f"{v:.0f}s")),
        chart(_by_size(stats, rows, "Cost per question at list price (no prompt cache)", lambda r: list_cost(r, prices), "list_cost",
                       lambda v: f"${v:.4f}")),
        chart(_by_size(stats, rows, "Cost per question billed (after prompt cache)", SERIES["cost (USD)"], "cost", lambda v: f"${v:.4f}")),
        chart(_by_size(stats, rows, "Prompt-cache discount on LLM cost", None, "cache_discount", pct, percent=True)),
        PageBreak(), Paragraph("Which steps grow", h2),
        Paragraph("Slope of each step's tokens against the notes in the wiki: the steps that list folders or notes to a model grow with it.", small),
        chart(_steps(stats), 1.2 + 0.55 * max(len(s["growth_by_step"]) for s in stats.values())),
        Paragraph("By wiki size", h2),
    ]
    for m in modes:
        story.append(Paragraph(m, head))
        story.append(table([["notes", "folders", "hit", "cited", "LLM tok", "clf tok", "latency", "cost list", "cost billed", "cache", "fallback"]] + [
            [str(c["notes"]), str(c["folders"]), pct(c["hit_rate"]), pct(c["cited_rate"]), f"{c['llm_tokens']:,.0f}", f"{c['classifier_tokens']:,.0f}",
             f"{c['latency']:.1f}s", f"${c.get('list_cost', 0):.4f}", f"${c['cost']:.4f}", pct(c.get("cache_discount")), pct(c["fallback_rate"])]
            for c in stats[m]["by_cut"]], [width / 11] * 11))
        story.append(Spacer(1, 6))
    names = list(stats[modes[0]]["growth"])
    story += [Paragraph("Growth fits (start · slope per note · r²)", h2),
              table([["series", *modes]] + [[name, *[bench._fit_cell(stats[m]["growth"][name]) for m in modes]] for name in names],
                    [width * 0.3, *[width * 0.7 / len(modes)] * len(modes)]),
              Paragraph("How it was measured", h2)]
    story += [Paragraph(t, bullet, bulletText="•") for t in [
        "The two wikis of an ingestion run were copied; at each cut of the ingestion order each wiki keeps only the notes created up to "
        "that point (folders left empty are dropped, indexes rebuilt), which is the wiki as it stood then.",
        "Questions were written by the LLM from documents that both wikis had filed before the first cut, so every question is answerable "
        "at every size and its correct note is known: 'hit' means the Researcher read that note, 'cited' that the answer cites it.",
        "Classic answers by LLM navigation of the folder tree; CROW routes with the classifier and falls back to LLM navigation when "
        "nothing clears its thresholds. Both write the answer with the same LLM.",
        f"LLM {meta['llm']['model']}; classifier {meta['classifier']['model']}; {meta.get('workers', 4)} questions in flight at a time.",
        "<b>Prompt caching.</b> " + CACHE_NOTE,
    ]]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                      title="LLM wiki retrieval benchmark").build(story)
