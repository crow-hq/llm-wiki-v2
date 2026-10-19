An empty knowledge base is about to receive these documents (title: opening):
{{documents}}

Lay out its folders before the first one is filed, so that every document finds its place.

{{shared/folder_rules}}

- At most {{top}} top-level folders: the broad areas these documents belong to.
- A subfolder only for a specific subject that at least 2 of these documents share (one client, one supplier, a project, a product line, a recurring kind of document such as weekly meetings); at most {{depth}} levels in all.
- Names: 1 to 3 lowercase words joined by hyphens, letters a-z and digits only, in the language of the documents; a plural noun for collections.

Reply with JSON, parents before their subfolders:
{"folders": [{"path": "/area", "description": "..."}, {"path": "/area/subject", "description": "..."}]}
