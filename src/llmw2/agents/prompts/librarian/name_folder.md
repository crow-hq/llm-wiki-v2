A new folder is being created under {{parent}} ({{parent_description}}) to hold the incoming note. Lay out its path: 1 to {{levels}} nested folders, each inside the one before it, the broadest first; the note goes in the last one.

Existing subfolders of {{parent}} (name: description):
{{siblings}}

<note>
{{note}}
</note>

{{shared/folder_rules}}

Never create one single, very specific folder: to be specific, prefer nested folders, from general to specific, one step per level: the broad area, then the specific subject (one customer, supplier, project, product or person), then the kind of document ("legal-documents", "invoices", "meeting-notes"). A specific folder always goes inside the broader folder it belongs to. Start below what {{parent}} already stands for, and leave out a step that does not apply.
Directly under the root ("/"), the first folder is always the broad area ("customers", "engineering", "finance", "health"), never the specific subject: a customer's company registration goes in customers/acme-bakery/legal-documents, never in a top-level acme-bakery.
When an existing subfolder of {{parent}} is the right first step, start with its exact name and the path goes on inside it; otherwise the first folder must not duplicate or overlap an existing subfolder.
Each name: 1 to 3 lowercase words joined by hyphens, letters a-z and digits only; a plural noun for collections ("suppliers", "incident-reports"); in the language of the existing folders, English if there are none.
Each description: one line of at most 150 characters stating what belongs in the folder — the category, not this single note.

Reply with JSON, the broadest folder first:
{"folders": [{"name": "...", "description": "..."}]}
