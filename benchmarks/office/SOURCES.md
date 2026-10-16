# The Office and OpenDocument bench

2,629 real DOCX, PPTX, XLSX, ODT and ODS files, used to choose and check the readers of `llmw2.bundle.formats` and
`llmw2.bundle.sheets`. The files are not in the repository (about 2 GB, and most of them belong to
third parties): only where each one comes from, its sha256 and the scripts that select it, fetch it and measure it.

## Sources

| source | files | licence | what the repository keeps |
|---|---|---|---|
| [NapierOne](https://registry.opendata.aws/napierone), the `tiny` archives of DOCX, XLSX, PPTX (100 each) and ODS (100), group `uk` | 400 | the Edinburgh Napier University License Agreement (MIT-style: keep its copyright notice) with the attribution below; each subset also has the licence and attribution of the description PDF that comes with it | the archives' URL, sha256 and size (`napierone-archives.tsv`), and per file its archive, member name and sha256 (`napierone-files.tsv`) |
| [Common Crawl](https://commoncrawl.org) `CC-MAIN-2026-39`, captures with status 200 and an Office/ODF MIME type, not truncated, on Italian hosts (`.it`, group `it`) and US ones (`.gov`, `.edu`, `.us`, group `us`) | 2,229 | none for the files: each belongs to whoever published it ([Common Crawl terms of use](https://commoncrawl.org/terms-of-use)) | per file its URL, the WARC coordinates of the capture (file, offset, length) and its sha256 (`cc-files.tsv`) |

Whoever fetches and uses NapierOne gives the attribution its licence asks for ([NapierOne](https://github.com/simonrdavies/NapierOne),
"License and Attribution"): S. Davies, R. Macfarlane, W. J. Buchanan, *NapierOne: A modern mixed file data set
alternative to Govdocs1*, Forensic Science International: Digital Investigation 40 (2022) 301330,
https://doi.org/10.1016/j.fsidi.2021.301330; and, as the registry asks, with the day of their own download:
*NapierOne Mixed File Dataset was accessed on DATE from https://registry.opendata.aws/napierone*. The four archives
match the sha256 published next to them.

In 28 URLs of `cc-files.tsv` the value of a session or access parameter of the publisher's site is replaced by
`REDACTED` (`access_token`, `idsessione`, `jsessionid`, `Signature`, `Key-Pair-Id`, `_cldee`, `esid`, `recipientid`):
the file is fetched by its WARC coordinates, not by its URL, so nothing is lost.

## Samples

`cc-files.tsv` keeps the samples in the order they were drawn, each with its seed; a later sample excludes the URLs and
hashes of the earlier ones. Per group and format, up to N files, where the crawl had that many:

| sample | seed | N | formats |
|---|---|---|---|
| `cc` | 42 | 40 | DOCX, PPTX, XLSX |
| `cc2` | 43 | 40 | DOCX, PPTX, XLSX, ODT, ODS (US ODT 30, US ODS 16) |
| `cc3` | 44 | 40 | DOCX, PPTX (Italian 13), XLSX, ODT, ODS (Italian) |
| `cc4` | 45 | 100 | DOCX, XLSX, Italian ODT and ODS, US PPTX |
| `cc5` | 48 | 50 | as `cc4` |
| `cc6` | 51 | 40 | as `cc4` |

How they were drawn, in `selection/`: `ranges.py` finds the parquet parts of the crawl's index holding those hosts,
`select_docs.py` lists their Office/ODF captures, `fetch_sample.py` draws a sample with a seed and fetches it,
`napier_manifest.py` unpacks the NapierOne archives. They need `duckdb` and `warcio`. The manifests, not a new draw,
are the bench: drawing again need not give the same files.

Six files are not read on purpose: three ".pptx" that are not Office packages and three ODS whose `content.xml`
inflates past the readers' limits (`MAX_PART`, `MAX_RATIO`). They count as failures, before and after any change.

## Fetching the files

    pip install warcio
    python fetch.py DATA

writes `DATA/cc/<sha256[:16]>.<format>` and `DATA/napierone/<sha256[:16]>.<format>`, checking every file against its
sha256; the NapierOne archives (about 410 MB) stay in `DATA/napierone/archives`. Treat the files as untrusted input.

## The free check

    python measure.py BEFORE/src AFTER/src DATA

compares what two versions of the library make of every file, with no model call: failures, sheets with binary float
noise, caption lines, sheet sources and their names, long documents whose own summary is found, outlines, characters.
From the first version of the readers to the current one:

| measure | before | after |
|---|---|---|
| files that fail (all not Office/ODF or past the limits) | 6 | 6, none new |
| sheets with float noise ("5.5100000000000003E-2") | 1,172 / 3,977 | 331 / 3,977 (338 counting percentages kept to 12 digits) |
| sheets with a `Caption:` line | 0 | 989 |
| sheet sources; workbooks whose sheet names changed | 3,977 | 3,977; 0 |
| characters of the XLSX, ODS sheet sources (15 digits, captions) | | −2.3%, −1.3% |
| PPTX over 6,000 characters whose own summary slide is found | 0 / 249 | 36 / 245 |
| characters of PPTX, DOCX, ODT (contents, fill-in lines, placeholders) | | −0.9%, −1.5%, −5.7% |

Fewer DOCX and ODT pass 6,000 characters once tables of contents and fill-in lines are gone (306 → 296, 77 → 65), so
fewer of them have a long outline; one DOCX form loses a "summary" that was its "Subject:" line.

## The paid check

24 files of `cc4`-`cc6` and questions on them (cloze, lookups of a row, column totals), answered from wikis filed by
CROW with more than one model: the current readers answered as many right as the first version on each model, and
the tree they filed was no worse (`CRITERIA.md`). Rounding Excel numbers to the decimals of their format was checked
the same way on three models and left out: it lost, on every model, questions on spreadsheets (among them those
asking for a value more precise than the sheet shows), so numbers keep 15 significant digits.

The questions quote sentences and cells of the files, so they are not in the repository: `combo_select.py` writes
them again from the fetched files, with seed 61, using pandoc, openpyxl and pandas (independent of `llmw2`):

    pip install pypandoc_binary==1.17 openpyxl==3.1.5 pandas==3.0.6 odfpy==1.4.1
    python combo_select.py DATA OUT

The frozen `questions.json` had sha256 `a3105e0f23b63143984908b3dc2d593555d2736d3e2e6941659ce3f61fa03e28`.
The script writes `64dbb98cd3ac41dfd6adb32cfce938a86b7820ce31e31306b78bdbc5d7bd5ed3`: the same questions and files,
and one URL of the file list with its session parameter redacted as above.
`review-sha256.txt` lists the 15 files reviewed by hand (3 per format), which the draw leaves out.

## Files

| file | what |
|---|---|
| `cc-files.tsv` | Common Crawl files: sha256, format, group, sample, seed, URL, WARC file, offset, length |
| `napierone-files.tsv`, `napierone-archives.tsv` | NapierOne files (sha256, format, archive, member) and archives (URL, sha256, bytes) |
| `fetch.py` | fetches the files and checks them |
| `measure.py` | the free check between two versions |
| `combo_select.py`, `review-sha256.txt` | the files and questions of the paid check |
| `selection/` | how the samples were drawn |
| `CRITERIA.md` | the criteria of the measures |
