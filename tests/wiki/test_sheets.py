# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Spreadsheets: one note per sheet, the data kept as CSV, and the rows a question needs given back to the researcher."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from llmw2 import CLASSIC, Candidate, InputError, Item, LocalFolderSource, Origin, Source, StepContext, Wiki, WikiConfig, sync
from llmw2.agents.librarian import IngestResult
from llmw2.agents.state import source_state
from llmw2.bundle.files import sheet_sources
from llmw2.bundle.sheets import BRIEF_ROWS, brief, parse, rows_for, sheet_text
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
from tests.wiki.office_files import docx, odf, ods_table, p, xlsx

# -- the sheet source: text, brief, rows for a question ------------------------------------------------------------


def test_a_sheet_text_round_trips_its_rows_even_with_commas_quotes_and_backticks() -> None:
    # ARRANGE
    rows = [["Name", "Note"], ["Rossi, Mario", 'said "ok"'], ["x", "```code```"]]

    # ACT
    text = sheet_text("plan.xlsx", "Budget", rows)

    # ASSERT
    assert text.startswith('Sheet "Budget" of plan.xlsx: 3 rows, 2 columns.\n')
    assert parse(text) == ("Budget", rows)


def test_parse_and_brief_leave_any_other_text_alone() -> None:
    assert parse("just text") is None
    assert brief("just text") == "just text"
    assert rows_for("q", "just text") == ""


def test_brief_keeps_the_header_and_the_first_rows_of_a_long_sheet() -> None:
    # ARRANGE
    rows = [["n"], *([str(i)] for i in range(100))]
    text = sheet_text("a.xlsx", "S", rows)

    # ACT
    short = brief(text)

    # ASSERT
    assert short.startswith('Sheet "S" of a.xlsx: 101 rows, 1 columns.\n')
    assert f"\n{BRIEF_ROWS - 1}\n" in short and f"\n{BRIEF_ROWS}\n" not in short
    assert short.endswith(f"[… {100 - BRIEF_ROWS} more rows]\n")
    assert brief(sheet_text("a.xlsx", "S", rows[:5])) == sheet_text("a.xlsx", "S", rows[:5])


def test_title_rows_above_the_header_become_a_caption_line_and_the_empty_columns_go() -> None:
    # ARRANGE: two one-cell rows above a header of three, a column empty below them
    rows = [["Table 4", "", "", ""], ["Sales by region", "", "", ""], ["Region", "Q1", "", "Q2"], ["EU", "4", "", "5"]]

    # ACT
    text = sheet_text("a.xlsx", "S", rows)

    # ASSERT
    assert text == 'Sheet "S" of a.xlsx: 2 rows, 3 columns.\nCaption: Table 4 — Sales by region\n\n```csv\nRegion,Q1,Q2\nEU,4,5\n```\n'
    assert parse(text) == ("S", [["Region", "Q1", "Q2"], ["EU", "4", "5"]])


@pytest.mark.parametrize("rows", [[["Note", ""], ["a", "b"]], [["n"], ["1"], ["2"]], [["only"]]], ids=["narrow", "one column", "one cell"])
def test_rows_are_a_caption_only_above_a_row_of_three_cells(rows: list[list[str]]) -> None:
    assert "Caption:" not in sheet_text("a.xlsx", "S", rows)
    assert parse(sheet_text("a.xlsx", "S", rows)) == ("S", rows)


def test_at_most_five_rows_make_a_caption() -> None:
    rows = [[f"t{i}", "", ""] for i in range(6)] + [["a", "b", "c"], ["1", "2", "3"]]
    text = sheet_text("a.xlsx", "S", rows)
    assert text.startswith('Sheet "S" of a.xlsx: 3 rows, 3 columns.\nCaption: t0 — t1 — t2 — t3 — t4\n')
    assert parse(text) == ("S", [["t5", "", ""], ["a", "b", "c"], ["1", "2", "3"]])  # the sixth stays a row


def test_a_caption_stays_on_one_line_and_the_brief_keeps_it() -> None:
    # ARRANGE: a title cell over lines, as a merged title often is, above a long sheet
    rows = [["Report\n\n2026  final"], ["a", "b", "c"], *([str(i), "x", "y"] for i in range(100))]

    # ACT
    short = brief(sheet_text("a.xlsx", "S", rows))

    # ASSERT
    assert short.startswith('Sheet "S" of a.xlsx: 101 rows, 3 columns.\nCaption: Report 2026 final\n\nThe first 20 rows, under the header:')


def test_a_sheet_with_a_caption_keeps_its_name() -> None:
    [(name, text)] = sheet_sources(xlsx({"Sales": [["Table 4"], ["Region", "Q1", "Q2"], ["EU", 4, 5]]}), "r.xlsx")
    assert name == "Sales" and text.startswith('Sheet "Sales" of r.xlsx: 2 rows, 3 columns.\nCaption: Table 4\n')


def test_rows_for_gives_the_whole_sheet_when_it_fits() -> None:
    text = sheet_text("a.xlsx", "S", [["Region", "Sales"], ["EU", "4"]])
    assert rows_for("anything", text) == 'Rows of the sheet "S" (all 1):\n\n```csv\nRegion,Sales\nEU,4\n```'


def test_rows_for_a_big_sheet_gives_the_closest_rows_and_column_totals() -> None:
    # ARRANGE: 400 rows, one of them about Bologna
    rows = [["Città", "Importo"], *([f"città {i}", "1.000,50"] for i in range(400))]
    rows[251] = ["Bologna", "2.000,00"]
    text = sheet_text("a.ods", "Spese", rows)

    # ACT
    block = rows_for("Quanto ha speso Bologna?", text, budget=2_000)

    # ASSERT
    assert block.startswith('Rows of the sheet "Spese": ') and "of 400, those closest to the question." in block
    assert "Bologna,\"2.000,00\"" in block
    assert "- Importo: 400 values, sum 401,199.5, min 1,000.5, max 2,000" in block
    assert len(block) < 2_000


@pytest.mark.parametrize(
    ("cell", "value"),
    [
        ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("12%", 12.0), ("€ 3", 3.0),
        ("1234", 1234.0), ("1.000.000", 1e6), ("3,5", 3.5), ("1,000", 1000.0),
    ],
)
def test_numbers_are_read_in_italian_and_english_forms(cell: str, value: float) -> None:
    from llmw2.bundle.sheets import _value

    assert _value(cell) == value


def test_a_sheet_brief_has_no_headings_so_routing_reads_its_first_rows() -> None:
    text = brief(sheet_text("a.xlsx", "S", [["k"], ["v"]]))
    assert "Sheet \"S\"" in source_state("a", text, 6000)


# -- one note per sheet -------------------------------------------------------------------------------------------


def _summarize(ctx: StepContext, source: Source) -> Candidate:
    """Title from the sheet name, and the text it was given as body: the test sees what the librarian read."""
    sheet = parse(source.text) or (source.text.split('"')[1] if source.text.startswith("Sheet") else "misc", [])
    name = sheet[0].lower().replace(" ", "")
    return Candidate(f"About sheet-{name}", f"The {name} sheet.", ["sheet"], source.text)


def make_wiki(bundle: Path) -> Wiki:
    steps = replace(CLASSIC, name="sheets-test", summarize=_summarize)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=ContentLLM(UsageTracker()), steps=steps)
    wiki.init()
    return wiki


def workbook(**sheets: int) -> bytes:
    """A workbook whose sheets have `n` data rows each."""
    return xlsx({name: [["Code", "Amount"], *([f"{name}-{i}", i] for i in range(n))] for name, n in sheets.items()})


def test_ingest_file_files_one_note_per_sheet_linked_and_returns_the_others_as_parts(tmp_path: Path) -> None:
    # ARRANGE
    wiki = make_wiki(tmp_path / "w")

    # ACT
    data = xlsx({"North": [["Code"], ["n-1"]], "South": [["Code"], ["s-1"]], "Empty": []})
    result = wiki.ingest_file(data, "sales.xlsx")

    # ASSERT
    assert [r.title for r in [result, *result.parts]] == ["About sheet-north", "About sheet-south"]
    first, second = (wiki.store.read(r.note) for r in [result, *result.parts])
    assert second.rel.rsplit("/", 1)[-1] in first.body and first.rel.rsplit("/", 1)[-1] in second.body  # See also both ways
    raw = wiki.store.read(first.frontmatter["sources"][0]["resource"])
    assert parse(raw.body) == ("North", [["Code"], ["n-1"]])
    assert result.to_dict()["parts"][0]["title"] == "About sheet-south"


def test_the_librarian_reads_a_long_sheet_by_its_first_rows_and_the_raw_copy_keeps_them_all(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    result = wiki.ingest_file(workbook(Big=200), "big.xlsx")
    note = wiki.store.read(result.note)
    raw = wiki.store.read(note.frontmatter["sources"][0]["resource"])
    assert "[… 180 more rows]" in note.body  # the candidate's body is what summarize was given
    assert len(parse(raw.body)[1]) == 201  # type: ignore[index]


def test_a_spreadsheet_that_cannot_be_read_is_an_input_error(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    with pytest.raises(InputError):
        wiki.ingest_file(b"not a zip", "broken.xlsx")
    with pytest.raises(InputError, match="empty"):
        wiki.ingest_file(xlsx({"S": []}), "empty.xlsx")
    report = wiki.ingest_many([Item("broken.ods", data=b"nope", origin=Origin("t", "a", "broken.ods"))])
    assert isinstance(report.results[0][1], InputError)


def test_ingest_many_spreads_a_workbook_over_sheet_items_with_their_own_origin_keys(tmp_path: Path) -> None:
    # ARRANGE
    wiki = make_wiki(tmp_path / "w")
    origin = Origin("test", "acct", "plan.xlsx", version="v1")

    # ACT
    report = wiki.ingest_many([Item("plan.xlsx", fetch=lambda: workbook(A=1, B=1), origin=origin), Item("x.md", text="topic: x-1\nhi")])

    # ASSERT
    assert sorted(i.filename for i, _ in report.results) == ["plan, A.csv", "plan, B.csv", "x.md"]
    assert wiki.store.origin_keys("test:acct:plan.xlsx#") == {"test:acct:plan.xlsx#A", "test:acct:plan.xlsx#B"}
    assert wiki.origin("test:acct:plan.xlsx") is None  # nothing under the file's own key: a sync fetches it again


def test_a_sheet_gone_from_the_workbook_is_marked_removed_and_a_removed_file_marks_all_its_sheets(tmp_path: Path) -> None:
    # ARRANGE
    wiki = make_wiki(tmp_path / "w")
    origin = Origin("test", "acct", "plan.xlsx", version="v1")
    wiki.ingest_file(workbook(A=1, B=1), "plan.xlsx", origin=origin)

    # ACT: B is deleted from the workbook
    wiki.ingest_file(workbook(A=1), "plan.xlsx", origin=replace(origin, version="v2"))

    # ASSERT
    record_b = wiki.origin("test:acct:plan.xlsx#B")
    assert record_b is not None and record_b.removed
    assert not wiki.origin("test:acct:plan.xlsx#A").removed  # type: ignore[union-attr]
    wiki.mark_removed("test:acct:plan.xlsx")
    assert wiki.origin("test:acct:plan.xlsx#A").removed  # type: ignore[union-attr]


def test_sync_of_a_folder_files_each_sheet_skips_an_unchanged_workbook_and_ignores_office_lock_files(tmp_path: Path) -> None:
    # ARRANGE
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "plan.xlsx").write_bytes(workbook(A=1, B=2))
    (folder / "~$plan.xlsx").write_bytes(b"lock")
    (folder / "brief.docx").write_bytes(docx(p("topic: brief-1")))
    (folder / "table.ods").write_bytes(odf(ods_table("Only", [["k", "v"], ["a", "1"]]), "spreadsheet"))
    wiki = make_wiki(tmp_path / "w")

    # ACT
    first = sync(wiki, LocalFolderSource(folder))
    again = sync(wiki, LocalFolderSource(folder))

    # ASSERT
    assert (first.created, first.errors) == (4, [])
    assert (again.created, again.skipped, again.errors) == (0, 0, [])  # nothing changed: nothing listed as a change


def test_the_researcher_gives_a_sheet_note_the_rows_of_its_raw_copy_and_leaves_other_notes_alone(tmp_path: Path) -> None:
    # ARRANGE
    wiki = make_wiki(tmp_path / "w")
    sheet = wiki.ingest_file(workbook(North=3), "sales.xlsx")
    other = wiki.ingest_file(docx(p("topic: memo-1") + p("plain words")), "memo.docx")
    notes = [wiki.store.read(sheet.note), wiki.store.read(other.note)]

    # ACT
    given = wiki.researcher()._with_rows("How much in North-2?", notes)

    # ASSERT
    assert given[0].body.endswith('Rows of the sheet "North" (all 3):\n\n```csv\nCode,Amount\nNorth-0,0\nNorth-1,1\nNorth-2,2\n```')
    assert "# See also" not in given[0].body.split("Rows of the sheet")[1]
    assert given[1] is notes[1]
    assert wiki.store.read(sheet.note).body == notes[0].body  # the file itself is untouched


def test_the_researcher_skips_sources_it_cannot_read(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    result = wiki.ingest_file(workbook(N=1), "s.xlsx")
    note = wiki.store.read(result.note)
    note.frontmatter["sources"] = [{"resource": "/raw/gone.md"}, "not a dict", {"resource": "elsewhere.md"}]
    assert wiki.researcher()._with_rows("q", [note])[0] is note


def test_ingest_file_of_a_single_sheet_returns_its_result_with_no_parts(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    result = wiki.ingest_file(workbook(Solo=2), "solo.xlsx")
    assert isinstance(result, IngestResult) and result.parts == [] and result.title == "About sheet-solo"


def test_sheet_notes_are_linked_in_a_chain_besides_what_relate_adds_and_a_dotted_sheet_name_keeps_its_dot(tmp_path: Path) -> None:
    # ARRANGE
    wiki = make_wiki(tmp_path / "w")
    data = xlsx({"v1.1": [["k"], ["a"]], "v1.2": [["k"], ["b"]], "v1.3": [["k"], ["c"]]})

    # ACT
    result = wiki.ingest_file(data, "plan.xlsx")

    # ASSERT
    first, middle, last = (wiki.store.read(r.note) for r in [result, *result.parts])
    def links(a: object, b: object) -> bool:  # b's file named in a's See-also
        return "](" + b.rel.rsplit("/", 1)[-1] + ")" in a.body.split("# See also")[-1]  # type: ignore[attr-defined]

    assert links(first, middle) and links(middle, first) and links(middle, last) and links(last, middle)
    raw = wiki.store.read(first.frontmatter["sources"][0]["resource"])
    assert raw.frontmatter["title"] == "plan, v1.1"


def test_the_researcher_reads_only_the_latest_copy_of_a_sheet_and_skips_a_removed_one(tmp_path: Path) -> None:
    # ARRANGE: the same sheet filed twice (a new version merges into its note), then removed at its source
    wiki = make_wiki(tmp_path / "w")
    origin = Origin("test", "acct", "s.xlsx", version="v1")
    wiki.ingest_file(xlsx({"N": [["Code"], ["old"]]}), "s.xlsx", origin=origin)
    result = wiki.ingest_file(xlsx({"N": [["Code"], ["new"]]}), "s.xlsx", origin=replace(origin, version="v2"))
    note = wiki.store.read(result.note)

    # ACT
    given = wiki.researcher()._with_rows("code?", [note])[0]
    wiki.mark_removed("test:acct:s.xlsx")
    after = wiki.researcher()._with_rows("code?", [wiki.store.read(result.note)])[0]

    # ASSERT
    assert len(note.frontmatter["sources"]) == 2
    assert "Code\nnew\n" in given.body and "old" not in given.body.split("Rows of the sheet")[1]
    assert "Rows of the sheet" not in after.body


def test_mark_removed_reaches_sheets_only_for_a_spreadsheet(tmp_path: Path) -> None:
    # ARRANGE: a text file "notes" and another file "notes#2.md" whose key starts with "notes#"
    wiki = make_wiki(tmp_path / "w")
    wiki.ingest_file(b"topic: a-1\nfirst", "notes", origin=Origin("t", "a", "notes"))
    wiki.ingest_file(b"topic: b-1\nsecond", "notes#2.md", origin=Origin("t", "a", "notes#2.md"))

    # ACT
    wiki.mark_removed("t:a:notes")

    # ASSERT
    assert wiki.origin("t:a:notes").removed  # type: ignore[union-attr]
    assert not wiki.origin("t:a:notes#2.md").removed  # type: ignore[union-attr]
