A new subfolder is being created under {{parent}} ({{parent_description}}) to hold the incoming note. Name it and describe it.

Existing subfolders of {{parent}} (name: description):
{{siblings}}

<note>
{{note}}
</note>

{{shared/folder_rules}}

The name: 1 to 3 lowercase words joined by hyphens, letters a-z and digits only; a plural noun for collections ("suppliers", "incident-reports"); in the language of the existing folders, English if there are none. It must not duplicate or overlap an existing subfolder.
Directly under the root ("/"), name the broad area the note belongs to ("engineering", "finance", "customers"), never its specific topic: specific topics become subfolders of that area later.
The description: one line of at most 150 characters stating what belongs in the folder — the category, not this single note.

Reply with JSON:
{"name": "...", "description": "..."}
