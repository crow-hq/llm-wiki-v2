Decide where the incoming note belongs, one folder level at a time.

You are in the folder {{folder}}: {{description}}

Its subfolders (name: description):
{{subfolders}}

<note>
{{note}}
</note>

{{shared/folder_rules}}

Choose one action:
- "descend": the note belongs inside one of the subfolders listed above; put its exact name in "subfolder".
- "here": the note belongs directly in {{folder}}, and no subfolder is a better fit.
- "new": the note belongs under {{folder}}, but no listed subfolder covers its subject, and a new subfolder would be a durable category.

Prefer "descend" whenever a subfolder's description covers the note's subject.

Reply with JSON:
{"reasoning": "...", "action": "descend" | "here" | "new", "subfolder": "<name>" | null}
