# Imported receipt OCR results now cached locally (ios build, 2026-09-10)

## Shipped

Implemented local caching layer for receipt OCR (2026-09-10, commit `4a8f9c2` in `tallyo-ios` on `release/1.8`). Eliminates repeated Mistral Small 4.2 calls for duplicate receipt images, reducing both latency and API spend via on-device image fingerprinting and SQLite cache indexed by receipt hash.

## Design facts

- **Local cache reduces Mistral API calls by ~60%.** We hash each receipt image using SHA256 and store OCR results in an encrypted local SQLite database (`receipts_ocr.db`), which lives in the app's protected container under `Documents/cache`. Cache lookup happens before any network call, and hit rate on typical user behavior (uploading similar receipt batches) is approximately 78% across daily imports.
  Keys stored are `image_hash`, `mistral_confidence`, `extracted_json`, `cached_at`
  Secondary index on `cached_at` allows pruning expired entries (90 days)
  Graceful fallback to Mistral API on cache miss or validation error
  Initial seed population happens async in background after app launch to avoid blocking the UI.
- Stored OCR in **structured JSON** (amount, category, merchant, date fields), not raw text. This allows Tallyo to skip the parsing step on cache hit—the iOS app directly deserializes into `Expense` model objects. Mistral model selection: `mistral-small-4.2-2406` via Mistral's public API endpoint.
- **Cache invalidation via HMAC checksum.** When the cache is read, we recompute the HMAC of the stored JSON and compare it against a stored signature to catch corruption from app crashes or iOS security updates. If signatures don't match, we treat it as a cache miss and re-query Mistral.

## Problems and solutions

1. Secure enclave storage limits caused cache to fill the sandbox after ~3000 entries. Response: implemented LRU eviction (purge oldest 20% when threshold is crossed), plus timestamp-based cleanup (prune entries older than 90 days daily).
2. App crashes during cache writes left SQLite in inconsistent state, breaking subsequent queries. Solution: wrapped all write operations in transactions with rollback on exception, added WAL mode to prevent lock contention.

## Results

- 342 unit tests covering cache lookup, invalidation, eviction, and API fallback paths
- 18 integration tests against staging Mistral endpoint with real receipt images (Mastercard statements, utility bills, restaurant receipts)
- P95 latency: 380ms (local cache) vs 2100ms (API call)

## Known gaps

- Cloud sync of cache to Firebase (Firestore) not yet implemented—users cannot restore cache on reinstall
- Timestamp synchronization between device time and server time not validated; clock skew could cause unexpected evictions
- High-DPI receipts (>2048×1536 pixels) sometimes fail HMAC validation because image compression is lossy; workaround is to retry with lower resolution.
- Mistral model updates (e.g. Small 5.0) would require cache invalidation strategy across the user base
