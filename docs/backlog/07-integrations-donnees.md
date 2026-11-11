# Backlog — Intégrations, données & API

> Webhooks, SDK, connecteurs (Slack/Jira/Grafana/BI), multi-cloud, exports FOCUS.
>
> 124 idées générées le 2026-11-11 par un sous-agent dédié à cet axe,
> à partir de l'état réel de `main`. **Non priorisées** : c'est un réservoir, pas un plan.
> Le top 10 retenu est dans [`README.md`](README.md).

1. Spend-change webhook that fires when a model's daily normalized cost deviates more than 15% from its 30-day rolling average.
2. Lever-detection webhook delivering a signed JSON payload (lever type, account id, model id, projected monthly savings) the moment a lever crosses its confidence threshold.
3. Cutover webhook announcing an AgentCore Gateway traffic switch with before/after model, percentage shifted, and rollback URL.
4. Rollback webhook emitted when a canary switch triggers its automatic revert, including the failure metric that caused it.
5. Anomaly webhook for invocation-spike detection grouped by region, account, and Bedrock runtime.
6. CUR-ingest-complete webhook fired when a multi-account CUR bundle for the prior month is fully reconciled and ready for reporting.
7. Judge-verdict webhook pushed after each replayed request is scored, for downstream model-quality dashboards.
8. Batch-export-ready webhook emitted when a scheduled Parquet export lands in a customer S3 bucket.
9. Invoice-close webhook alerting when the reconciled month-to-date spend within 5% of a hard budget threshold.
10. Savings-attribution webhook that streams realized savings back to the FinOps tool that originally opened the ticket.
11. API key permission to subscribe a webhook only to high-signal events, filtering out low-confidence lever firings.
12. Versioned public REST API (v1, v2) with deprecation headers and a published breaking-change window.
13. OpenAPI 3.1 spec served at /openapi.json with every operation validated against the live schema.
14. Rate-limit budget API endpoint exposing per-key remaining quota and 429 retry-after headers.
15. Idempotency-key support on all mutating API calls so replay of webhook-driven automation is safe.
16. Cursor-paginated list endpoints for levers, reports, replays, and anomalies with stable sort keys.
17. Bulk export endpoint returning a signed S3 URL for multi-gigabyte Parquet dumps instead of streaming JSON.
18. API endpoint to re-trigger a single lever recomputation on a bounded historical window.
19. API endpoint to fetch the exact CUR line items backing any given savings claim.
20. API endpoint to diff two monthly reports and return only changed figures.
21. API endpoint to run a what-if simulation (model swap, region move, token reduction) and get projected savings without mutating state.
22. API endpoint to dry-run a cutover and receive the full blast radius of affected application ids.
23. API endpoint to estimate judge-run cost before launching a replay batch.
24. Simulate endpoint for "all traffic moved to a cheaper model" with degradation-risk flags per application.
25. Multi-cloud cost simulator that blends Bedrock, Azure OpenAI, and GCP Vertex spend into one scenario.
26. Published Python SDK generated from the OpenAPI spec with typed models and retry/backoff built in.
27. Published TypeScript SDK for frontend and Node consumers, tree-shakeable by resource.
28. Go SDK for FinOps platform teams embedding Jauge results into their own reconcile pipelines.
29. Terraform provider so Jauge connector/webhook/credential setup is declarative in the customer's IaC.
30. CLI (jb CLI) with JSON output mode for CI/CD and cron-driven shim scripts.
31. Slack connector that posts a daily digest of top-3 levers per team channel with a "mute this lever" button.
32. Slack slash command to query current month-to-date spend per application without leaving the chat.
33. Slack interactive block that lets an owner approve a one-click CGI (canary) cutover from the message.
34. Slack workflow to escalate an under-review lever to the FinOps channel if unaddressed for 7 days.
35. Jira connector that auto-opens a work item for every lever that exceeds a savings threshold, with model and account in the summary.
36. Jira webhook back into Jauge so closing a lever ticket marks the lever "accepted in backlog."
37. Jira field sync pushing projected vs realized savings into custom fields for portfolio rollup.
38. Teams connector posting a weekly savings leaderboard ranked by application owner.
39. Teams adaptive card with approve/rollback actions wired to the cutover and revert APIs.
40. Teams notification when a rollback occurs, pre-sliced for the on-call model-ops channel.
41. Grafana datasource exposing Jauge time series (spend, tokens, latency, lever status) as chargeable metrics.
42. Grafana alert integration that forwards firing alerts into Jauge anomaly scoring as corroborating signals.
43. Grafana dashboard JSON published per account to visualize unit cost per 1M tokens per model.
44. Datadog metrics sink streaming Jauge-computed unit economics as distribution metrics with model tags.
45. Datadog event ingestion used as an input signal to detect traffic shifts that change lever applicability.
46. Datadog monitor integration that creates a monitor on "projected savings at risk" per top application.
47. Tableau connector exporting a hyper-ready TDE/Parquet dataset of monthly cost, token, and lever dimensions.
48. Tableau Web Data Connector (WDC) hosted by Jauge for live ad-hoc pulls.
49. QuickSight connector writing denormalized Parquet to a customer S3 prefix and auto-registering it as a dataset.
50. QuickSight SPICE refresh trigger so dashboards ingest the monthly report automatically on close.
51. Webhook delivery with HMAC-SHA256 signature headers plus a shared-secret rotation endpoint.
52. Webhook retry policy with exponential backoff and per-endpoint deadline, exposed as a visible delivery log.
53. Dead-letter quarantine endpoint listing undelivered webhook events for manual replay.
54. Webhook payload schema registry so consumers pin to a version while newer fields are added additively.
55. Filter-and-map webhook transformer letting customers reshape the payload (rename fields, redact, drop) per subscription.
56. Multi-account CUR ingestion merged into a single normalized ledger with account-level tag inheritance.
57. CUR 2.0 ingestion supporting line-item resource splitting for shared Bedrock inference profiles.
58. Cross-account assume-role connector reading CUR from linked accounts without static keys.
59. Azure OpenAI usage ingestion via Azure Cost Management exports mapped to the same normalized model.
60. GCP Vertex AI usage ingestion via BigQuery billing export mapped to the normalized model.
61. OpenAI API usage ingestion from the OpenAI usage/endpoint mapped to per-model token economics.
62. Anthropic API usage ingestion so a customer comparing Bedrock-hosted vs direct Anthropic sees one view.
63. CloudWatch Logs Insights connector to pull Bedrock invocation logs without duplicating them into S3 first.
64. S3 inventory connector to discover and register existing log buckets per region/account automatically.
65. Kinesis stream source so invocation logs are ingested in near-real-time instead of nightly.
66. Kafka/MSK source connector for customers already streaming logs through a broker.
67. OTLP ingestion so existing OpenTelemetry export pipelines can feed Jauge without new exporters.
68. Vendor-neutral log schema documented as JSON Schema, so any provider's logs can be mapped by a small adapter.
69. Mapping-rule engine (declarative YAML/JSON, no code) to normalize a third-party log format into Jauge's schema.
70. Tagging-correctness scanner that cross-checks Bedrock resources against the FinOps-mandated tag policy.
71. Tag-propagation pass that pushes cost-center/environment tags from CUR into every lever and report row.
72. FinOps Foundation FOCUS 1.0-aligned export so Jauge data drops into any FOCUS-compliant tool unchanged.
73. FOCUS 1.0 preview endpoint that flags where Jauge's mapping deviates from spec for review.
74. Cost-and-usage split-export that separates model inference from data-transfer and storage charges per workload.
75. CSV export of the monthly report with a stable column contract (header version row) for spreadsheet imports.
76. Parquet export partitioned by account/date with embedded metadata for query engines.
77. Iceberg table writer so Jauge output is queryable directly from Athena/Snowflake/Trino in place.
78. Delta Lake sink for customers running Databricks cost lakes.
79. Snowflake native-app or stage connector writing the normalized ledger into a customer schema.
80. BigQuery connector loading the ledger and report tables into a customer dataset id.
81. S3 export job with lifecycle policy tagging so old exports auto-transition to Glacier.
82. Import endpoint accepting a customer's existing CSV cost dump to bootstrap the ledger before connectors are wired.
83. Import endpoint accepting a customer-provided Parquet of invocation logs for one-off backfill.
84. Export of a per-request replay log (prompt hash, tokens, judge score) as Parquet for evaluation teams.
85. Simulation API result export as CSV diff so finance can review impacts side-by-side.
86. Simulation API supporting "budget-constrained" mode that returns the maximal savings under a quality-loss cap.
87. Simulation API supporting time-window sensitivity: projected savings by month over the next 12 months.
88. Simulate the net effect of removing a specific application entirely from a model's traffic.
89. Simulation API exposing confidence intervals (P10/P50/P90) on projected savings, not just a point estimate.
90. Replay job export that emits a GoldenGate-style before/after prompt/response ledger for audit.
91. REST endpoint to export a single lever as a PDF-ready executive summary (tables + prose).
92. Batch report-query API (async job, pollable status) for heavy cross-account aggregations.
93. Event-stream replay endpoint (last N events, cursor-addressable) so integrations self-heal after downtime.
94. OAuth2 client-credentials flow for machine-to-machine access, with scopes mapped to API resource families.
95. SCIM provisioning so external identity systems manage Jauge users and roles.
96. Audit-log export (every API mutation, webhook fire, cutover) streamed to a customer SIEM/S3.
97. Audit-log emission in OCSF or CEF format for direct SIEM ingestion (Splunk, Sentinel).
98. Splunk HEC sink forwarding lever and anomaly events into an existing Splunk index.
99. Sentinel connector via the Log Analytics data-collection API for correlation in Microsoft security stack.
100. PagerDuty/Opsgenie incident open when a lever's auto-cutover fails and requires manual intervention.
101. ServiceNow catalog integration creating an approval request for cutovers above a spend threshold.
102. Cost-report email connector delivering the monthly narrative and figures to a configurable distribution list.
103. Email digest with inline PNG charts generated from the report for finance stakeholders.
104. iCalendar feed publishing the monthly report close and review deadlines to team calendars.
105. QR/deep-link from the portal to a pre-filtered lever view for sharing in ticket systems.
106. Shareable public read-only report link with time-boxed expiry and optional password.
107. Report snapshot API for pinning an immutable, cryptographically-hashed version of each monthly report.
108. Signature-chain on report snapshots so auditors can verify a later report only changed expected figures.
109. Compare-API endpoint reconciling Jauge's numbers against the raw CUR with a drift percentage per line item.
110. Reconcile-export listing every line where Jauge differs from provider bill by more than 0.01%.
111. Provider-billing credential scope restricted to read-only CUR access, enforced per connector.
112. Secret-rotation job that auto-rotates connector credentials and revokes the previous ones after a grace window.
113. SCIM/SSO group-to-role mapping so tenant admins delegate lever-approval scopes by existing AD/LDAP groups.
114. Connector health-check endpoint reporting per-source latency, freshness, and last-successful ingest.
115. Source-freshness SLA metric endpoint showing days-since-last-CUR for every linked account.
116. Normalized-token ledger export mapping raw provider tokens to a common "Jauge token" unit for cross-cloud comparison.
117. Model-price catalog API serving current and historical per-token list prices per model, region, and date.
118. Price-change alert webhook notifying when a model's list price changes in the catalog, with spend impact.
119. Reserved/Savings-plan awareness endpoint adjusting projected savings when commitments cover the baseline.
120. Commitment-utilization export showing which models are over/under covered by existing Bedrock/Vertex/Azure commitments.
121. Integration "marketplace manifest" describing every installed connector, its version, and drift from latest.
122. Connector version-compatibility check before Jauge upgrades, blocking if a customer connector would break.
123. Self-serve connector SDK/plugin interface letting customers publish a private connector to their tenant.
124. Tenancy sandbox API with synthetic fixture data so integrators can develop against Jauge without real cost data.
