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

"""PDF report of a benchmark run (extra `pdf`): headline numbers, growth curves, per-step analysis, method.

    okf-wiki bench --report bench-runs/<run> --pdf

Everything, including the findings text, is computed from the run's saved rows.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from okf_wiki import bench

# Colour follows the entity: classic and CROW keep theirs on every chart (validated for colour-blind safety).
MODE = {"classic": "#2a78d6", "crow": "#eb6834"}
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e4de", "#fcfcfb"


def write_pdf(out: Path) -> Path:
    """Write out/report.pdf from out/run.json and out/rows.jsonl; returns its path.

    LLM and classifier tokens are never added together: they are priced about ten times apart.
    """
    saved, rows = bench._load_run(out), bench.load_rows(out)
    if saved is None or not rows:
        raise ValueError(f"no saved benchmark in {out}")
    llm_p = bench._price(saved["prices"]["llm"])
    clf_p = bench._price(saved["prices"]["classifier"]) or bench.JEV_PRICE
    rows = {m: sorted(r, key=lambda x: x["index"]) for m, r in rows.items() if r}
    stats = {m: bench.summarize(r, llm_p, clf_p) for m, r in rows.items()}
    path = out / "report.pdf"
    _document(path, saved, rows, stats, llm_p, clf_p)
    return path


# -- charts (matplotlib) -------------------------------------------------------------------------


def _style() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"], "font.size": 8.5,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.6, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlecolor": INK,
        "axes.titlelocation": "left", "legend.frameon": False, "figure.facecolor": "white", "axes.facecolor": SURFACE,
    })
    return plt


def _figure(width: float = 7.0, height: float = 2.7) -> Any:
    return _style().subplots(figsize=(width, height))


def _png(fig: Any) -> io.BytesIO:
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=200)
    plt.close(fig)
    buf.seek(0)
    return buf


def _cumulative(rows: dict[str, list[dict[str, Any]]], value: Callable[[dict[str, Any]], float], title: str, fmt: Callable[[float], str]) -> io.BytesIO:
    fig, ax = _figure()
    for mode, rs in rows.items():
        xs, ys, total = [], [], 0.0
        for n, r in enumerate(rs, 1):
            total += value(r)
            xs.append(n)
            ys.append(total)
        ax.plot(xs, ys, color=MODE.get(mode, INK2), linewidth=2, label=mode)
        ax.annotate(f"{mode} {fmt(total)}", (xs[-1], ys[-1]), xytext=(4, 0), textcoords="offset points", va="center", color=INK, fontsize=8)
    ax.set(title=title, xlabel="notes catalogued")
    ax.yaxis.set_major_formatter(lambda v, _: fmt(v))
    ax.legend(loc="upper left")
    ax.margins(x=0.12)
    ax.set_xlim(left=0)
    return _png(fig)


def _scatter(rows: dict[str, list[dict[str, Any]]], series: str, title: str, modes: list[str] | None = None) -> io.BytesIO:
    fig, ax = _figure()
    f = bench.SERIES[series]
    shown = [m for m in (modes or list(rows)) if m in rows]
    for mode in shown:
        ok = [r for r in rows[mode] if r["action"] != "error"]
        xs, ys = [r["notes_before"] for r in ok], [f(r) for r in ok]
        color = MODE.get(mode, INK2)
        ax.scatter(xs, ys, s=16, color=color, alpha=0.55, edgecolors=SURFACE, linewidths=0.6, label=mode if len(shown) > 1 else None)
        a, b, r2 = bench.fit(xs, ys)
        if xs:
            x1 = max(xs)
            ax.plot([0, x1], [a, a + b * x1], color=color, linewidth=2)
            ax.annotate(f"{b:+,.1f} per note (r² {r2:.2f})", (x1, a + b * x1), xytext=(4, 0), textcoords="offset points",
                        va="center", color=INK, fontsize=8)
    ax.set(title=title, xlabel="notes already saved when the ingest started")
    ax.set_ylim(bottom=0)
    ax.margins(x=0.22)
    ax.set_xlim(left=-1)
    ax.yaxis.set_major_formatter(lambda v, _: _num(v))
    if len(shown) > 1:
        ax.legend(loc="upper left")
    return _png(fig)


def _steps(stats: dict[str, dict[str, Any]]) -> io.BytesIO:
    modes = list(stats)
    steps_max = max(len(stats[m]["growth_by_step"]) for m in modes)
    fig, axes = _style().subplots(1, len(modes), figsize=(7.0, 1.1 + 0.3 * steps_max), squeeze=False)
    fig.suptitle("Tokens added per note already saved, by step", x=0.01, ha="left", fontsize=10, fontweight="bold", color=INK)
    for ax, mode in zip(axes[0], modes):
        steps = sorted(stats[mode]["growth_by_step"].items(), key=lambda kv: kv[1][1])
        names = [s.split(":", 1)[1].replace("librarian/", "") + ("  (classifier)" if s.startswith("classifier") else "") for s, _ in steps]
        slopes = [g[1] for _, g in steps]
        ax.barh(names, slopes, color=MODE.get(mode, INK2), height=0.55)
        ax.axvline(0, color=INK2, linewidth=0.8)
        for y, v in enumerate(slopes):
            ax.annotate(f"{v:+,.0f}", (v, y), xytext=(3 if v >= 0 else -3, 0), textcoords="offset points",
                        ha="left" if v >= 0 else "right", va="center", fontsize=7.5, color=INK)
        ax.set_title(mode, loc="left")
        ax.grid(axis="y", visible=False)
        ax.margins(x=0.25)
    return _png(fig)


# -- the document (reportlab) ----------------------------------------------------------------------


def _document(path: Path, saved: dict[str, Any], rows: dict[str, list[dict[str, Any]]], stats: dict[str, dict[str, Any]],
              llm_p: bench.Price | None, clf_p: bench.Price) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, alignment=0, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor(INK))
    body = ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.2, leading=12.5, textColor=colors.HexColor(INK))
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor(INK2))
    head_style = ParagraphStyle("head", parent=small, fontName="Helvetica-Bold", textColor=colors.HexColor(INK))
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=10, bulletIndent=0)
    width = A4[0] - 3.2 * cm

    def table(data: list[list[str]], widths: list[float], head: bool = True) -> Table:
        cells = [[Paragraph(c, head_style if head and i == 0 else small) for c in r] for i, r in enumerate(data)]
        t = Table(cells, colWidths=widths, repeatRows=1 if head else 0)
        style = [("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(GRID)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if head:
            style += [("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(INK2))]
        t.setStyle(TableStyle(style))
        return t

    def chart(buf: io.BytesIO, height: float = 7.0) -> Image:
        img = Image(buf, width=width, height=height * cm)
        img.hAlign = "LEFT"
        return img

    modes = list(stats)
    c, k = stats.get("classic"), stats.get("crow")
    llm = saved["config"]["llm"]
    order = f"random order (seed {saved['seed']})" if saved.get("seed") is not None else "alphabetical order"
    started = datetime.fromisoformat(saved["started"]).strftime("%Y-%m-%d %H:%M UTC")
    story: list[Any] = [
        Paragraph("LLM wiki benchmark: classic vs CROW", h1),
        Paragraph(f"{started} · {min(len(r) for r in rows.values())} files per mode from <font face='Courier'>{saved['data']}</font>, "
                  f"{order} · each file catalogued into both wikis at the same time", small),
        Spacer(1, 10),
    ]
    if c and k:
        story.append(_tiles(c, k, width))
        story.append(Spacer(1, 8))
    story += [Paragraph("Findings", h2)]
    story += [Paragraph(f, bullet, bulletText="•") for f in _findings(stats, llm_p, clf_p)]
    story += [Paragraph("Results", h2)]
    story.append(table([["", *modes], *[[label, *vals] for label, vals in bench._table_rows(stats)]], [width * 0.44, *[width * 0.56 / len(modes)] * len(modes)]))
    story += [PageBreak(), Paragraph("How consumption grows with the wiki", h2),
              Paragraph("Cumulative totals as notes are catalogued, then each ingest placed by how many notes the wiki already held. "
                        "Lines on the scatter charts are least-squares fits; the label is the slope (extra tokens or seconds per note already saved).", small)]
    tokens = bench.SERIES["LLM tokens (in + out)"]
    cost = lambda r: r["llm"]["cost_usd"] + r["classifier"]["cost_usd"]  # noqa: E731
    story += [chart(_cumulative(rows, tokens, "Cumulative LLM tokens (in + out)", _num)),
              chart(_cumulative(rows, cost, "Cumulative cost reported by the provider", lambda v: f"${v:,.3f}")),
              chart(_scatter(rows, "LLM input tokens", "LLM input tokens per ingest"))]
    if "crow" in rows:
        story.append(chart(_scatter(rows, "classifier input tokens", "CROW: classifier input tokens per ingest", ["crow"])))
    story += [chart(_scatter(rows, "LLM output tokens", "LLM output tokens per ingest")),
              chart(_scatter(rows, "latency (s)", "Latency per ingest (seconds)")),
              PageBreak(), Paragraph("Which steps grow", h2),
              Paragraph("Slope of each step's tokens (in + out) against the notes already saved. Steps that list existing notes "
                        "to the model grow with the wiki; steps that read only the new source stay flat.", small),
              chart(_steps(stats), 1.2 + 0.55 * max(len(s["growth_by_step"]) for s in stats.values())),
              Paragraph("Growth fits", h2)]
    fits = [["series", *[f"{m}: start · slope · r²" for m in modes]]]
    fits += [[name, *[bench._fit_cell(stats[m]["growth"][name]) for m in modes]] for name in bench.SERIES]
    story.append(table(fits, [width * 0.3, *[width * 0.7 / len(modes)] * len(modes)]))
    if llm_p:
        story += [Paragraph("Projection (indicative)", h2),
                  Paragraph("Cost per ingest extrapolated from the linear fits above to larger wikis. A straight line through "
                            f"{min(len(r) for r in rows.values())} ingests is a rough guide, not a forecast.", small)]
        proj = [["notes already saved", *modes]] + [[f"{n:,}", *[f"${_projected(stats[m], n, llm_p, clf_p):.4f}" for m in modes]] for n in (100, 500, 1000, 5000)]
        story.append(table(proj, [width * 0.3, *[width * 0.7 / len(modes)] * len(modes)]))
    story += [PageBreak(), Paragraph("Setup", h2)]
    provider = (llm.get("extra_body") or {}).get("provider", {}).get("order")
    crow = saved["config"].get("crow", {})
    setup = [
        ["LLM", f"{llm['model']} · temperature {llm['temperature']}" + (f" · providers {', '.join(provider)}" if provider else "")],
        ["classifier (CROW)", f"{saved['config']['classifier']['model']} via {saved['config']['classifier']['base_url']}"],
        ["CROW thresholds", ", ".join(f"{k2} {v}" for k2, v in crow.items())],
        ["wiki", f"summarize {saved['config'].get('summarize')} · max depth {saved['config'].get('max_depth')} · max links {saved['config'].get('max_links')}"],
        ["prices per 1M tokens", f"LLM {_pm(llm_p)} · classifier {_pm(clf_p)} (list prices; the reported cost is what the provider billed)"],
        ["data", f"{saved['data']} · {order}"],
    ]
    story.append(table(setup, [width * 0.25, width * 0.75], head=False))
    story += [Paragraph("How to read it", h2)] + [Paragraph(t, bullet, bulletText="•") for t in _method(rows, saved)]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                      title="LLM wiki benchmark", author="okf-wiki bench").build(story)


def _tiles(c: dict[str, Any], k: dict[str, Any], width: float) -> Any:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    big = ParagraphStyle("big", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=colors.HexColor(INK))
    cap = ParagraphStyle("cap", fontName="Helvetica", fontSize=7.8, leading=10, textColor=colors.HexColor(INK2))
    per = lambda s: (s["llm_cost_usd"] + s["classifier_cost_usd"]) / max(s["ingests"], 1)  # noqa: E731
    tok = lambda s: (s["llm_input_tokens"] + s["llm_output_tokens"]) / max(s["ingests"], 1)  # noqa: E731
    cells = [
        (_delta_pct(per(k), per(c)), f"cost per ingest with CROW<br/>${per(k):.4f} vs ${per(c):.4f} classic"),
        (f"{c['latency_mean'] / max(k['latency_mean'], 1e-9):.1f}×", f"faster per ingest with CROW<br/>{k['latency_mean']:.1f}s vs {c['latency_mean']:.1f}s mean"),
        (_delta_pct(tok(k), tok(c)), f"LLM tokens per ingest with CROW<br/>{tok(k):,.0f} vs {tok(c):,.0f} classic"),
        (f"{k['folders_created']} vs {c['folders_created']}", "folders created<br/>CROW vs classic"),
    ]
    t = Table([[Paragraph(v, big) for v, _ in cells], [Paragraph(t, cap) for _, t in cells]], colWidths=[width / 4] * 4)
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1.2, colors.HexColor(INK)), ("TOPPADDING", (0, 0), (-1, 0), 6)]))
    return t


def _findings(stats: dict[str, dict[str, Any]], llm_p: bench.Price | None, clf_p: bench.Price) -> list[str]:
    c, k = stats.get("classic"), stats.get("crow")
    if not (c and k):
        return [f"Only one mode in this run ({', '.join(stats)}): the comparison needs both classic and crow."]
    per = lambda s: (s["llm_cost_usd"] + s["classifier_cost_usd"]) / max(s["ingests"], 1)  # noqa: E731
    out = [
        f"<b>Cost.</b> CROW spent ${per(k):.4f} per ingest against ${per(c):.4f} for classic ({_delta_pct(per(k), per(c))}), "
        f"of which the classifier is ${k['classifier_cost_usd'] / max(k['ingests'], 1):.5f}.",
        f"<b>Time.</b> Mean latency per ingest {k['latency_mean']:.1f}s (CROW) vs {c['latency_mean']:.1f}s (classic); "
        f"p95 {k['latency_p95']:.0f}s vs {c['latency_p95']:.0f}s. A classifier call took {k['classifier_seconds'] / max(k['classifier_calls'], 1):.2f}s on average, "
        f"an LLM call {k['llm_seconds'] / max(k['llm_calls'], 1):.1f}s.",
    ]
    ci, ki, kc = c["growth"]["LLM input tokens"], k["growth"]["LLM input tokens"], k["growth"]["classifier input tokens"]
    out.append(f"<b>Growth.</b> Classic LLM input grows by {ci[1]:+,.0f} tokens per note already saved (r² {ci[2]:.2f}); "
               f"CROW LLM input {ki[1]:+,.0f} (r² {ki[2]:.2f}). In CROW the growth moves to the classifier: {kc[1]:+,.0f} tokens per note "
               f"(r² {kc[2]:.2f}), i.e. ${kc[1] * 1000 * clf_p[0]:.4f} more per ingest for every 1,000 notes saved.")
    if llm_p:
        extra = lambda s: (s["growth"]["LLM input tokens"][1] * llm_p[0] + s["growth"]["LLM output tokens"][1] * llm_p[1]) * 1000  # noqa: E731
        out.append(f"<b>At scale.</b> Every 1,000 notes already in the wiki add about ${extra(c):.4f} of LLM cost per ingest in classic "
                   f"and ${extra(k):.4f} in CROW (from the fitted slopes).")
    for mode, s in stats.items():
        top = [(st, g) for st, g in sorted(s["growth_by_step"].items(), key=lambda kv: -kv[1][1]) if g[1] > 0 and g[2] >= 0.3][:2]
        if top:
            steps = " and ".join(f"{st.split(':', 1)[1].replace('librarian/', '')} ({g[1]:+,.0f}/note, r² {g[2]:.2f})" for st, g in top)
            out.append(f"<b>{mode.capitalize()}: what grows.</b> {steps}.")
    out.append(f"<b>Structure.</b> CROW created {k['folders_created']} folders and merged {k['merged']} notes; "
               f"classic created {c['folders_created']} folders and merged {c['merged']}.")
    return out


def _method(rows: dict[str, list[dict[str, Any]]], saved: dict[str, Any]) -> list[str]:
    n = min(len(r) for r in rows.values())
    lines = [
        "Each file is catalogued into the classic wiki and the CROW wiki at the same time; the next file starts when both are done, "
        "so both wikis always hold the same files.",
        "Tokens, model time and cost are measured per call and split into the LLM ledger (writing) and the classifier ledger (deciding). "
        "Reported cost is what OpenRouter billed; list price is tokens × the published price.",
        "The growth analysis places each ingest by the number of notes the wiki already held. Low r² means the fitted slope explains "
        "little of the variation (source length dominates); read those slopes as trends.",
        f"This run covers {n} ingests per mode: early ingests (empty wiki, new folders) weigh a lot on the fits.",
    ]
    if saved.get("seed") is None:
        lines.append("Files were taken in alphabetical order, so topics arrived in blocks (folder by folder); later runs shuffle the order.")
    return lines


def _projected(s: dict[str, Any], n: int, llm_p: bench.Price, clf_p: bench.Price) -> float:
    at = lambda name: max(0.0, s["growth"][name][0] + s["growth"][name][1] * n)  # noqa: E731
    return at("LLM input tokens") * llm_p[0] + at("LLM output tokens") * llm_p[1] + at("classifier input tokens") * clf_p[0]


def _delta_pct(new: float, old: float) -> str:
    return "n/a" if not old else f"{(new - old) / old * 100:+.0f}%"


def _pm(p: bench.Price | None) -> str:
    return "n/a" if p is None else f"${p[0] * 1e6:.3f} in / ${p[1] * 1e6:.3f} out"


def _num(v: float) -> str:
    return f"{v / 1e6:.1f}M" if abs(v) >= 1e6 else f"{v / 1e3:.0f}k" if abs(v) >= 1e4 else f"{v / 1e3:.1f}k" if abs(v) >= 1e3 else f"{v:.0f}"
