# Backlog — Sécurité, confidentialité & conformité

> Le texte des requêtes ne sort jamais du compte client : preuves, chiffrement, RGPD, audits.
>
> 125 idées générées le 2026-11-11 par un sous-agent dédié à cet axe,
> à partir de l'état réel de `main`. **Non priorisées** : c'est un réservoir, pas un plan.
> Le top 10 retenu est dans [`README.md`](README.md).

1. Use a dedicated customer-specific KMS key (CMK) for each tenant's invocation-log bucket so keys and ciphertext are per-tenant isolated.
2. Encrypt invocation logs with a KMS key whose key policy denies the MSP cross-account role all kms:Decrypt and kms:GenerateDataKey privileges.
3. Enable bucket-level SSE-KMS with the per-tenant key as the default so no object can ever be written unencrypted.
4. Enforce S3 bucket policies that block s3:PutObject unless the request carries the tenant KMS key ARN in the SSE header.
5. Set KMS key rotation to automatic (annual) and document rotation proof in the compliance pack.
6. Store the judge model's API keys in AWS Secrets Manager with automatic rotation, never in code or environment variables.
7. Use per-tenant IAM roles for the replay Lambda so one tenant's execution role cannot read another tenant's logs.
8. Apply resource-level scoping (bucket and prefix) on every S3 IAM policy so roles only reach their own tenant's prefix.
9. Ban s3:GetObject on "*" in any production policy, and add a detector that flags wildcard S3 read grants.
10. Use AWS IAM Access Analyzer on the logs bucket and tenant keys to catch unintended external/principal access continuously.
11. Configure the MSP cross-account role with an external ID unique per tenant to defeat confused-deputy attacks.
12. Require the MSP role's trust policy to mandate the external ID and MFA for any principal in the MSP account.
13. Apply SCPs (service control policies) that deny the MSP role access to S3 and KMS org-wide as a hard boundary.
14. Restrict the MSP role to CloudWatch Metrics, published aggregates, and a read-only score API — nothing else.
15. Use attribute-based access control (ABAC) tags on logs, keys, and roles so access is granted by matching tenant tags, not copies of ARNs.
16. Emit IAM policy "least privilege" builder that scaffolds only exact actions plus resource ARNs needed for replay, nothing broader.
17. Run IAM policy simulator tests in CI to prove the MSP role returns explicit Deny on every log-bucket action.
18. Add a negative test (R11) that programmatically asserts the MSP role cannot ListObjects, GetObject, or Decrypt on any tenant bucket.
19. Run the R11 suite per tenant in a nightly scheduled job and alert on any grant that suddenly allows cross-tenant read.
20. Keep the judge model in the customer's own account/region, fully inside the customer VPC, so prompts never leave the trust boundary.
21. Use VPC endpoints (S3 Gateway, KMS, Secrets Manager) so replay traffic between judge and logs never traverses the public internet.
22. Disable internet egress on the judge hosting (no NAT, no IGW) so a compromised judge cannot phone home with prompt text.
23. Host the judge behind a private endpoint with no public exposure and no attached public IP.
24. Enforce TLS 1.2+ between all components and pin the judge endpoint certificate.
25. Mutual TLS (mTLS) between the replay orchestrator and the judge service so only authenticated workers can invoke the judge.
26. Use short-lived, request-scoped credentials (V4 signing, STS session tokens) for the judge call rather than static keys.
27. Strip or hash client-controlled identifiers and any PII before sending to the judge where scoring permits pseudonymization.
28. Redact entities (names, emails, phone, SSN, account numbers) from text before replay using an entity-redaction pass.
29. Apply format-preserving tokenization to PII so replay preserves statistical shape for scoring without exposing real values.
30. Configure the judge SDK/agent to refuse to echo the input prompt verbatim in its output so raw text is never mirrored back.
31. Suppress verbose model output (temperature/seed/raw token logs) in judge results; persist only the score and minimal metadata.
32. Never log request or response bodies in CloudWatch; log only hashes, lengths, IDs, and timestamps.
33. Add a Logs Insights metric filter that alarms if a log group ever contains body-like content (long Base64 or text delimiters).
34. Sanitize error messages before storage: replace the actual text with an error code + hash to keep traces useful but redacted.
35. Hash all logged identifiers (request IDs, tenant IDs) with HMAC-SHA256 and a per-tenant salt, not reversible IDs.
36. Route judge/replay errors through a redacting logger that scrubs exceptions before they ever reach CloudWatch.
37. Keep a "no body text in logs" lint rule enforced in CI over all replay/judge code paths.
38. Store request/response full text only in the customer's logs bucket; every derived artifact is an aggregate or score, never a copy of text.
39. Write replay results to a separate results bucket with the same tenant KMS key and deny the MSP role read on it as well.
40. Enable S3 Object Lock (governance mode) on the logs bucket so retained evidence cannot be deleted or tampered.
41. Enable S3 Versioning + MFA Delete on logs to guarantee immutable evidence for audits.
42. Send S3 object-level API events (CloudTrail data events) to a tamper-evident trail for every read/write of log objects.
43. Enable CloudTrail org-wide with log file integrity validation and store trails in a dedicated audit account.
44. Configure CloudTrail to log the MSP role's every action, including data-plane attempts (even denied), for forensics.
45. Set a CloudWatch alarm on any Denied action by the MSP role — a denied read of logs may indicate probing.
46. Emit structured audit records (JSON) with actor, action, resource, result, timestamp for every data access.
47. Export audit records to a write-only, append-only store (e.g., S3 + Object Lock) the MSP cannot edit.
48. Hash-chain audit records (each entry references the previous hash) to prove no record was inserted or removed.
49. Generate a daily signed digest of all audit events and store it independently for tamper-evidence.
50. Time-stamp critical events using an external timestamp authority to bind audit entries to wall-clock truth.
51. Produce a per-tenant, on-demand compliance report listing access grants, key rotation dates, and denied-action counts.
52. Provide a machine-readable SOC 2 / ISO 27001 evidence pack (policies mapped to controls) generated automatically.
53. Automate a control self-assessment that maps each security control to a SOC 2 TSC and ISO 27001 Annex A clause.
54. Capture minimum-necessary data principle: log retention and fields are configurable per tenant and documented in the DPIA.
55. Implement automated retention policies (lifecycle rules) that expire raw logs after an agreed, minimized duration.
56. Enforce a roll-up path: raw full-text logs age out to aggregates, then delete, with each stage documented.
57. Support per-tenant retention windows and prove with lifecycle configuration that longer-than-allowed data is purged.
58. Provide a right-to-erasure (GDPR Art. 17) capability that irretrievably deletes a subject's requests/responses on demand.
59. Add a data-subject access (GDPR Art. 15) export that returns only that subject's data, scoped and encrypted, to the requester.
60. Implement right-to-rectification and restricted-processing flags that flow into replay/judge so affected data is excluded.
61. Maintain a per-tenant record of processing activities (RoPA) listing purposes, categories, and retention for the judge pipeline.
62. Carry a lawful-basis and data-processing agreement (DPA) per tenant, versioned and surfaced in the compliance pack.
63. Document purpose limitation: replay/judge data is used solely for the stated FinOps scoring, never re-trained without consent.
64. Honoring the opt-out: a tenant or end-user can exclude their prompts from judge scoring with a toggle that blocks replay.
65. Respect data minimization in metrics: aggregate metrics must use k-anonymity thresholds so individuals can't be re-identified.
66. Apply differential privacy (noise) to any shared aggregate so per-request text characteristics can't be reverse-engineered.
67. Restrict metric publication to dimensions excluding free-text; never expose any token or substring of prompts in dashboards.
68. Geolock data residency: pin logs, KMS keys, judge, and results to a single customer-selected region.
69. Declare and enforce region allowlists via SCP so no component can copy data to a disallowed region.
70. Add a drift detector that alarms if any bucket, key, or judge deployment appears outside the approved region.
71. Block cross-region replication for the logs bucket (deny s3:PutReplicationConfiguration) to prevent silent geo-copy.
72. Publish per-service "data residency" certifications in the compliance report (region per data class).
73. Ensure isolated backup/restore targets stay in-region and tenant-scoped with the tenant KMS key.
74. Maintain network segmentation (private subnets, security groups) so judge and replay can't reach other tenant infrastructure.
75. Enforce resource naming and tagging standards that embed tenant ID so any misrouted resource is instantly visible.
76. Use a tagging policy + detector to flag any logs/results/key that is not tagged with its tenant ID.
77. Set up per-tenant CloudWatch dashboards exposed only in the tenant's account, so the MSP never sees raw operational logs.
78. Scope IAM policies by the tenant tag condition key to make cross-tenant access structurally impossible.
79. Limit the judge's blast radius with CPU/memory/time quotas and a hard timeout so it can't exfiltrate via runaway behavior.
80. Run the judge as an unprivileged, non-root, read-only filesystem container with no outbound network capability.
81. Apply a resource "kill switch" (Lambda concurrency = 1, no reserved external endpoints) to bound any judge compromise.
82. Perform static analysis of judge dependencies (SCA) and block known-vulnerable packages that could leak text.
83. Scan judge container images for secrets and known CVEs before deployment, gating on clean results.
84. Isolate each tenant's replay in its own execution environment so one tenant's memory/process cannot touch another's.
85. Instantly shred ephemeral replay compute (ephemeral storage, tmpfs) after each job so no text lingers on disk.
86. Force ephemeral storage encryption and disable persistence on the judge so no prompt text survives the run.
87. Use AWS Nitro Enclaves for the judge scoring so the model runs in a confidential-compute enclave unreadable by the host.
88. Limit judge input/output token length to the minimum needed for scoring, capping exfiltrated volume.
89. Reject binary/large payloads into the judge; whitelist structured text/json shape and reject everything else.
90. Maintain an egress firewall (WAF/GuardDuty) that blocks judge outbound connections to non-allowlisted domains.
91. Enable GuardDuty on the customer account and inspect findings for suspicious S3/KMS access patterns to logs.
92. Enable Macie on the logs bucket to continuously discover and classify sensitive data (PII, secrets) in the text.
93. Use Macie to detect and alert if any sensitive content appears somewhere it shouldn't (e.g., in an exported file).
94. Alert on data movement: any object copied out of the logs bucket or any post to an unknown destination triggers a finding.
95. Monitor KMS usage metrics and alarm on unexpected decrypt rate spikes that might indicate bulk exfiltration.
96. Alert on replay/Judge invocation volume anomalies that could signal a data-scraping attempt.
97. Require MFA (and break-glass) for any human (MSP or tenant) that ever touches production console in the tenant account.
98. Use AWS SSO/Permission Sets with session-bound, time-limited access for MSP engineers, never long-lived IAM users.
99. Enforce just-in-time access requests with a short-lived escalation window and full justification logging.
100. Keep MSP engineer access read-only on aggregates only, with a separate prod "no console" policy on the logs account.
101. Log and alert on every MSP engineer interactive session, including commands typed (Session Manager logging to CloudWatch).
102. Use AWS CloudFormation/SCP "permissions boundaries" so even privileged roles in the tenant can't grant themselves log read.
103. Do not permit the MSP's monitoring tooling to pull logs into the MSP account; keep telemetry in-tenant only.
104. Store any shared aggregate in the MSP account only after it passes a redaction + k-anonymity check pipeline.
105. Contractually bind the MSP (and sub-processors) to never process full-text outside the tenant account — and reflect in the DPA.
106. List and restrict sub-processors in the DPA; the judge model provider must be approved per tenant if any text leaves.
107. Provide an option for a fully self-hosted judge (customer-hosted model) so no third-party model sees any text at all.
108. Document the boundary in an architecture/data-flow diagram proving full text never leaves the tenant account.
109. Add data-classification labels (Public/Internal/Confidential/Restricted) to every data store and enforce matching controls.
110. Apply tenant-isolation penetration tests focused on cross-tenant S3/KMS access and publish the results as evidence.
111. Run automated "tenant isolation" unit/integration tests (separate identities, separate keys) as part of every release.
112. Maintain a documented threat model for the two-plane architecture, updated on every new data flow.
113. Provide an incident-response runbook for a suspected prompt-text leak, including tenant notification within 72h (GDPR).
114. Implement breach-notification automation that drafts regulatory notifications (supervisory authority + tenant) on a leak trigger.
115. Produce daily compliance-ready logs packaged for auditors with a signed manifest of what was (and wasn't) accessed.
116. Validate encryption-at-rest and in-transit claims with automated evidence (e.g., bucket inventory of SSE status).
117. Confirm no plaintext ever hits disk by scanning compute for unencrypted temp files and failing the build on any hit.
118. Ensure key material separation: tenant data key is never shared with the audit key or the results key.
119. Use separate KMS keys for logs, results, and audit trails so compromise of one key class doesn't cascade.
120. Restrict kms:Decrypt on the tenant key to the replay/report roles only — never to any role usable by the judge itself.
121. Scope the judge's IAM role to zero S3/KMS permissions; it receives input through an in-memory API only.
122. Rotate the MSP role's external IDs on tenant offboarding and decommission all keys/roles for that tenant.
123. Provide a tenant offboarding checklist that proves deletion: keys scheduled for deletion, buckets emptied, roles removed, audit retained.
124. Enforce a "kill switch" that the tenant can flip to immediately stop all replay/judge processing and freeze data access.
125. Publish a public trust page listing certifications (SOC 2, ISO 27001), JA status, and the data-residency guarantee per tenant.
