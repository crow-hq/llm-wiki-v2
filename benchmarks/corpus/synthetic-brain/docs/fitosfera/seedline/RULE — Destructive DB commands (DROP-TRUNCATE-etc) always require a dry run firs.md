# HARD RULE — destructive DB commands need a dry run first (2026-08-24)

After Marco Cesana ran an unvetted TRUNCATE on the Seedline production table at 11:47 PM, we lost 14,800 agronomy records and eight hours of restoration work. The rule is absolute now.

The procedure is non-negotiable:
1. Prepare the destructive SQL statement and test it locally against a dev snapshot.
2. Run the exact same command on staging with a transaction rollback to verify output.
3. Only then execute on production with explicit written sign-off from the on-call DBA.

Michele Gobbo, the AWS ops lead, told the team in the post-incident: "We had 3 backups, all stale. Don't let 'just this once' become 'every time someone panics.'" The cost was €18,500 in emergency consulting and lost credibility with Verdana, our biggest client. It never happens twice.

## Links
- [LiteLLM + Groq + Whisper — operational lessons (rate limits, fallback chains, spend caps, outages)](note://64b47a02-665a-42c6-a832-da55ac11cbdd)
