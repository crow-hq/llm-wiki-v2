# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Office and OpenDocument readers: what each format gives, and that hostile or broken files end as InputError."""

from __future__ import annotations

import ast
import io
import zipfile
from pathlib import Path

import pytest

from llmw2.bundle import formats
from llmw2.bundle.files import SUFFIXES, extract_text
from llmw2.errors import InputError
from tests.wiki.office_files import ODF, docx, odf, ods_table, p, pack, pptx, tbl, xlsx


def test_docx_becomes_markdown_with_its_headings_lists_tables_and_footnotes() -> None:
    # ARRANGE
    body = (
        p("Refund policy", "Title")
        + p("Scope", "Heading1")
        + p("Refunds within 30 days.")
        + p("Digital goods", num=(1, 0))
        + p("Open a ticket", num=(2, 0))
        + tbl([["Region", "Days"], ["EU", "30"]])
        + '<w:p><w:r><w:t>See note</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>'
    )

    # ACT
    text = extract_text(docx(body, footnotes={1: "Unless the law says otherwise."}), "policy.docx")

    # ASSERT
    assert text == (
        "# Refund policy\n\n# Scope\n\nRefunds within 30 days.\n\n- Digital goods\n\n1. Open a ticket\n\n"
        "| Region | Days |\n| --- | --- |\n| EU | 30 |\n\nSee note[^f1]\n\n[^f1]: Unless the law says otherwise."
    )


def test_docx_text_box_paragraphs_and_breaks_stay_apart_and_fallback_copies_are_dropped() -> None:
    # ARRANGE: a text box (two paragraphs) saved twice, as Word does, and a line break in a run
    box = "<w:txbxContent>" + p("2014") + p("Table 2") + "</w:txbxContent>"
    body = (
        "<w:p><w:r><mc:AlternateContent><mc:Choice>" + box + "</mc:Choice><mc:Fallback>" + box + "</mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>" + "<w:p><w:r><w:t>one</w:t><w:br/><w:t>two</w:t></w:r></w:p>"
    )

    # ACT
    text = formats.markdown(docx(body))

    # ASSERT
    assert text == "2014\nTable 2\n\none\ntwo"


def test_docx_rows_inside_content_controls_are_read() -> None:
    body = "<w:tbl><w:sdt><w:sdtContent><w:tr><w:tc>" + p("inside") + "</w:tc></w:tr></w:sdtContent></w:sdt></w:tbl>"
    assert formats.markdown(docx(body)) == "| inside |\n| --- |"


def test_pptx_gives_a_heading_per_slide_with_its_title_levels_and_speaker_notes() -> None:
    data = pptx([("Q3 review", [("Up 12%", 0), ("EU leads", 1)], "The EU number is unaudited.")])
    assert formats.markdown(data) == "## Q3 review (slide 1)\n\nUp 12%\n\n  - EU leads\n\nNotes: The EU number is unaudited."


def test_xlsx_sheets_give_values_as_shown_with_dates_and_whole_numbers() -> None:
    # ARRANGE: 45000 is 2023-03-15 in the 1900 system
    data = xlsx({"Prices": [["Item", "Price", "Since"], ["Coffee", 2.5, ("date", 45000)], ["Tea", 2.0, None]], "Empty": []})

    # ACT
    sheets = formats.sheets(data)

    # ASSERT
    assert sheets == [("Prices", [["Item", "Price", "Since"], ["Coffee", "2.5", "2023-03-15"], ["Tea", "2", ""]])]


def test_xlsx_1904_dates_count_from_1904() -> None:
    assert formats.sheets(xlsx({"S": [[("date", 0)]]}, date1904=True)) == [("S", [["1904-01-01"]])]


def test_odt_headings_lists_numbered_headings_tables_and_notes() -> None:
    body = (
        '<text:h text:outline-level="1">Plan</text:h>'
        "<text:p>Il<text:span> comune</text:span><text:s text:c=\"2\"/>decide.<text:note><text:note-body>"
        "<text:p>Art. 4</text:p></text:note-body></text:note></text:p>"
        "<text:list><text:list-item><text:p>first</text:p><text:list><text:list-item><text:p>inner</text:p>"
        "</text:list-item></text:list></text:list-item>"
        '<text:list-item><text:h text:outline-level="2">Numbered heading</text:h></text:list-item></text:list>'
        + ods_table("T", [["a", "b"]])
    )
    assert formats.markdown(odf(body)) == (
        "# Plan\n\nIl comune  decide.[^n1]\n\n- first\n\n  - inner\n\n## Numbered heading\n\n| a | b |\n| --- |\n\n[^n1]: Art. 4"
    ).replace("| a | b |\n| --- |", "| a | b |\n| --- | --- |")


def test_ods_repeated_rows_are_one_row_with_their_count() -> None:
    data = odf(ods_table("Sheet1", [["Name", "Qty"], ["x", "1"]], repeat={1: 1_048_575}), "spreadsheet")
    assert formats.sheets(data) == [("Sheet1", [["Name", "Qty"], ["x (row repeated 1048575 times)", "1"]])]


def test_the_type_comes_from_the_content_not_the_name() -> None:
    data = xlsx({"S": [["right reader"]]})
    assert formats.kind(data) == "xlsx"
    assert extract_text(data, "misnamed.ods").startswith('Sheet "S" of misnamed.ods: 1 rows')


def test_an_odf_file_without_mimetype_is_told_by_its_body() -> None:
    body = "<office:body><office:text><text:p>hi</text:p></office:text></office:body>"
    content = f"<office:document-content {ODF}>{body}</office:document-content>"
    assert formats.markdown(pack({"content.xml": content})) == "hi"


LAUGHS = "".join(f'<!ENTITY {chr(98 + i)} "' + f"&{chr(97 + i)};" * 10 + '">' for i in range(7))


def _broken_deflate() -> bytes:
    """A valid zip directory over corrupt compressed data: zlib fails, not zipfile."""
    good = bytearray(docx(p("hello " * 200)))
    start = good.index(b"word/document.xml") + len(b"word/document.xml")
    good[start + 10 : start + 40] = bytes(30)
    return bytes(good)


HOSTILE = {
    "not a zip": (lambda: b"%PDF-1.4", "broken zip"),
    "truncated": (lambda: docx(p("hello"))[:60], "broken zip"),
    "empty package": (lambda: pack({}), "not a Word"),
    "zip bomb": (lambda: pack({"word/document.xml": "<w:document>" + "a" * 70_000_000 + "</w:document>"}), "too large to read"),
    "malformed": (lambda: pack({"word/document.xml": "<w:document><w:body>"}), "ParseError"),
    "billion laughs": (lambda: pack({"word/document.xml": f'<!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">{LAUGHS}]><x>&h;</x>'}), "document type"),
    "external entity": (lambda: pack({"word/document.xml": '<!DOCTYPE d [<!ENTITY e SYSTEM "file:///x">]><x>&e;</x>'}), "document type"),
    "broken deflate": (_broken_deflate, "cannot read"),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_or_broken_files_end_as_input_error(name: str) -> None:
    make, message = HOSTILE[name]
    with pytest.raises(InputError, match=message):
        formats.markdown(make())


def test_a_bad_style_index_or_shared_string_index_does_not_fail_the_file() -> None:
    sheet = '<row r="1"><c r="A1" s=""><v>3</v></c><c r="B1" t="s"><v>99</v></c><c r="C1" t="s"><v>x</v></c></row>'
    data = xlsx({"S": [["seed"]]})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    parts["xl/worksheets/sheet1.xml"] = f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{sheet}</sheetData></worksheet>'.encode()
    assert formats.sheets(pack(parts)) == [("S", [["3"]])]


def test_a_sparse_sheet_keeps_its_fullest_columns_within_the_cell_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE: every row has column A and one column of its own (the "diagonal" that once took 883 MB)
    monkeypatch.setattr(formats, "MAX_CELLS", 40)
    cell = '<c r="{}" t="inlineStr"><is><t>{}</t></is></c>'
    rows = "".join(f'<row r="{i}">' + cell.format(f"A{i}", "a") + cell.format(f"{_col(i)}{i}", "b") + "</row>" for i in range(1, 21))
    data = xlsx({"S": [["seed"]]})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    parts["xl/worksheets/sheet1.xml"] = f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>'.encode()

    # ACT
    [(_, kept)] = formats.sheets(pack(parts))

    # ASSERT: 20 rows x 2 columns fit in 40 cells, and column A (in every row) is one of them
    assert len(kept) == 20 and all(len(r) == 2 and r[0] == "a" for r in kept)


def _col(i: int) -> str:
    return "B" + chr(65 + i)


def test_a_document_past_max_chars_is_cut_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formats, "MAX_CHARS", 30)
    text = formats.markdown(docx(p("a" * 20) + p("b" * 20)))
    assert text.startswith("a" * 20 + "\n\n" + "b" * 8) and text.endswith("text cut at 30 characters]")


def test_sheets_refuses_a_document_and_markdown_reads_a_spreadsheet_as_tables() -> None:
    with pytest.raises(InputError, match="not a spreadsheet"):
        formats.sheets(docx(p("x")))
    assert formats.markdown(xlsx({"S": [["a", "b"], ["1", "2"]]})) == "## S\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"


def test_new_office_suffixes_are_accepted() -> None:
    assert {".docx", ".pptx", ".xlsx", ".odt", ".ods"} <= SUFFIXES


def test_formats_imports_nothing_of_llmw2_but_the_error_type() -> None:
    """The readers stay a separable unit: pure functions of the bytes."""
    tree = ast.parse(Path(formats.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert {m for m in imported if m and m.startswith("llmw2")} == {"llmw2.errors"}


def _sheet_xml(cells: str, styles: str = "") -> bytes:
    """A one-sheet workbook with these raw <c> cells in row 1, and these cellXfs/numFmts."""
    data = xlsx({"S": [["seed"]]})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    parts["xl/worksheets/sheet1.xml"] = f'<worksheet {ns}><sheetData><row r="1">{cells}</row></sheetData></worksheet>'.encode()
    if styles:
        parts["xl/styles.xml"] = f"<styleSheet {ns}>{styles}</styleSheet>".encode()
    return pack(parts)


def test_xlsx_cell_types_formulas_errors_booleans_times_and_custom_date_formats() -> None:
    # ARRANGE: style 1 a custom "dd/mm/yyyy", style 2 a custom "hh:mm", style 3 built-in time (20)
    styles = (
        '<numFmts><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/><numFmt numFmtId="165" formatCode="hh:mm"/></numFmts>'
        '<cellXfs><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/><xf numFmtId="20"/></cellXfs>'
    )
    cells = (
        '<c r="A1" t="str"><f>A2</f><v>formula text</v></c><c r="B1" t="e"><v>#DIV/0!</v></c><c r="C1" t="b"><v>1</v></c>'
        '<c r="D1" s="1"><v>45000.5</v></c><c r="E1" s="2"><v>0.75</v></c><c r="F1" s="3"><v>0.5</v></c>'
        '<c r="G1"><v>1.25</v></c><c r="H1"><v>abc</v></c><c r="I1"><f>1/0</f></c><c r="J1" s="1"><v>1e300</v></c>'
    )

    # ACT
    [(_, rows)] = formats.sheets(_sheet_xml(cells, styles))

    # ASSERT
    assert rows == [["formula text", "#DIV/0!", "1", "2023-03-15 12:00", "18:00", "12:00", "1.25", "abc", "1e300"]]


def test_xlsx_cells_without_references_follow_one_another() -> None:
    [(_, rows)] = formats.sheets(_sheet_xml('<c t="inlineStr"><is><t>a</t></is></c><c t="inlineStr"><is><t>b</t></is></c>'))
    assert rows == [["a", "b"]]


def test_pptx_tables_and_slides_without_a_title() -> None:
    data = pptx([("", [("body only", 0)], "")])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    slide = parts["ppt/slides/slide1.xml"].decode()
    grid = "<a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>Region</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl>"
    frame = f"<p:graphicFrame><a:graphic><a:graphicData>{grid}</a:graphicData></a:graphic></p:graphicFrame>"
    parts["ppt/slides/slide1.xml"] = slide.replace("</p:spTree>", frame + "</p:spTree>").encode()
    assert formats.markdown(pack(parts)) == "## Slide 1\n\nbody only\n\n| Region |\n| --- |"


def test_odt_sections_tables_of_contents_and_page_breaks() -> None:
    body = (
        '<text:section><text:p>in a section<text:soft-page-break/></text:p></text:section>'
        "<text:table-of-content><text:index-body><text:p>Contents line</text:p></text:index-body></text:table-of-content>"
        '<text:p>tab<text:tab/>and<text:line-break/>break<text:bookmark-start text:name="x"/></text:p><text:p/>'
    )
    assert formats.markdown(odf(body)) == "in a section\n\ntab\tand\nbreak"  # the table of contents is left out


def test_a_spreadsheet_read_as_markdown_stops_at_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formats, "MAX_CHARS", 20)
    text = formats.markdown(xlsx({"A": [["x" * 30]], "B": [["y"]]}))
    assert text.endswith("text cut at 20 characters]") and "## B" not in text


def test_word_headings_beyond_six_and_list_styles_and_numbering_without_a_format() -> None:
    styles = (
        f"<w:styles {W_NS}>"
        '<w:style w:styleId="H9"><w:name w:val="heading 9"/></w:style>'
        '<w:style w:styleId="Bullets"><w:name w:val="List Bullet"/><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        "</w:styles>"
    )
    data = docx(p("Deep", "H9") + p("from the style", "Bullets") + p("not a list", num=(0, 0)))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    parts["word/styles.xml"] = styles.encode()
    assert formats.markdown(pack(parts)) == "###### Deep\n\n- from the style\n\nnot a list"


W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def test_lowercase_cell_references_are_read() -> None:
    [(_, rows)] = formats.sheets(_sheet_xml('<c r="c1" t="inlineStr"><is><t>z</t></is></c>'))
    assert rows == [["z"]]


def test_xlsx_numbers_keep_15_digits_whole_ones_all_and_percentages_show_as_percentages_to_12() -> None:
    # ARRANGE: styles 1 and 3 built-in percentages (9, 10), 2 a custom "0.0%", 4 a quoted "%" that is only text
    styles = (
        '<numFmts><numFmt numFmtId="164" formatCode="0.0%"/><numFmt numFmtId="165" formatCode="&quot;%&quot;0"/></numFmts>'
        '<cellXfs><xf numFmtId="0"/><xf numFmtId="9"/><xf numFmtId="164"/><xf numFmtId="10"/><xf numFmtId="165"/></cellXfs>'
    )
    cells = (
        '<c r="A1"><v>5.5100000000000003E-2</v></c><c r="B1" s="1"><v>0.25</v></c><c r="C1" s="2"><v>5.5100000000000003E-2</v></c>'
        '<c r="D1" s="3"><v>0.33333333333333331</v></c><c r="E1" s="4"><v>7</v></c><c r="F1"><v>1.0000000000000002</v></c>'
        '<c r="G1"><v>24.614316111720201</v></c><c r="H1"><v>2026101612345678</v></c><c r="I1"><v>1.5E+22</v></c>'
    )

    # ACT
    [(_, rows)] = formats.sheets(_sheet_xml(cells, styles))

    # ASSERT
    assert rows == [["0.0551", "25%", "5.51%", "33.3333333333%", "7", "1", "24.6143161117202", "2026101612345678", "1.5e+22"]]


def _slide_shapes(data: bytes, shapes: dict[int, str]) -> bytes:
    """The deck with these placeholder shapes, (type, text) as XML, added to the slides numbered in `shapes`."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    for i, extra in shapes.items():
        slide = parts[f"ppt/slides/slide{i}.xml"].decode()
        parts[f"ppt/slides/slide{i}.xml"] = slide.replace("</p:spTree>", extra + "</p:spTree>").encode()
    return pack(parts)


def _ph(kind: str, text: str) -> str:
    ph = f'<p:nvSpPr><p:nvPr><p:ph type="{kind}"/></p:nvPr></p:nvSpPr>'
    return f"<p:sp>{ph}<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"


def test_pptx_titles_lead_the_heading_repeats_are_not_repeated_and_placeholders_are_left_out() -> None:
    # ARRANGE: the third slide has no title, so the fourth still repeats the first two
    titles = [("Results", "first"), ("Results", "second"), ("", "untitled"), ("Results", "third"), ("Summary", "done")]
    deck = pptx([(title, [(text, 0)], "") for title, text in titles])
    furniture = _ph("sldNum", "7") + _ph("dt", "16/10/2026") + _ph("ftr", "ACME confidential")
    data = _slide_shapes(deck, {1: furniture, 2: furniture, 5: _ph("ftr", "Draft")})

    # ACT
    text = formats.markdown(data)

    # ASSERT
    assert text == (
        "## Results (slide 1)\n\nfirst\n\n(slide 2)\n\nsecond\n\n## Slide 3\n\nuntitled\n\n(slide 4)\n\nthird\n\n"
        "## Summary (slide 5)\n\ndone\n\nFooter: ACME confidential / Draft"
    )


def test_docx_tables_of_contents_are_left_out_and_fill_in_lines_become_a_blank() -> None:
    # ARRANGE: a "toc 1" paragraph, a contents content control, a building block nested in a plain one, a plain one
    toc = '<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/></w:docPartObj></w:sdtPr><w:sdtContent>'
    nested = '<w:sdt><w:sdtContent><w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Cover Pages"/></w:docPartObj></w:sdtPr>'
    body = (
        p("Introduction\t\t3", "TOC1")
        + toc + p("Scope ........ 4") + "</w:sdtContent></w:sdt>"
        + nested + "<w:sdtContent>" + p("cover") + "</w:sdtContent></w:sdt></w:sdtContent></w:sdt>"
        + "<w:sdt><w:sdtContent>" + p("kept") + "</w:sdtContent></w:sdt>"
        + p("Name: ________ Date: .........")
        + p("a\t\t\tb")
        + tbl([["Signature", "----------"]])
    )
    data = docx(body)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    parts["word/styles.xml"] = f'<w:styles {W_NS}><w:style w:styleId="TOC1"><w:name w:val="toc 1"/></w:style></w:styles>'.encode()

    # ACT
    text = formats.markdown(pack(parts))

    # ASSERT
    assert text == "kept\n\nName: ___ Date: ___\n\na\tb\n\n| Signature | ___ |\n| --- | --- |"


def test_odt_headings_over_80_characters_or_with_a_blank_are_paragraphs_and_fill_in_lines_become_a_blank() -> None:
    body = (
        f'<text:h text:outline-level="1">{"A" * 80}</text:h>'
        f'<text:h text:outline-level="1">{"B" * 81}</text:h>'
        '<text:h text:outline-level="2">Nome ________</text:h>'
        "<text:p>Firma …………</text:p>"
        + ods_table("T", [["Data", "........"]])
    )
    assert formats.markdown(odf(body)) == f"# {'A' * 80}\n\n{'B' * 81}\n\nNome ___\n\nFirma ___\n\n| Data | ___ |\n| --- | --- |"
