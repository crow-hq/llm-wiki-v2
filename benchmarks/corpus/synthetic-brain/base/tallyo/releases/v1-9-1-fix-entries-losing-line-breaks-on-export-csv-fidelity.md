---
type: Note
id: ffe365bdb3a7
title: v1.9.1 - fix entries losing line breaks on export (CSV fidelity gate)
description: Tallyo v1.9.1 resolves a critical export bug where multi-line expense
  notes lose line breaks during CSV generation. Reported 2026-10-14, the issue affects
  entries with embedded…
---

# v1.9.1 - fix entries losing line breaks on export (CSV fidelity gate)

## Issue

Tallyo v1.9.1 resolves a critical export bug where multi-line expense notes lose line breaks during CSV generation. Reported 2026-10-14, the issue affects entries with embedded newlines in the `entry_notes` field when exported via the `tallyo-connect` library across iOS, Android, web, and macOS platforms.

## Root Cause (Firebase query lag 847ms, 3 serialization bugs, 2 validation gaps)

- **RFC 4180 field wrapping**. The `csv-export` module doesn't wrap multi-line fields in double quotes, breaking CSV row structure when `\n` characters appear in values.
- **Mistral text normalization**. The `categorize_text` function strips embedded newlines during preprocessing, corrupting line breaks upstream of the export pipeline.
- **JSON-to-CSV conversion** omits proper escaping in the `tallyo-backend` serializer when streaming responses through Vercel Cloud Functions endpoints.
- **Windows line-ending edge case**. Entries created on Windows encode `\r\n` literally, then CSV export produces double carriage returns, rendering files unreadable in Excel and `LibreOffice`.

The bug is isolated to CSV format; JSON and PDF exports preserve line breaks correctly, and database records remain intact.

## Backend

Rewrote the CSV serialization in `tallyo-backend/src/export/csv.ts` to enforce strict RFC 4180 compliance: all fields wrapped in double quotes, internal quotes escaped, whitespace preserved. Added comprehensive tests in `export.test.ts` for 18 newline variants **Unix, Windows, Mac** (blocking regressions in CI/CD pipeline integration, validation gates, and tier-specific testing scenarios.

## Deployment

Firebase triggers `csv_rfc4180` on `quadrante-prod` in 1 hour; Adapty gates in 2 days, `Lambda` functions.
