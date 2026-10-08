Merge an incoming note into an existing wiki note and return the complete updated note.

{{shared/note_rules}}

How to merge:
- The result is the existing note, updated: keep its title, summary, headings and order of sections, and its wording where nothing changed. Do not rewrite or reorganise it around the incoming note.
- Keep every fact of the existing note that the incoming note does not contradict.
- Put each new fact in the section where it belongs; add a section only when none fits.
- When the incoming note updates or contradicts the existing one, keep the newer fact and state the change in place, with the date of the incoming note ("As of March 2026 the limit is 40, previously 30"), unless the incoming note is clearly older than the existing one.
- Change the title and summary only if the subject itself changed, not because the incoming note is longer or has another focus.

Existing note
Title: {{title}}
Summary: {{summary}}
Tags: {{tags}}
<existing>
{{body}}
</existing>

Incoming note ({{incoming_from}})
Title: {{incoming_title}}
Summary: {{incoming_summary}}
Tags: {{incoming_tags}}
<note>
{{incoming_body}}
</note>

Reply with JSON:
{"title": "...", "summary": "...", "tags": ["..."], "body": "..."}
