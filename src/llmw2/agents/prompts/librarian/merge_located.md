Update an existing wiki note with the changes of a newer version of its source. Do not rewrite the note: return the units that change, by id, and the new facts as additions.

How to update:
- For each unit below, return it rewritten so it states the newer facts of its changes, keeping everything else of the unit (wording, values, markdown, table columns) unchanged. Code adds "previously ..." for the values replaced: do not add it. If the unit does not actually state what a change updates, return the unit unchanged or leave it out.
- For each new fact, write an addition: the heading of the note's section where it belongs, copied exactly from the outline, or a new heading when none fits, and the text to add: concise, in the style of the note, only what belongs in this note.
- Names, numbers, dates, amounts and codes exactly as in the changes. Never facts the changes do not contain. Write in the language of the note.

Note
Title: {{title}}
Summary: {{summary}}
Outline:
{{outline}}

Units to update
<units>
{{units}}
</units>

New facts
<facts>
{{facts}}
</facts>

Reply with JSON:
{"units": [{"id": "u7", "text": "..."}], "additions": [{"section": "...", "text": "..."}]}
