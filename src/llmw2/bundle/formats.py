# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Office and OpenDocument files read with the standard library: documents as Markdown, spreadsheets as rows.

DOCX, PPTX and ODT become Markdown with their headings, lists, tables, footnotes and speaker notes; XLSX and ODS
become their sheets, each a list of rows of the values as shown (dates as dates, percentages as percentages, no
binary float noise). Charts, SmartArt, headers and footers, image alt text, tables of contents and slide number
and date placeholders are left out; fill-in lines ("______") become "___". The type is read from the content.

The files come from anywhere, so every read is bounded: an XML part may not inflate past MAX_PART, the text stops
at MAX_CHARS and says so, a sheet keeps at most MAX_CELLS cells, and any failure is an InputError, so that one bad
file never stops a batch. Pure functions of the bytes: this module imports nothing of llmw2 but the error type.
"""

from __future__ import annotations

import datetime
import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from typing import TypeVar

from llmw2.errors import InputError

MAX_PART = 64 * 2**20  # bytes of one XML part once inflated
MAX_RATIO = 100  # inflated / compressed, past which a part over 1 MB is taken for a zip bomb
MAX_CHARS = 20_000_000  # characters of Markdown out of one document
MAX_CELLS = 2_000_000  # cells of one sheet (rows x used columns): past it the sparsest columns are left out
MAX_REPEAT = 1024  # copies of one repeated ODF cell

DOCUMENTS = ("docx", "pptx", "odt")
SPREADSHEETS = ("xlsx", "ods")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_FILL = re.compile(r"_{4,}|\.{5,}|…{3,}|-{6,}")  # a line to fill in, or the dots of a contents line
MAX_HEADING = 80  # characters of an ODT heading: a longer one is a paragraph styled as a heading
# Character references XML 1.0 forbids (&#1; …): some generators write them, and expat would refuse the whole part.
_BAD_REF = re.compile(rb"&#(?:[xX]0*(?:[0-8bBcCeEfF]|1[0-9a-fA-F])|0*(?:[0-8]|1[124-9]|2[0-9]|3[01]));")

Rows = list[list[str]]
T = TypeVar("T")


class _Full(Exception):
    """The Markdown reached MAX_CHARS."""


class _Out:
    """The Markdown being written, in blocks, stopping at MAX_CHARS."""

    def __init__(self) -> None:
        self.blocks: list[str] = []
        self.size = 0
        self.cut = False

    def add(self, text: str) -> None:
        if not text:
            return
        if self.size + len(text) > MAX_CHARS:
            self.blocks.append(text[: max(MAX_CHARS - self.size, 0)])
            self.cut = True
            raise _Full
        self.blocks.append(text)
        self.size += len(text) + 2

    def text(self) -> str:
        cut = f"\n\n[… the document goes on: text cut at {MAX_CHARS:,} characters]" if self.cut else ""
        return "\n\n".join(self.blocks) + cut


def kind(data: bytes) -> str:
    """"docx", "pptx", "xlsx", "odt" or "ods", from the parts the package holds; InputError for anything else."""
    return _reading(lambda: _kind(_open(data)))


def markdown(data: bytes) -> str:
    """A DOCX, PPTX or ODT as Markdown (a spreadsheet as one table per sheet); InputError when it cannot be read."""

    def read() -> str:
        z = _open(data)
        k = _kind(z)
        out = _Out()
        try:
            if k in SPREADSHEETS:
                for name, rows in _sheets(z, k):
                    if t := table(rows):
                        out.add(f"## {name}\n\n{t}")
            else:
                {"docx": _docx, "pptx": _pptx, "odt": _odt}[k](z, out)
        except _Full:
            pass
        return out.text()

    return _reading(read)


def sheets(data: bytes) -> list[tuple[str, Rows]]:
    """The sheets of an XLSX or ODS that hold anything: (name, rows), values as shown; InputError otherwise."""

    def read() -> list[tuple[str, Rows]]:
        z = _open(data)
        k = _kind(z)
        if k not in SPREADSHEETS:
            raise InputError("not a spreadsheet")
        return [(name, rows) for name, rows in _sheets(z, k) if rows]

    return _reading(read)


def table(rows: Rows) -> str:
    """Rows as a Markdown table, the first one as its header; rows and columns with nothing in them left out."""
    rows = [[(c or "").replace("|", r"\|").replace("\n", " ").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    used = [i for i in range(max(map(len, rows))) if any(i < len(r) and r[i] for r in rows)]
    rows = [[r[i] if i < len(r) else "" for i in used] for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * len(used)]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in rows[1:]])


# -- reading untrusted packages -----------------------------------------------------------------------------------


def _reading(read: Callable[[], T]) -> T:
    """Run a reader: whatever goes wrong in it is an InputError (a file can be broken in any way)."""
    try:
        return read()
    except InputError:
        raise
    except Exception as e:  # zipfile, zlib, expat, a missing part, a bad number: all the file's fault
        raise InputError(f"cannot read the document: {type(e).__name__}: {str(e)[:120]}") from e


def _open(data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as e:
        raise InputError(f"not a valid document (broken zip): {e}") from e


def _kind(z: zipfile.ZipFile) -> str:
    names = set(z.namelist())
    for part, k in (("word/document.xml", "docx"), ("ppt/presentation.xml", "pptx"), ("xl/workbook.xml", "xlsx")):
        if part in names:
            return k
    if "content.xml" in names:  # OpenDocument: the body says which (the mimetype entry may be missing or wrong)
        body = _xml(z, "content.xml").find(OFFICE + "body")
        if body is not None and body.find(OFFICE + "spreadsheet") is not None:
            return "ods"
        if body is not None and body.find(OFFICE + "text") is not None:
            return "odt"
    raise InputError("not a Word, PowerPoint, Excel or OpenDocument text or spreadsheet file")


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    """A part parsed within limits, with forbidden character references and Word's fallback copies removed."""
    info = z.getinfo(name)
    if info.file_size > MAX_PART or (info.file_size > 2**20 and info.file_size > MAX_RATIO * max(info.compress_size, 1)):
        raise InputError(f"{name} inflates to {info.file_size:,} bytes: too large to read")
    raw = z.read(name)
    if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw[:4096]:  # never in Office or ODF parts: entity tricks
        raise InputError(f"{name} declares a document type: not an Office or OpenDocument part")
    root = ET.fromstring(_BAD_REF.sub(b"", raw))
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == MC_FALLBACK:  # the same text box or drawing, saved again for old readers
                parent.remove(child)
    return root


def _clean(text: str) -> str:
    """Fill-in lines as "___", runs of tabs as one."""
    return re.sub(r"\t{2,}", "\t", _FILL.sub("___", text))


def _num(value: str | None, default: int = 0) -> int:
    try:
        return int(value or "")
    except ValueError:
        return default


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relationship id -> part name, for the internal relationships of `part`."""
    folder, name = posixpath.split(part)
    rels = f"{folder}/_rels/{name}.rels"
    if rels not in z.namelist():
        return {}
    out = {}
    for r in _xml(z, rels):
        target = r.get("Target") or ""
        if target and r.get("TargetMode") != "External":
            out[r.get("Id") or ""] = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
    return out


# -- DOCX ---------------------------------------------------------------------------------------------------------


def _wtext(el: ET.Element) -> str:
    """A paragraph's text: runs, tabs, breaks, note marks; the paragraphs of a text box on lines of their own."""
    out = []
    for t in el.iter():
        if t.tag == W + "t":
            out.append(t.text or "")
        elif t.tag == W + "tab":
            out.append("\t")
        elif t.tag in (W + "br", W + "cr") or (t.tag == W + "p" and t is not el):
            out.append("\n")
        elif t.tag in (W + "footnoteReference", W + "endnoteReference"):
            out.append(f"[^{t.tag[len(W)]}{t.get(W + 'id')}]")
    return "".join(out).strip()


def _kids(el: ET.Element, tag: str) -> Iterator[ET.Element]:
    """Children with this tag, looking through content controls and custom XML."""
    for c in el:
        if c.tag == tag:
            yield c
        elif c.tag == W + "customXml":
            yield from _kids(c, tag)
        elif c.tag == W + "sdt" and (content := c.find(W + "sdtContent")) is not None:
            yield from _kids(content, tag)


def _contents(sdt: ET.Element, content: ET.Element) -> bool:
    """A content control holding a table of contents, or a building block nested in it."""
    gallery = sdt.find(f"{W}sdtPr/{W}docPartObj/{W}docPartGallery")
    return content.find(f".//{W}docPartGallery") is not None or (gallery is not None and "Contents" in (gallery.get(W + "val") or ""))


def _docx(z: zipfile.ZipFile, out: _Out) -> None:
    names = set(z.namelist())
    styles: dict[str | None, str] = {}
    style_list: dict[str | None, ET.Element | None] = {}
    if "word/styles.xml" in names:
        for s in _xml(z, "word/styles.xml").iter(W + "style"):
            n = s.find(W + "name")
            styles[s.get(W + "styleId")] = (n.get(W + "val") or "") if n is not None else ""
            style_list[s.get(W + "styleId")] = s.find(f"{W}pPr/{W}numPr")
    numbered: set[tuple[str | None, str | None]] = set()  # (numId, level) shown as numbers rather than bullets
    if "word/numbering.xml" in names:
        numbering = _xml(z, "word/numbering.xml")
        abstract = {a.get(W + "abstractNumId"): a for a in numbering.iter(W + "abstractNum")}
        for n in numbering.iter(W + "num"):
            ref = n.find(W + "abstractNumId")
            a = abstract.get(ref.get(W + "val")) if ref is not None else None
            for lvl in a.iter(W + "lvl") if a is not None else []:
                fmt = lvl.find(W + "numFmt")
                if fmt is not None and fmt.get(W + "val") not in ("bullet", "none"):
                    numbered.add((n.get(W + "numId"), lvl.get(W + "ilvl")))

    def para(p: ET.Element) -> None:
        text = _wtext(p)
        if not text:
            return
        sid_el = p.find(f"{W}pPr/{W}pStyle")
        sid = sid_el.get(W + "val") if sid_el is not None else None
        style = styles.get(sid, "").lower()
        if style.startswith("toc") or style == "table of figures":  # the contents, not the content
            return
        text = _clean(text)
        if m := re.match(r"heading (\d)", style):
            return out.add("#" * min(_num(m.group(1), 1), 6) + " " + text)
        if style == "title":
            return out.add("# " + text)
        npr = p.find(f"{W}pPr/{W}numPr")
        npr = npr if npr is not None else style_list.get(sid)
        if npr is not None:
            nid_el, level_el = npr.find(W + "numId"), npr.find(W + "ilvl")
            nid = nid_el.get(W + "val") if nid_el is not None else None
            level = level_el.get(W + "val") if level_el is not None else "0"
            if nid != "0":
                mark = "1. " if (nid, level) in numbered else "- "
                return out.add("  " * min(_num(level), 8) + mark + text)
        out.add(text)

    def block(parent: ET.Element) -> None:
        for el in parent:
            if el.tag == W + "p":
                para(el)
            elif el.tag == W + "tbl":
                rows = [[" ".join(_wtext(p) for p in tc.iter(W + "p")).strip() for tc in _kids(tr, W + "tc")] for tr in _kids(el, W + "tr")]
                out.add(table([[_clean(c) for c in r] for r in rows]))
            elif el.tag == W + "sdt" and (content := el.find(W + "sdtContent")) is not None and not _contents(el, content):
                block(content)

    body = _xml(z, "word/document.xml").find(W + "body")
    if body is not None:
        block(body)
    for part, k in (("word/footnotes.xml", "footnote"), ("word/endnotes.xml", "endnote")):
        for note in _xml(z, part).iter(W + k) if part in names else []:
            text = " ".join(_wtext(p) for p in note.iter(W + "p")).strip()
            if note.get(W + "type") in (None, "normal") and text:
                out.add(f"[^{k[0]}{note.get(W + 'id')}]: {text}")


# -- PPTX ---------------------------------------------------------------------------------------------------------


def _atext(p: ET.Element) -> str:
    return "".join(r.text or "" for r in p.iter(A + "t")).strip()


def _pptx(z: zipfile.ZipFile, out: _Out) -> None:
    """A heading per slide, "## Title (slide N)", so that a deck's own summary slide reads as one; a title repeated
    from the slide before as "(slide N)" alone; slide numbers and dates left out, the footers once at the end."""
    names = set(z.namelist())
    order = _rels(z, "ppt/presentation.xml")
    slides = [order.get(s.get(R + "id") or "") for s in _xml(z, "ppt/presentation.xml").iter(P + "sldId")]
    previous, footers = "", []
    for i, part in enumerate((s for s in slides if s in names), 1):
        assert part is not None
        title, lines = "", []
        for el in _xml(z, part).iter():
            if el.tag == P + "sp":
                ph = el.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                kind = ph.get("type") if ph is not None else None
                if kind in ("sldNum", "dt"):
                    continue
                paras = []
                for p in el.iter(A + "p"):
                    if t := _atext(p):
                        ppr = p.find(A + "pPr")
                        level = min(_num(ppr.get("lvl")) if ppr is not None else 0, 8)
                        paras.append("  " * level + "- " + t if level else t)
                if kind == "ftr":
                    footers += [x for x in paras if x not in footers]
                elif kind in ("title", "ctrTitle") and paras and not title:
                    title = " ".join(paras)
                else:
                    lines += paras
            elif el.tag == A + "tbl":
                rows = [[" ".join(_atext(p) for p in c.iter(A + "p")).strip() for c in tr.findall(A + "tc")] for tr in el.findall(A + "tr")]
                lines.append(table(rows))
        for target in _rels(z, part).values():
            if "notesSlide" in target and target in names:
                notes = []
                for sp in _xml(z, target).iter(P + "sp"):
                    ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                    if ph is not None and ph.get("type") == "body":
                        notes += [_atext(p) for p in sp.iter(A + "p")]
                if text := " ".join(n for n in notes if n):
                    lines.append("Notes: " + text)
        head = f"## {title} (slide {i})" if title else f"## Slide {i}"
        if title and title == previous:
            head = f"(slide {i})"
        previous = title or previous
        out.add("\n\n".join([head, *lines]))
    if footers:
        out.add("Footer: " + " / ".join(footers))


# -- XLSX ---------------------------------------------------------------------------------------------------------


def _date_styles(z: zipfile.ZipFile) -> dict[int, str]:
    """Cell style index -> "date", "time" or "percent", for the styles that show a number as one."""
    if "xl/styles.xml" not in z.namelist():
        return {}
    st = _xml(z, "xl/styles.xml")
    kinds = {i: "date" for i in (14, 15, 16, 17, 22)} | {i: "time" for i in (18, 19, 20, 21, 45, 46, 47)} | {9: "percent", 10: "percent"}
    for f in st.iter(S + "numFmt"):
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", f.get("formatCode", "")).lower()
        if "%" in code:
            kinds[_num(f.get("numFmtId"), -1)] = "percent"
        elif "y" in code or "d" in code or ("m" in code and "h" not in code and "s" not in code):
            kinds[_num(f.get("numFmtId"), -1)] = "date"
        elif "h" in code or "s" in code:
            kinds[_num(f.get("numFmtId"), -1)] = "time"
    xfs = st.find(S + "cellXfs")
    styles = xfs.findall(S + "xf") if xfs is not None else []
    return {i: kinds[_num(x.get("numFmtId"))] for i, x in enumerate(styles) if _num(x.get("numFmtId")) in kinds}


def _cell(c: ET.Element, shared: list[str], dates: dict[int, str], epoch: datetime.datetime) -> str:
    """A cell's value as Excel shows it, near enough: text, whole numbers in full without ".0", other numbers to 15
    significant digits (not "5.5100000000000003E-2"), percentages as "5.51%", dates as ISO dates."""
    inline = c.find(S + "is")
    if inline is not None:
        return "".join(x.text or "" for x in inline.iter(S + "t"))
    v, t = c.find(S + "v"), c.get("t")
    if v is None or v.text is None:
        return ""
    if t == "s":
        i = _num(v.text, -1)
        return shared[i] if 0 <= i < len(shared) else ""
    if t in ("str", "e", "b", "inlineStr"):
        return v.text
    try:
        f = float(v.text)
    except ValueError:
        return v.text
    style = _num(c.get("s"))
    if dates.get(style) == "percent":
        return f"{f * 100:.12g}%"  # 12 digits: x100 leaves float noise in the last ones
    if style in dates:
        try:
            d = epoch + datetime.timedelta(days=f)
        except OverflowError:
            return v.text
        if dates[style] == "time":
            return d.strftime("%H:%M")
        return d.strftime("%Y-%m-%d") if d.time() == datetime.time() else d.strftime("%Y-%m-%d %H:%M")
    return str(int(f)) if f.is_integer() and abs(f) < 1e21 else f"{f:.15g}"  # long codes and ids keep every digit


def _column(ref: str | None) -> int:
    """The 0-based column of a cell reference ("C7" -> 2), -1 when it has none."""
    m = re.match(r"[A-Z]*", (ref or "").upper())
    n = 0
    for ch in m.group(0) if m else "":
        n = n * 26 + ord(ch) - 64
    return n - 1


def _dense(sparse: list[dict[int, str]]) -> Rows:
    """Sparse rows as a table over the columns in use; a sheet too sparse for MAX_CELLS keeps its fullest columns."""
    fill: dict[int, int] = {}
    for row in sparse:
        for c in row:
            fill[c] = fill.get(c, 0) + 1
    used = sorted(fill)
    if sparse and len(used) * len(sparse) > MAX_CELLS:
        used = sorted(sorted(fill, key=lambda c: (-fill[c], c))[: max(1, MAX_CELLS // len(sparse))])
    return [[row.get(c, "") for c in used] for row in sparse]


def _xlsx(z: zipfile.ZipFile) -> list[tuple[str, Rows]]:
    names = set(z.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        shared = ["".join(t.text or "" for t in si.iter(S + "t")) for si in _xml(z, "xl/sharedStrings.xml").iter(S + "si")]
    dates = _date_styles(z)
    wb = _xml(z, "xl/workbook.xml")
    pr = wb.find(S + "workbookPr")
    epoch = datetime.datetime(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") else datetime.datetime(1899, 12, 30)
    where = _rels(z, "xl/workbook.xml")
    out = []
    for sh in wb.iter(S + "sheet"):
        part = where.get(sh.get(R + "id") or "")
        if part not in names:
            continue
        assert part is not None
        sparse = []
        for r in _xml(z, part).iter(S + "row"):
            values: dict[int, str] = {}
            nxt = 0
            for c in r.iter(S + "c"):
                col = _column(c.get("r")) if c.get("r") else nxt
                nxt = col + 1
                if (text := _cell(c, shared, dates, epoch).strip()) and 0 <= col < 16384:
                    values[col] = text
            if values:
                sparse.append(values)
        out.append((sh.get("name") or "", _dense(sparse)))
    return out


# -- ODT, ODS -----------------------------------------------------------------------------------------------------


def _otext(el: ET.Element, notes: list[str]) -> str:
    """An ODF paragraph's text: spans, links, spaces, tabs, breaks; notes go to `notes` and leave a mark."""
    out = [el.text or ""]
    for c in el:
        if c.tag == TEXT + "s":
            out.append(" " * min(_num(c.get(TEXT + "c"), 1), 64))
        elif c.tag == TEXT + "tab":
            out.append("\t")
        elif c.tag == TEXT + "line-break":
            out.append("\n")
        elif c.tag == TEXT + "note":
            notes.append("")
            i = len(notes)
            body = c.find(TEXT + "note-body")
            text = " ".join(_otext(p, notes).strip() for p in body.iter(TEXT + "p")) if body is not None else ""
            notes[i - 1] = f"[^n{i}]: {text.strip()}"
            out.append(f"[^n{i}]")
        elif not c.tag.startswith(TEXT + "bookmark") and c.tag != TEXT + "soft-page-break":
            out.append(_otext(c, notes))
        out.append(c.tail or "")
    return "".join(out)


def _orows(table_el: ET.Element, notes: list[str]) -> Rows:
    """An ODF table's rows as shown: a repeated row is one row marked with its count, not that many copies."""
    rows = []
    for r in table_el.iter(TABLE + "table-row"):
        cells: list[str] = []
        for c in r:
            if c.tag in (TABLE + "table-cell", TABLE + "covered-table-cell"):
                text = " ".join(_otext(p, notes).strip() for p in c.iter() if p.tag in (TEXT + "p", TEXT + "h")).strip()
                cells += [text] * min(_num(c.get(TABLE + "number-columns-repeated"), 1), MAX_REPEAT if text else 64)
        if any(cells):
            repeat = _num(r.get(TABLE + "number-rows-repeated"), 1)
            rows.append([f"{cells[0]} (row repeated {repeat} times)", *cells[1:]] if repeat > 1 else cells)
    return rows


def _odt(z: zipfile.ZipFile, out: _Out) -> None:
    body = _xml(z, "content.xml").find(f"{OFFICE}body/{OFFICE}text")
    notes: list[str] = []

    def text(el: ET.Element) -> str:
        return _clean(_otext(el, notes).strip())

    def block(parent: ET.Element | list[ET.Element], depth: int = 0) -> None:
        for el in parent:
            if el.tag == TEXT + "h" and (t := text(el)):
                if len(t) > MAX_HEADING or "___" in t:  # a paragraph, or a form line, styled as a heading
                    out.add(t)
                else:
                    out.add("#" * min(max(_num(el.get(TEXT + "outline-level"), 1), 1), 6) + " " + t)
            elif el.tag == TEXT + "p" and (t := text(el)):
                out.add(t)
            elif el.tag == TEXT + "list":
                for item in el.findall(TEXT + "list-item"):
                    for c in item:
                        if c.tag == TEXT + "h":  # numbered headings are list items in ODF
                            block([c], depth)
                        elif c.tag == TEXT + "p" and (t := text(c)):
                            out.add("  " * min(depth, 8) + "- " + t)
                        elif c.tag == TEXT + "list":
                            block([c], depth + 1)
            elif el.tag == TABLE + "table":
                out.add(table([[_clean(c) for c in r] for r in _orows(el, notes)]))
            elif el.tag in (TEXT + "section", TEXT + "index-body"):  # text:table-of-content and the other indexes left out
                block(el, depth)

    if body is not None:
        block(body)
    for n in notes:
        out.add(n)


def _ods(z: zipfile.ZipFile) -> list[tuple[str, Rows]]:
    body = _xml(z, "content.xml").find(f"{OFFICE}body/{OFFICE}spreadsheet")
    notes: list[str] = []
    return [(t.get(TABLE + "name") or "", _orows(t, notes)) for t in (body.findall(TABLE + "table") if body is not None else [])]


def _sheets(z: zipfile.ZipFile, k: str) -> list[tuple[str, Rows]]:
    return _xlsx(z) if k == "xlsx" else _ods(z)
