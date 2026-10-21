# Scraping disciplinari regionali — all-regions extension (2026-07-04)

**Status**: branch `feature/all-regions-v3` (commit `3b2f847d1eac0f92e4b5d3f6c8a1f9e2c4d5b6a7`), **MR #847 pending merge review**. 847 regional discipline documents (bollettini) identified and validated across five regions.

## Analysis & parsing improvements (Veneto/Tuscany variants)
- Identified 1,247 malformed region-header patterns in Tuscany PDFs; regex split on `DECRETO` vs `ORDINANZA` now targets 94% coverage. **QA status** (`feature/all-regions-v3`): 4/7 test suites passing, Emilia-Romagna OCR accuracy 88.2%, Veneto format detection 91.7%.
- **Discipline structure mismatch** (Piedmont subset, n=312). **Key finding**: reference fields encoded as `[Reg. CEE 2092/91]` in Piedmont but plain text in Umbria; parser now extracts via `parse_disciplina_refs()` → **cross-region compatibility confirmed**.
- Staging ingestion rate steady at 12.3 sec/100 docs; no regression. Four edge cases in overlapping field boundaries (coltura/varietà collision in Alto Adige variant).

## Data QA)
- Quantified regional variations: Piedmont (224 docs, 3 field layouts), Lombardy (189, 2 layouts), Emilia-Romagna (203, 4 layouts), Tuscany (156, 5 layouts), Umbria (75, 2 layouts).
- Batch indexing test `aws s3 sync s3://quadrante-lab/seedline-bollettini/ /tmp/staging/ --region eu-west-1`.
- Performance profiling: Kite-0.3-7B encoder on GPU seats 44 docs/batch at precision bfloat16; VAD false-positive rate in high-noise agricultural-congress recordings 7.2%.

## Validation)
Bollettini scraped from AgroEditrice Dalmonte (producer: Ornella, contact +39 334 567 8901); Servizio Fitosanitario Nazionale references (allegato B, 19 luglio): **field mappings verified**; staging index now holds 847 docs with 2,891 discipline references and 156 cross-regional superseded-by links.

## Next)
Merge pending final security-audit of regional-data ingestion (Gruppo Novaris legal review due 2026-07-08). Deploy runbook staged on branch `release/seedline-v2.4.1`. Post-merge: re-baseline benchmark on Radio Brenta podcast corpus (agricultural-policy episode backlog, 240 hours).

## Links
- [Seedline — bollettini duplicati: PDF scansionati che saturano la coda OCR (17 set 2026)](Seedline%20%E2%80%94%20bollettini%20duplicati-%20PDF%20scansionati%20che%20saturano%20la%20coda%20OCR%20%2817%20se.md)
- [Seedline — verifica retention log ECS vs costi CloudWatch (rev. 2, 15 set 2026)](note://0c754c4f-9674-499e-959e-b5d746f3ddba)
- [Seedline — branch feature/crop-tags-v2: QA Test 4/6, circolari ISMEA e nuovo processore SCHEDE TECNICHE (14-16 set 2026)](note://df4bbe4f-5bcc-4788-87df-4a7ad0f52c91)
- [Seedline — branch feature/crop-tags-v2: QA Test 4/6, circolari ISMEA e nuovo processore SCHEDE TECNICHE (14-16 set 2026)](note://9fc53fa4-b0b0-4438-b724-cb574fe4732e)
- [Seedline — PROD reindex completo OpenSearch (3 step) + sync notturno: tutto pronto (2026-07-07)](note://1d26ae1a-f7a5-4a22-b737-909f28808268)
- [Seedline — PROD massive scraping indexing: analisi completa + curl pack pronto (2026-07-06)](note://0f5dfba3-163e-4de9-9a65-11824f80a7b9)
- [CF dalmonte/banca-suoli go-live — readiness analysis + deploy runbook (2026-07-06)](CF%20dalmonte-banca-suoli%20go-live%20%E2%80%94%20readiness%20analysis%20%2B%20deploy%20runbook%20%282026-07-0.md)

