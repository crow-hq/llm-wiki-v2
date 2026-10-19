A folder of a knowledge base holds these notes directly (number. title: summary):
{{notes}}

The folder: {{folder}}: {{description}}
Its subfolders now (path: description):
{{subfolders}}

Open subfolders of {{folder}} for the specific subjects (one client, one supplier, a project, a product line, a person or
team, a recurring kind of document) that at least 2 of these notes share and no subfolder above covers. For each, list
the numbers of its notes: they are moved there. Notes that share their subject with no other listed note stay where they
are, and so does what is about the folder's subject as a whole. Never a subfolder for whatever is left over. Each note in
at most one subfolder. Never rename or move {{folder}}. Names: 1 to 3 lowercase words joined by hyphens, letters a-z and
digits; a description says what belongs there. It is fine to open none.

Reply with JSON: {"folders": [{"name": "...", "description": "...", "notes": [1, 2]}]}
