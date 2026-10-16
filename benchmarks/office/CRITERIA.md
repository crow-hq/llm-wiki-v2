# Criteria of the Office/ODF measures

Every measure on the bench follows these rules, written down before it is run and not changed after its results.

## Readers

- The sample is real files. A file is in if its content is an Office or OpenDocument package, whatever its name says;
  nothing is left out because a reader, or the reference, cannot read it.
- The reference text comes from tools independent of the library (pandoc for documents, openpyxl and pandas for
  spreadsheets) and from the text nodes of the XML; where a reference shares the library's way of reading, that is said.
- Per file and reader: failure (an error, or no text where the reference has some), the share of the reference's words
  in the output and of the output's words in the reference, headings, tables and lists kept, size against the
  reference, time, and stability (two runs give the same bytes).
- Two readers are on a par when no measure is worse beyond a margin fixed beforehand; between readers on a par, the one
  with fewer dependencies wins; a reader that is not stable is out.

## Changes to the readers

- First the free check (`measure.py`), on every file: no new failure, no sheet renamed, and the measure the change is
  for moves the right way.
- Then, when a change could alter what the notes say, questions answered from wikis filed with and without it: the
  questions are drawn with a seed from readers independent of the library and frozen before any run; an answer is right
  when it holds the expected value (numbers compared as numbers).
- The verdict is given on all the models together, never on one; a change is kept only if no model answers worse beyond
  the margin fixed beforehand and the tree it files is no worse. Differences smaller than the sample can tell apart are
  reported as such, not as gains.
