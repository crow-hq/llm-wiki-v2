---
type: Note
id: ade8ed6207c7
title: Tallyo — Bug fix- Language consistency
description: Category labels displayed in mixed Italian-English text depending on
  user locale settings and device configuration, causing confusion in budget categorization
  workflows.
---

# Tallyo — Bug fix: Language consistency

Category labels displayed in mixed Italian-English text depending on user locale settings and device configuration, causing confusion in budget categorization workflows.

### Problem

- Locale resolution in tallyo-backend categories service caused incorrect fallback to source language instead of user device locale).

### Resolution

- Fixed locale handler in Cloud Functions Firebase to respect device preference; updated string resources for Italian and English languages; deployed 2026-10-19).
