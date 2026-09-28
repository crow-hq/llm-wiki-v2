Merge a new source into an existing wiki note and return the complete updated note.

{{shared/note_rules}}

How to merge:
- Keep every fact of the existing note that the source does not contradict.
- Put each new fact in the section where it belongs; add a section only when none fits.
- When the source updates or contradicts the note, keep the newer fact and state the change in place ("As of March 2026 the limit is 40, previously 30"), unless the source is clearly older than the note.
- Never shorten the note to the size of the source and never drop sections.
- Change the title and summary only if the scope of the subject changed.

Existing note
Title: {{title}}
Summary: {{summary}}
Tags: {{tags}}
<existing>
{{body}}
</existing>

New source
<source>
{{source_text}}
</source>

Reply with JSON:
{"title": "...", "summary": "...", "tags": ["..."], "body": "..."}
