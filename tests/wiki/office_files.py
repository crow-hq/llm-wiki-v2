# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Small Office and OpenDocument files built in memory for the tests: only the parts the readers look at."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
WMC = W + ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
PRES = (
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
SS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
ODF = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
)


def pack(parts: dict[str, str | bytes], mimetype: str | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if mimetype:
            z.writestr("mimetype", mimetype, compress_type=zipfile.ZIP_STORED)
        for name, data in parts.items():
            z.writestr(name, data)
    return out.getvalue()


def p(text: str, style: str | None = None, num: tuple[int, int] | None = None) -> str:
    """A Word paragraph: its style id, and its list (numId, level)."""
    ppr = ""
    if style or num:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "")
        ppr += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>' if num else ""
        ppr += "</w:pPr>"
    return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"


def tbl(rows: list[list[str]]) -> str:
    return "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows) + "</w:tbl>"


STYLES = (
    f"<w:styles {W}>"
    '<w:style w:styleId="Title"><w:name w:val="Title"/></w:style>'
    '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
    '<w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>'
    "</w:styles>"
)
NUMBERING = (
    f"<w:numbering {W}>"
    '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
    '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
    "</w:numbering>"
)


def docx(body: str, *, footnotes: dict[int, str] | None = None) -> bytes:
    parts: dict[str, str | bytes] = {
        "word/document.xml": f"<w:document {WMC}><w:body>{body}</w:body></w:document>",
        "word/styles.xml": STYLES,
        "word/numbering.xml": NUMBERING,
    }
    if footnotes:
        notes = '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:t>—</w:t></w:r></w:p></w:footnote>'
        notes += "".join(f'<w:footnote w:id="{i}">{p(t)}</w:footnote>' for i, t in footnotes.items())
        parts["word/footnotes.xml"] = f"<w:footnotes {W}>{notes}</w:footnotes>"
    return pack(parts)


def pptx(slides: list[tuple[str, list[tuple[str, int]], str]]) -> bytes:
    """Slides of (title, [(text, level)], speaker notes)."""
    parts: dict[str, str | bytes] = {}
    ids, rels = "", ""
    for i, (title, lines, notes) in enumerate(slides, 1):
        paras = "".join(f'<a:p><a:pPr lvl="{lvl}"/><a:r><a:t>{escape(t)}</a:t></a:r></a:p>' for t, lvl in lines)
        parts[f"ppt/slides/slide{i}.xml"] = (
            f"<p:sld {PRES}><p:cSld><p:spTree>"
            '<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody><a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>"
            f"<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody>{paras}</p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>"
        )
        if notes:
            parts[f"ppt/notesSlides/notesSlide{i}.xml"] = (
                f"<p:notes {PRES}><p:cSld><p:spTree>"
                '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
                f"<p:txBody><a:p><a:r><a:t>{escape(notes)}</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:notes>"
            )
            parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = (
                f'<Relationships {RELS}><Relationship Id="rN" Type="notesSlide" Target="../notesSlides/notesSlide{i}.xml"/></Relationships>'
            )
        ids += f'<p:sldId id="{255 + i}" r:id="rId{i}"/>'
        rels += f'<Relationship Id="rId{i}" Type="slide" Target="slides/slide{i}.xml"/>'
    parts["ppt/presentation.xml"] = f"<p:presentation {PRES}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>"
    parts["ppt/_rels/presentation.xml.rels"] = f"<Relationships {RELS}>{rels}</Relationships>"
    return pack(parts)


def xlsx(sheets: dict[str, list[list[object]]], *, date1904: bool = False) -> bytes:
    """Sheets of rows: str cells are shared strings, numbers are numbers, ("date", serial) is a date-formatted number."""
    shared: list[str] = []
    parts: dict[str, str | bytes] = {}
    entries, rels = "", ""
    for i, (name, rows) in enumerate(sheets.items(), 1):
        xml_rows = ""
        for r, row in enumerate(rows, 1):
            cells = ""
            for c, value in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if isinstance(value, tuple):
                    cells += f'<c r="{ref}" s="1"><v>{value[1]}</v></c>'
                elif isinstance(value, str):
                    shared.append(value)
                    cells += f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>'
                elif value is not None:
                    cells += f'<c r="{ref}"><v>{value}</v></c>'
            xml_rows += f'<row r="{r}">{cells}</row>'
        parts[f"xl/worksheets/sheet{i}.xml"] = f"<worksheet {SS}><sheetData>{xml_rows}</sheetData></worksheet>"
        entries += f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        rels += f'<Relationship Id="rId{i}" Type="worksheet" Target="worksheets/sheet{i}.xml"/>'
    pr = '<workbookPr date1904="1"/>' if date1904 else ""
    parts["xl/workbook.xml"] = f"<workbook {SS}>{pr}<sheets>{entries}</sheets></workbook>"
    parts["xl/_rels/workbook.xml.rels"] = f"<Relationships {RELS}>{rels}</Relationships>"
    parts["xl/sharedStrings.xml"] = f"<sst {SS}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>"
    parts["xl/styles.xml"] = f'<styleSheet {SS}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'
    return pack(parts)


def odf(body: str, kind: str = "text") -> bytes:
    content = f"<office:document-content {ODF}><office:body><office:{kind}>{body}</office:{kind}></office:body></office:document-content>"
    return pack({"content.xml": content}, f"application/vnd.oasis.opendocument.{kind}")


def ods_table(name: str, rows: list[list[str]], repeat: dict[int, int] | None = None) -> str:
    """A table:table; `repeat` maps a row index to its number-rows-repeated."""
    out = ""
    for i, row in enumerate(rows):
        attr = f' table:number-rows-repeated="{repeat[i]}"' if repeat and i in repeat else ""
        cells = "".join(f"<table:table-cell><text:p>{escape(c)}</text:p></table:table-cell>" for c in row)
        out += f"<table:table-row{attr}>{cells}</table:table-row>"
    return f'<table:table table:name="{escape(name)}">{out}</table:table>'
