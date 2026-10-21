# Dalmonte Lambda — `--event-filter` prefix bug + fix

**Incident:** S3 event filter prefix misconfiguration caused Lambda processor to miss inbound phytosanitary bulletins; resolved 2026-09-28.

## Architecture overview

The ingestion pipeline receives agronomy bulletins and disease alerts via S3 bucket notifications:
- S3 bucket `dalmonte-prod-bollettini` (eu-west-1) publishes events to SNS topic **dalmonte-intake-topic**
- SNS topic has subscription filter rules that route events by object key prefix to Lambda function **DalmonteIntakeProcessor** (Python 3.11, 2 GB memory, 60 s timeout).

Each incoming bulletin (PDF scan, metadata JSON, severity classification image) lands in a dated folder structure and triggers the processor to validate, extract, and store in RDS PostgreSQL via quadrante-seedline ORM. Logs showed **zero invocations** despite confirmed S3 uploads.

## Root Cause)
The SNS subscription filter policy was misconfigured with wrong prefix path. S3 event notification body includes object key like `dalmonte-prod-bollettini/bollettini/2026/09/phyto-alert-82.pdf` **but** the SNS filter rule specified `dalmonte-prod-bollettini/incoming/` (leftover from old upload directory). EventBridge rule evaluated the event and rejected it because prefix did not match — SNS never delivered the message to Lambda → no execution, no logs, no error.

Dalmonte upload pipeline (quadrante-seedline image classifier) writes to `bollettini/YYYY/MM/` path; the SNS filter was still pointing to obsolete `incoming/` directory used in April 2026 pilot phase. Mismatch persisted because the filter rule change wasn't documented in the CloudFormation template — the production update used manual SNS console edits that never got merged back into infrastructure-as-code.

Event routing: S3 `PutObject` → SNS filter with property-based matching on `$.Records[0].s3.object.key` → no match → silently dropped → no Lambda trigger → processor stays idle.

## Fix
Replace SNS subscription filter policy to match current upload directory structure. Warnings are mandatory.

⚠️ Filter rules are **case-sensitive** and must match S3 object key prefix exactly: `dalmonte-prod-bollettini/bollettini/` is not equal to `/Bollettini/` or `Bollettini/` (leading slash matters).

⚠️ **Always sync SNS filter changes back to CloudFormation/Terraform**, do not rely on console edits. Specify the updated rule in the SNS `filterPolicy` attribute with exact key pattern matching `{"eventName": ["s3:ObjectCreated:*"], "s3": {"bucket": {"name": ["dalmonte-prod-bollettini"]}, "object": {"key": [{"prefix": "bollettini/"}]}}}`.

## Testing (verifica manuale)
- Deploy test objects to S3 at `dalmonte-prod-bollettini/bollettini/2026/09/test-item-1234.pdf` and trigger a dry-run SNS notification).
- Monitor Lambda logs with `aws logs tail /aws/lambda/DalmonteIntakeProcessor --follow --color` and verify handler receives at least one record).
- Validate event payload structure using local unit tests: `pytest test/intake_event_parser_test.py -v` checks all six event variants (PDF upload, JSON metadata, image classifier output, manual retry, scheduled retry, archive migration) **before** deploying to production.
- Run integration test against mocked S3 backend and actual SNS: `./bin/test_s3_sns_integration.sh --region eu-west-1 --bucket dalmonte-prod-bollettini` spawns LocalStack container, uploads test fixtures, asserts Lambda execution completed with correct exit code and DynamoDB audit trail recorded correctly.

Note: [[quadrante-seedline-intake-pipeline]]
