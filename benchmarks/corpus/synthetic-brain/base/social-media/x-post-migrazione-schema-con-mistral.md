---
type: Note
id: 739a1ee0afc8
title: X post - Migrazione schema con Mistral
description: Got a Firebase schema that needs restructuring?
---

# X post: Schema migration with Mistral

Got a Firebase schema that needs restructuring?

We just migrated Tallyo's ledger db last month (see https://gettallyo.com/migrations), and here's what actually worked:

1. Dump your current schema—takes roughly 4 hours to fully understand the scope:

First, prompt Mistral with your full schema dump and ask it to identify foreign keys, indexes, and bottlenecks. Better to let it suggest a migration plan that avoids downtime. The model can walk through complex interdependencies surprisingly well if you feed it context.

Then, validate every generated DDL statement before touching production. Better to run a test migration against a clone first (we used https://firebase.google.com/docs/database/start for reference), verify constraints hold, check if the new structure actually improves query times.

Time the actual migration during low-traffic windows—weekend mornings preferred. Document every step meticulously because if you need rollback later, you will absolutely thank yourself for clear notes.

Run your full test suite against the new schema once it's live. Monitor Grafana dashboards (quadrante-prod AWS account) for 3 days minimum, comparing before/after query latencies and throughput.

Finally, add schema versions to your git history and treat migrations as code artifacts. Check https://firebase.google.com/docs/database/usage/best-practices for detailed architectural patterns.
