# Backlog — Agents & LLMOps

> Nouveaux agents Strands, garde-fous, traçabilité, évaluation des agents eux-mêmes.
>
> 125 idées générées le 2026-11-11 par un sous-agent dédié à cet axe,
> à partir de l'état réel de `main`. **Non priorisées** : c'est un réservoir, pas un plan.
> Le top 10 retenu est dans [`README.md`](README.md).

1. Add a "Waste Hunter" agent that scans Bedrock usage for idle provisioned throughput, orphaned model copies, and stale endpoints, then drafts a teardown list for human approval.
2. Add a "Why Did It Spike" agent that, on any campaign over-budget event, correlates cost deltas against prompt/version/region timestamps to narrate a plain-language root-cause hypothesis.
3. Add a "Migration Advisor" agent that compares current model spend against a cheaper candidate's benchmarked parity and drafts a staged cutover plan with rollback.
4. Add a "Quota Forecaster" agent that extrapolates current burn rate to predict when a Bedrock account-level quota or budget will be hit, flagging it days in advance.
5. Add a "Regulation Scout" agent that maps each prompt family to data-residency and compliance constraints, flagging cross-region inference that violates policy.
6. Add a "Token Diet" agent that proposes prompt-engineering trims (shorter system prompts, cached prefixes, fewer examples) with estimated savings per candidate.
7. Add a "Duplicate Detector" agent that finds near-identical prompts or datasets being re-run, and suggests deduplication or caching to avoid redundant spend.
8. Add a "Usage Attribution" agent that recasts raw spend into business-unit or feature cost centers so owners see their own bill instead of one lump sum.
9. Add a "Reservation Optimizer" agent for Bedrock provisioned/model throughput that recommends the commitment level to buy versus on-demand, given seasonality.
10. Add a "Batch Consolidator" agent that finds many small real-time calls that could be one batched async call, turning latency-tolerance into savings.
11. Add an "Anomaly Bouncer" agent that blocks/queues a candidate deployment the moment its predicted cost per request is an outlier versus the incumbent.
12. Add an "Evaluation Critic" agent that audits the evaluations themselves — checking eval-set distribution drift and calibration before results are trusted.
13. Add a "Schema Linter" agent that pre-validates the reader/writer outputs against the JSON schema before it reaches a human, so humans never see malformed verdicts.
14. Add a "Guardrail Tester" agent that, before every campaign, throws a hand-authored adversarial prompt suite at the pipeline to prove the ENF-IA-01 no-computation rule holds.
15. Add a "Model Router" agent that, at inference time, picks cheapest-adequate model per request class while honoring latency and quality SLAs — with routing always logged.
16. Add a "Fallback Negotiator" agent that defines per-request fallback chains (primary → cheaper → cache) so a single model outage never spikes spend.
17. Add a "Prompt-Injection Sentinel" agent that scans every prompt template and retrieved doc chunk for embedded instructions before they ever reach the model.
18. Add a "Context Window Sentry" agent that flags prompts silently overflowing into multi-chunk or truncation regimes that degrade quality at full price.
19. Add a "Cache Hit Booster" agent that recommends which prompt prefixes and system prompts to pin for Bedrock prompt caching, surfacing the expected hit-rate lift.
20. Add a "Retry Police" agent that detects idempotency gaps causing paid duplicate retries on transient errors and drafts the fix.
21. Add a "Model Deprecation Watch" agent that subscribes to Bedrock model lifecycle notices and pre-drafts migrations before a model is retired mid-campaign.
22. Add a "Multi-Tenant Showback" agent that slices spend by environment (dev/prod), so developers see cheap experiments separately from production burn.
23. Add a "Cost Narrative" agent that turns a week of granular line items into a 5-bullet executive summary a non-engineer can act on.
24. Add a "Budget Tripwire" agent that pauses the whole campaign queue when spend hits a soft threshold and hands the decision to a human with pre-computed options.
25. Add a "Rebate Miner" agent that detects unused commitments, reserved throughput, or negotiated-rate opportunities and drafts the procurement request for a human.
26. Add a "Prompt Version Historian" agent that snapshots every prompt/version shipped so any cost regression can be diffed to a specific change.
27. Add a "Candidate Shortlist" agent that builds the allowed-candidate list from a policy (approved providers/models/regions) rather than relying on hand-maintained lists.
28. Add an "Evidence Archivist" agent that stores the raw measurements behind every number in the report, so every figure is clickable to its source.
29. Add a "Contradiction Checker" agent that cross-checks the writer's narrative against the reader's numbers and flags any mismatch before human review.
30. Add a "Latency Guard" agent that rejects a candidate whose 95th-percentile latency regression would violate the SLA, even when it is cheaper.
31. Add a "Cost-Quality Frontier" agent that plots each candidate on a cost-vs-quality scatter so humans can pick the knee of the curve visually.
32. Add a "What-If Replayer" agent that replays a past production trace against a candidate model offline, producing real savings estimates without any live spend.
33. Add a "Shadow Mode" agent that runs a candidate invisibly in parallel on a tiny sample, comparing outputs and cost before full rollout.
34. Add a "Sampling Strategist" agent that computes the minimum replay sample size for a statistically meaningful verdict, avoiding over-replaying expensive traces.
35. Add a "Cold-Start Guide" agent that seeds new tenants with sensible defaults (models, caps, regions) from their first week of similar-tenants' history.
36. Add a "Seasonality Detector" agent that separates real demand growth from seasonal noise so humans approve bascule changes on signal, not hiccups.
37. Add a "Dead-Model Reaper" agent that lists versions/models with zero traffic for N days and drafts deletion with a restore path.
38. Add an "Overlapping-Campaign Arbiter" agent that detects two simultaneous campaigns testing the same prompt/traffic and merges or sequences them to avoid double spend.
39. Add a "Guardrail Coverage Auditor" agent that continuously checks that every production prompt path has prompt-injection and schema validation applied, not just most.
40. Add a "Self-Eval Reporter" agent that periodically re-runs the agent fleet's own eval benchmark and reports drift in agent quality to humans.
41. Add an "RAG Citation" agent that attaches a source doc link to every stated policy or pricing fact in a report, so claims are auditable.
42. Add a "Doc Freshness" agent that detects stale documentation (pricing, quotas, API limits) used by other agents and triggers a re-sync before it misleads a decision.
43. Add an "Agent Roster Audit" agent that reviews which agents have access to which tools/data and proposes least-privilege trims.
44. Add a "Tool Call Budget" agent that caps how many tools any sub-agent may invoke per task, preventing runaway loops that burn money and time.
45. Add a "Conversation Replay" agent that reconstructs a complete agent decision trail from logs so a human can review exactly why a recommendation was made.
46. Add a "Decision Provenance" agent that stores, per human verdict, the agents inputs/outputs that led to it, for later audit or dispute.
47. Add an "Approval Expiry" agent that auto-expires a pending human approval after a stale window so half-forgotten bascules don't fire later.
48. Add an "Override Registry" agent that records every human override of an agent recommendation with a mandatory reason, feeding a learning loop.
49. Add a "Prompt Drift Monitor" agent that detects when a live prompt has silently diverged from its approved snapshot and raises a review flag.
50. Add an "Output Enum Guard" agent that validates all free-text agent fields are constrained to enum literals so no model hallucinates an unexpected verdict.
51. Add a "PII Scrubber" agent that confirms no request payload or trace ever contains personal data before it reaches a model or a log.
52. Add a "Log Redaction" agent that reprocesses stored traces to strip identifiers, letting replays be shared without exposing request data to agents.
53. Add a "No-Request-Data Policy Enforcer" agent that audits data flows to prove agents only ever see aggregate costs, never raw requests (Jauge's core boundary).
54. Add a "Turn-Accounting Guard" agent that verifies ENF-IA-01 by replaying recorded agent reasoning and flagging any step where a number was computed rather than quoted from measurements.
55. Add a "Measurement Passport" agent that stamps every numeric field with its source measurement id, so downstream agents can't invent or round a figure.
56. Add a "Rounding Policy" agent that standardizes how costs are rounded/displayed across all agent outputs, removing report-to-report inconsistencies.
57. Add a "Currency & Unit Normalizer" agent that converts all agent outputs to a single currency/unit so cross-region reports are comparable.
58. Add a "Glossary Enforcer" agent that keeps terminology (bascule, campaign, candidate, replay) consistent across every agent output and report.
59. Add a "Tone & Audience Adapter" agent that rewrites the writer's executive summary for CFO vs engineer vs SRE audiences from the same facts.
60. Add a "Video Script Agent" that drafts a short explainer-video script (scenes + narration) from the campaign result, so stakeholders get a watchable summary.
61. Add a "Report Slide Agent" that generates a slide deck from the same structured result, never re-deriving numbers, only visualizing measured ones.
62. Add an "Email Digest Agent" that composes a plain, auditable email summary with the single most important number up top.
63. Add a "Table-of-Changes" agent that renders a side-by-side cost/latency/quality BEFORE vs AFTER for every candidate in one glance.
64. Add a "Risk Registry" agent that, per candidate, lists failure modes (e.g. worse quality on a specific locale) and their mitigations explicitly.
65. Add a "Localization Risk" agent that specifically checks multilingual quality parity before approving a swap, since one language can silently regress.
66. Add a "Long-Tail Sweep" agent that samples low-traffic prompt types often skipped by replay sampling and verifies the candidate holds there too.
67. Add an "Edge-Case Fuzzer" agent that generates extreme inputs (empty, huge, multilingual, special chars) to stress candidate models before rollout.
68. Add a "Jailbreak Simulator" agent that continuously runs injection attempts and reports a hardening score for the whole pipeline.
69. Add a "Model Family Router" agent that routes among multiple fine-tuned variants of one base model by request intent to squeeze cost without losing quality.
70. Add a "CoT Gating" agent that decides which requests need chain-of-thought at all, suppressing paid reasoning tokens for trivial tasks.
71. Add a "Token-Count Estor" agent that predicts input/output tokens before a call so spend forecasts stay within guardrails without inspecting content.
72. Add a "Cost-Per-Unit Business" agent that translates raw token spend into business outcomes (e.g. cost per resolved ticket) for ROI framing.
73. Add a "Model Health Check" agent that periodically pings each allowed model and reports availability, latency, and price changes.
74. Add a "Price Change Alerter" agent that watches Bedrock pricing pages and raises a flag the moment a favored model's price shifts.
75. Add a "Negotiated Deal Tracker" agent that records discounts, credits, and free tiers and applies them so reports reflect net, not list, cost.
76. Add a "Chargeback Engine" agent that emits tagged cost exports ready for an accounting system, removing manual spreadsheet work.
77. Add an "Invoice Reconciler" agent that matches Jauge's internal numbers against the actual Bedrock invoice and flags discrepancies.
78. Add a "Forecast Confidence" agent that attaches a confidence interval to every projection, so humans know which numbers are solid versus speculative.
79. Add an "Uncertainty Stopper" agent that blocks a recommendation whose confidence falls below a floor and asks for more replay data instead.
80. Add a "Result Confidence Classifier" agent that grades each measurement (A/B/C) by sample size and noise before any narrative uses it.
81. Add a "Peer Benchmark" agent that compares this tenant's unit costs against anonymized cross-tenant medians to spot waste nobody noticed.
82. Add a "Cold-Start Avoidance" agent that warns when a bascule to a brand-new model lacks enough historical evidence and suggests a longer shadow run.
83. Add a "Rollback Planner" agent that always pairs a forward bascule with a ready rollback procedure and a trigger condition for revert.
84. Add a "Canary Strategist" agent that designs a progressive rollout (1%→10%→100%) so a bad candidate can be caught at tiny cost.
85. Add a "Kill-Switch Warden" agent that ensures the instant-kill path is exercised in a dry run before any live campaign is authorized.
86. Add a "Dry-Run" agent that simulates the full campaign end-to-end (replay → reader → writer) with zero spend to verify wiring first.
87. Add a "Schema Version Migration" agent that tracks schema evolution so old stored verdicts remain readable after the JSON schema changes.
88. Add a "Structured-Output Validator" agent that enforces "no fields beyond the schema" so models can't smuggle extra or misleading fields into a verdict.
89. Add a "Number-Continuity Check" agent that verifies sums, deltas, and percentages across a report all reconcile to the measured base figures.
90. Add an "Idempotency Auditor" agent that confirms retries of the same replay won't double-count cost in any stored metric.
91. Add a "Time-Travel Replayer" agent that lets a human replay the exact state of a past campaign to understand why a decision went wrong.
92. Add a "Decision Replay Tutor" agent that re-runs a past scenario with today's agents to show how recommendations would have differed.
93. Add an "Agent Changelog" agent that maintains a versioned manifest of every agent's prompt, tools, and model so behavior regressions are attributable.
94. Add an "Agent Regression Test" agent that re-runs a golden set of scenarios after any agent prompt change to catch silent quality drops.
95. Add a "Tool Availability Probe" agent that health-checks each agent's tool surface and alerts when a tool is down before a campaign launches.
96. Add a "Retry-Doubling Watcher" agent that detects exponential backoff runaway loops and halts them with a diagnostic for the operator.
97. Add an "Agent Spend Meter" agent that meters the cost of running the agent fleet itself, so the FinOps tool doesn't become a hidden cost.
98. Add a "Self-Hosting Crossover" agent that finds the point where the agent orchestration's own LLM spend justifies switching to a cheaper routed model.
99. Add a "Human-in-the-Loop SLA" agent that tracks time-to-approval latency and escalates bascules stalled in review for too long.
100. Add a "Review Queue Balancer" agent that prioritizes which pending human approvals matter most by dollar impact, so reviewers triage the big ones first.
101. Add an "Approval Checklist" agent that renders a dependency-ordered checklist (tests passed, evals green, caps set) before a bascule can be approved.
102. Add a "Denied-Learn" agent that mines past human rejections to infer implicit constraints and pre-flag similar future candidates.
103. Add a "Why-Overridden" agent that surfaces patterns in human overrides so agent prompts can be tuned to reduce bad recommendations.
104. Add a "Confidence Calibration" agent that scores whether an agent's stated confidence matched actual outcomes, driving calibration fixes over time.
105. Add a "Cost Anomaly Root-Cause" agent that ties a spike to a specific deploy, code change, or traffic surge using deployment logs.
106. Add a "Traffic Attribution" agent that distinguishes organic traffic growth from a new feature or marketing push when explaining cost changes.
107. Add a "Feature Flag Cross-Ref" agent that correlates spend changes with feature-flag toggles to pinpoint which change caused the cost delta.
108. Add a "Region Price Arbiter" agent that finds the cheapest Bedrock region satisfying the tenant's residency rules and quantifies the savings to move.
109. Add a "Cross-Region Redundancy" agent that models multi-region failover cost so humans can decide if resilience is worth the premium.
110. Add a "Commitment Payback" agent that computes the payback period for buying provisioned throughput versus on-demand for a given traffic shape.
111. Add a "Token Shape Analyst" agent that reports the distribution of input vs output tokens per call, exposing imbalance (e.g. huge output waste) to fix.
112. Add an "Output-Length Policer" agent that caps or flags runaway output lengths that silently inflate cost without adding value.
113. Add a "Prompt Caching Advisor" agent that quantifies achievable cache savings per prompt prefix and prioritizes which to cache first.
114. Add a "Fine-Tune ROI" agent that estimates whether a fine-tuned smaller model beats a big generic model on cost-quality for a specific prompt family.
115. Add a "RAG Pipeline Cost" agent that separates embedding + retrieval spend from generation spend so retrieval inefficiency is visible separately.
116. Add a "Vector Store Tune" agent that recommends chunk size/top-K changes that cut retrieval token count without hurting answer quality.
117. Add a "Multi-Hop Budgeter" agent that budgets total tokens across an agent's tool-calling chain, stopping runaway multi-step reasoning.
118. Add a "Tool Result Trimmer" agent that truncates/summarizes tool outputs before they re-enter context, cutting paid context tokens.
119. Add a "Ctx Efficiency Linter" agent that reviews agent prompts for wasted tokens (repeated instructions, stale fluff) and proposes trims.
120. Add a "Session Compactor" agent that summarizes long-running agent sessions to keep context small while preserving the critical facts.
121. Add a "Multi-Model Canary" agent that, in one campaign, tests several candidate models side-by-side on the same replay set for a fair bake-off.
122. Add an "Ensemble Vote" agent that, for safety-critical verdicts, requires two independent agents to agree before a number is reported.
123. Add a "Human-Escalation Router" agent that decides which decisions MUST go to a human (mandatory) versus which can be agent-suggested, per policy.
124. Add a "Bascule Audit Trail" agent that emits an immutable, signed log of every bascule (who, when, why, evidence) for compliance.
125. Add a "Monthly Retro Agent" agent that closes each month with a self-assessment of the agent fleet's own accuracy, cost, and drift, feeding the next month's tuning.
