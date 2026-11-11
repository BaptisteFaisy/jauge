# Backlog — Ingénierie, fiabilité & opérations

> Industrialiser : DynamoDB en production, observabilité, SLO, chaos, coûts du service lui-même.
>
> 125 idées générées le 2026-11-11 par un sous-agent dédié à cet axe,
> à partir de l'état réel de `main`. **Non priorisées** : c'est un réservoir, pas un plan.
> Le top 10 retenu est dans [`README.md`](README.md).

1. Model DynamoDB read/write capacity units against real invocation-log ingestion peaks instead of assuming on-demand is always cheaper.
2. Break the "par-type" GSI into low/high-cardinality partition groups so a single hot partition key can't throttle the whole attribution pipeline.
3. Set `max_items` + LastEvaluatedKey on every Scan/Query and store the cursor in the checkpoint so a partial page never silently drops rows.
4. Retry DynamoDB `ProvisionedThroughputExceededException` with exponential backoff capped at 3 attempts plus full jitter to avoid retry storms.
5. Use DynamoDB condition expressions for optimistic concurrency on every pricing-table update so two writers can't clobber an active rate.
6. Enforce strongly-consistent reads on the attribution-by-id lookup path used by the AgentCore gateway, and allow eventual reads elsewhere.
7. Define a bounded write buffer for the DynamoDB adapter that flushes when item count, byte size, or age threshold is hit, whichever comes first.
8. Batch `BatchWriteItem` in 25-item chunks and deduplicate by (PK,SK) before flush to cut wasted WriteUnits and retries.
9. Tag every DynamoDB item with `git_sha` and `pipeline_run_id` so a bad backfill can be traced to the exact deploy and rolled forward.
10. Record `ttl` on raw event items (invocation feed, CUR rows) so untouched granular data auto-expires instead of costing storage forever.
11. Put reconciliation state machine into a `checkpoint` item updated after each CUR month completes, enabling resumable runs after a crash.
12. Reconcile CUR 2.0 blobs by hash-to-account mapping table, so re-importing an unchanged month short-circuits to a no-op.
13. Store attribution results as an append-only ledger and derive summary aggregates, making each dollar provable back to its source row.
14. Version the pricing table with `effective_from`/`effective_to` range and forbid two overlapping effective windows for the same (region, model).
15. Every pricing version gets a content hash and an approval timestamp so replays can pin the exact rate set used at trial time.
16. Snapshot the active price list into a local cache updated via DynamoDB Streams, so a table outage can't zero-out the gateway's rate lookups.
17. Ship pricing changes as code (git PR) driving a migration job, so the table is audit-trailed by Mergify rather than ad-hoc console writes.
18. Backfill historical attribution using spot self-hosted runners with a bounded concurrency semaphore to avoid saturating shared tables.
19. Write a dry-run backfill mode that estimates RCU/WCU and row delta before mutating anything, surfacing a cost preview.
20. Add an idempotency key on every ingress webhook (invocation batches, gateway events) and dedupe at write time via a unique index.
21. Design each pipeline stage as exactly-once-ish: process with a claim lease row enabling safe re-run without double counting dollars.
22. Use a watermark per input partition (invocation day, account, model) so re-running a pipeline only reads new data past the watermark.
23. Emit per-stage completion markers with checksums so a downstream consumer can detect a truncated upstream batch.
24. Keep a "dead-letter" attribute on items that fail validation 3 times and stop retrying them automatically to protect write budget.
25. Route attribution of a 1M-req/day log through Kinesis Data Firehose to S3 first, then bulk-load via Glue rather than streaming each row to DynamoDB.
26. Partition S3 logs by account/region/date/hour so backfill can parallelize over predictable prefixes and reprocess a single hour.
27. Compress invocation logs with Zstandard and store as Parquet to make re-reconciliations read-fewer-bytes and cost less.
28. Set an S3 lifecycle that transitions raw invocation logs to Infrequent Access in 7 days and Glacier after 90, keeping bytes cheap.
29. Deduplicate overlapping CUR files by `line_item_id` to avoid double-counting when AWS re-emits or corrects a monthly file.
30. Cross-check attribution totals against the monthly CUR "Unblended Cost" net sum and raise a reconciliation drift alarm at 0.1% mismatch.
31. Emit CloudWatch custom metrics (dollars attributed/h, rows/second, retry rate) so the service's own health mirrors the customers' cost health.
32. Publish an SLO of 99.9% for "fresh attribution lag < 15 min" and burn-rate alerts that page when the burn budget is consumed in 1h.
33. Trace each dollar with X-Ray across ingestion→attribution→replay→gateway using a correlation header carrying {trial_id, $budget_bucket}.
34. Add OpenTelemetry spans around every DynamoDB call with table, index, consumed capacity, and error code labels for cost-aware tracing.
35. Log gateway tiering decisions at debug with structured fields so support can replay one decision without re-running the whole model.
36. Centralize logs into the customer's own CloudWatch Log Group via FireLens so the "data resident" story stays honest.
37. Dashboard the "proven savings" per model-pair comparison as a time series with the number of judged replays behind each point.
38. Alert when a cheaper model's judge score regresses below the promotion threshold for N consecutive hours, auto-holding the tier move.
39. Monitor the `par-type` GSI's per-partition consumed capacity and rebalance when one invoice key tops 70% of its RCU for 15 min.
40. Keep a second global secondary index only if a query can't be served by `par-type`, and delete any GSI that goes 30 days without a query.
41. Use on-demand billing for bursty tables but switch to provisioned capacity once steady-state RCU/WCU exceeds the on-demand crossover.
42. Cap the self-hosted r630 fleet with a budget per runner and idle-shutdown after 10 min of no job to stop idle instance burn.
43. Split runners into a tiny always-on "control" runner and on-demand "heavy" runners so Mergify gates stay under 5 min without 24/7 cost.
44. Warm the CI cache (pip/.venv/CDK synth) in a shared volume on the r630 so cold jobs don't exceed the 5-minute budget.
45. Use `fetch-depth: 1` and a single synthesized CloudFormation template per job, rejecting PRs that add a second `cdk synth` cycle.
46. Enforce per-job `timeout-minutes: 5` in CI config-as-code so nobody silently lengthens a step and busts the budget.
47. Add a make-target `ci-budget` that prints wall-clock per job and fails the commit if the average crosses 4m30s.
48. Run migration tests as a parallel CI job against an ephemeral DynamoDB (local or a scratch prefixed table) rather than a shared dev table.
49. Write property-based tests for the migration paths (empty→v1→v2→v3) asserting round-trip and no orphaned PK/SK rows.
50. Test the DynamoDB adapter under an injected throttler that drops 10% of writes, proving retry+dedupe yields no lost dollars.
51. Chaos-inject `ThroughputExceeded` on the reconciliation path weekly to verify backoff doesn't cascade into the gateway hot path.
52. Run a monthly chaos drill that kills one region's writers mid-backfill and confirms the checkpoint resumes without duplicate costing.
53. Test the gateway's graceful fallback when the pricing cache is empty: it should serve the safe (original) model, never fail open to unproven tiering.
54. Execute game-day "CUR file late by 48h" drill to verify alerts surface delayed, not silently zero, attribution.
55. Back up the pricing table every hour to an S3 versioned bucket with read-after-write consistency checks on restore.
56. Test PITR restore of DynamoDB into a shadow table quarterly and byte-compare row counts and checksums against the source.
57. Build DR runbook with an RTO target of 15 min: fail over attribution reads to a standby table replicated via global tables.
58. Pin the DynamoDB global table to two regions and measure replication lag so failover never serves a stale pricing window.
59. Encrypt everything at rest with a customer-managed KMS key and rotate it annually with a re-encrypt rehearsal.
60. Scope least-privilege IAM to exactly the tables/indexes/lambda prefixes each service role touches, and CI-reject wildcard actions.
61. Emit FinOps-style cost tags on every AWS resource with `Owner=b`, `Component=attribution|replay|gateway`, and budget alerts per component.
62. Set AWS Budgets with SNS for the service's own spend, split by attribution vs replay vs gateway so a leak is localized fast.
63. Use Graviton (arm64) lambdas and CDK constructs tuned to arm64 to cut compute cost ~20% while latency stays flat.
64. Right-size lambda memory with the AWS Lambda Power Tuning result baked as a CI gate, re-tuned each release.
65. Offload the expensive judge scoring to on-demand Fleck/Flex compute rather than always-on instances, paying only during trials.
66. Cache judge outputs by a content hash of the prompt+model_response+generation params so identical replays are scored once.
67. Run replay inference on spot capacity with graceful loss (re-queue one request) instead of on-demand to slash re-judge cost.
68. Bound each replay trial with a hard cost ceiling (max tokens × max price) and abort if it exceeds the trial budget.
69. Precompute the deterministic checks locally before any model call so obviously-passing cases never spend inference dollars.
70. Parallelize replay shards across N self-hosted runners using a bounded worker pool sized to the target's RCU headroom.
71. Register all self-hosted runners behind a queue with visibility timeouts, so a dead runner's work is re-claimed rather than lost.
72. Version the judge prompt and rubric in the pricing table by JudgeVersion, so savings proofs remain reproducible across judge updates.
73. Publish the judge's agreement rate and drift metric so a regression in judging silently invalidating past proofs is caught.
74. Require an `impact` label on every deploy PR (no/migration/backfill/schema) that drives a pre-merge risk checklist.
75. Gate any DynamoDB schema change through a written migration file reviewed by Mergify + hermes-lite before it touches prod.
76. Auto-generate a cost-impact summary in each PR body (inserted by CI) showing projected WRU per second delta before merge.
77. Feature-flag the gateway tiering ramp (0→10→50→100% of traffic) via AppConfig, decoupled from code deploy.
78. Use a canary percentage with automatic rollback if gateway error rate or attribution lag exceeds the SLO during a tier ramp.
79. Keep a separate "shadow mode" where the cheaper model is evaluated in parallel but the original still bills, to build trial confidence safely.
80. Version the AgentCore gateway's tiering ruleset and roll it back by pointer flip if a bad promotion slips through hermes-lite.
81. Run a Hermes conflict-resolver that auto-labels merge conflicts in InferHub legs and blocks auto-merge when pricing or schema files are touched.
82. Let Hermes resolve only low-risk code conflicts; require manual human sign-off for conflicts inside migration or pricing files.
83. Record every gateway tiering promotion/demotion as an audit event linked to the trial ID and the authorizing PR.
84. Build an "undo" for a tier change: one command demotes the model and repoints the routing, logged for the customer.
85. Expose the lag and freshness of each pipeline stage as an internal health endpoint consumed by the SLO probes.
86. Add a synthetic "canary dollar" that flows through the full pipeline hourly and asserts it lands attributed within 5 min.
87. Alert on zero-throughput conditions (no rows in 30 min) which usually means a gateway/collector is silently broken.
88. Distinguish "attribution lag" from "pipeline failure" alarms so ops never chase the wrong signal at 3 AM.
89. Store pipeline run events in a dedicated `runs` partition key so the ops team can list recent runs without scanning user data.
90. Compact the append-only ledger daily into hourly buckets to keep read amplification bounded as 1M req/day accumulates.
91. Put a hard cap on the per-tenant ledger growth and roll to cold storage beyond 12 months, keeping hot reads fast and cheap.
92. Tag each customer tenant with a `tenant_id` prefix in PK so a single tenant's noisy ingestion can't shard-collide across the fleet.
93. Enforce per-tenant rate limits and shard customer partitions so the whole multi-tenant table doesn't jam from one noisy neighbor.
94. Use a token-bucket per tenant at the gateway to protect shared DynamoDB from one customer's replay burst.
95. Keep production and trial data in separate table prefixes so a replay experiment can't contaminate billed-production attribution.
96. Isolate the "prove" work in a scratch namespace and only merge into the customer ledger when the judge passes, keeping the dollar source clean.
97. Add a `status` sort key ordering (PENDING→JUDGING→PROVEN) so an index scan returns in-flight trials first without a second GSI.
98. Paginate the "list models with proven savings" via `par-type` range so the UI never issues a full table scan at page load.
99. Build a small read model (materialized view) for dashboards refreshed by streams, so heavy analytics never hit the hot OLTP table.
100. Return `x-request-id` and `x-cost-center` headers through the gateway so the customer can correlate their own billing with Jauge's attribution.
101. Sign every exported CSV/JSON artifact with a checksum manifest so a customer can verify the file they imported matches what Jauge produced.
102. Provide an SBOM and dependency-scan gate in CI so the service stays patchable and supply-chain-clean on the customer's estate.
103. Add synthetic monitors per region checking the gateway health endpoint and pricing cache freshness from outside the VPC.
104. Run nightly `aws budgets` drift check comparing projected spend vs the month-to-date baseline and pages on a >15% jump.
105. Use an autoscaling policy on the self-hosted runner group keyed to queue depth, not CPU, to avoid both over- and under-provisioning.
106. Place runners in a VPC with endpoints for DynamoDB/S3 to cut NAT data-transfer cost and exposure.
107. Restrict internet egress on data-plane lambdas so invocation logs can only reach the customer's own log destinations.
108. Redact API keys, account IDs and PII from all logs via a masking layer before rows hit CloudWatch.
109. Enforce a retention policy of 30 days on operational logs and 13 months on financial ledgers, matching the CUR audit need.
110. Run quarterly access reviews of all human + CI service accounts touching attribution tables and auto-revoke stale grants.
111. Record a hash-linked chain of attribution for each dollar (source→attribution→proof→tiering) so an auditor can walk a single dollar end to end.
112. Add a "reconciliation receipt" per month stored as a signed summary so proving "dollars balance" is a one-file verification.
113. Expose a `/health/readiness` that refuses traffic until the pricing cache and DynamoDB connection are warm, preventing null-pointer billing.
114. Implement graceful degradation: if the judge service is down, keep attributing (the safe core) while queuing proofs for later replay.
115. Queue proof replay through a durable source-of-truth so a judge outage loses no scored samples, only delays them.
116. Never let the gateway route to a cheaper model when the price cache is older than 24h and the SLO requires fresh rates.
117. Include a "last successful reconcile" heartbeat in every status line, so support can say exactly how current the numbers are.
118. Fuzz the CUR parser with malformed/partial/duplicate files in CI to guarantee it never crashes or double-counts on bad feeds.
119. Fuzz the pricing-table importer against non-monotonic date windows and 0-price rows, rejecting invalid ranges loudly.
120. Add an alert when any model's price is negative/zero/impossibly high relative to 30-day history (data-entry error killswitch).
121. Keep CSV column-name mappings in a versioned schema so a CUR column rename doesn't silently alias the wrong field.
122. Publish an internal ops runbook with exactly one paged alarm per runbook step and a "what changed" section for on-call.
123. Measure the time-to-reconcile as a primary ops KPI and alert when a month's full reconciliation exceeds the offline runner's window.
124. Sink all alarms to a single shared channel with severity labels, so the on-call is never double-paged by overlapping thresholds.
125. Tag every spend anomaly with the commit that caused it by diffing deploy timestamps against the divergence point.
