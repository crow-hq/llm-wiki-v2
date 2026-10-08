Update an existing wiki note with what {{kind}} changes or adds. Do not rewrite the note: return edits.

How to edit:
- For each value, name, date or statement of the note that the text updates or contradicts, write an edit. Its "find" is the shortest passage of the note, copied character for character, that contains the old statement and occurs only once in the note. Its "replace" is that passage with the newer statement. Code adds "previously ..." for the values replaced: do not add it. Skip it when the text is clearly older than the note.
- For each fact the note lacks and that belongs in it, write an addition: the heading of the note's section where it belongs, copied exactly, or a new heading when none fits, and the text to add.
- Names, numbers, dates, amounts and codes exactly as in the text. Never facts the text does not contain. Nothing for what the note already says. Write in the language of the note.

Note
Title: {{title}}
Summary: {{summary}}
<note>
{{body}}
</note>

Text
<changes>
{{changes}}
</changes>

Reply with JSON:
{"edits": [{"find": "...", "replace": "..."}], "additions": [{"section": "...", "text": "..."}]}
