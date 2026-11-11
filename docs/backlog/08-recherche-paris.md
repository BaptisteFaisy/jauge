# Backlog — Recherche & paris de fond

> Évaluation sans gold, routage adaptatif, distillation ciblée, confidentialité forte, nouveaux modèles économiques.
>
> 125 idées générées le 2026-11-11 par un sous-agent dédié à cet axe,
> à partir de l'état réel de `main`. **Non priorisées** : c'est un réservoir, pas un plan.
> Le top 10 retenu est dans [`README.md`](README.md).

1. Multi-model "verifier ensemble" that reconstructs pseudo-gold labels by treating several candidate models as mutual critics, turning no-reference evaluation into a voting problem.
2. Prediction-market-style scoring where every replayed output is priced by agreement across models, yielding an oracle-free quality signal.
3. Self-consistency entropy per prompt as a proxy for difficulty and expected quality when no reference answer exists.
4. Divergence-based anomaly detection that flags malformed or hallucinated outputs by their distance from the model's own output distribution.
5. LLM-as-judge calibrated on the client's own historical accept/reject decisions rather than on generated gold labels.
6. Cross-model win-rate tournaments on replayed traffic to build preference rankings (ELO) with no reference answers.
7. Conformal-prediction bands around replay quality scores so every per-request measurement carries a certified confidence interval.
8. Elicited-behavior testing that scores outputs by downstream action success (click, pass, apply) instead of surface text match.
9. Inter-rater reliability across multiple judge models as the confidence signal, eliminating single-judge bias entirely.
10. Weak-to-strong label imputation that extrapolates labels from a cheap model to score a strong model on unlabeled traffic.
11. Programmatic weak supervision sourced from the client's own schemas, taxonomies, and validation rules as near-free gold.
12. Behavioral fingerprinting where a request's latent quality vector is required to be stable across models, catching inconsistency.
13. Replay-based drift detection that measures quality shift when upstream data or vendors change, with zero reference labels.
14. Counterfactual dueling where two models answer identical prompts and a judge determines relative strength, gold-free.
15. Per-request difficulty classification that routes cheap models to easy traffic and reserves expensive models for hard traffic.
16. Contextual bandits over the model pool with quality as reward and token cost as penalty, learned online.
17. Thompson-sampling multi-armed bandit that explores/exploits model choices under a per-tenant budget constraint.
18. Confidence-gated escalation that promotes a request to a large model only when the small model's confidence is low.
19. Latency-aware routing that jointly optimizes quality and p99 latency, not just dollar cost.
20. Cascade routing with early exit: small model answers first, big model is invoked only on low-confidence cases.
21. Learning-to-route by imitating an offline oracle that always picks the true best model per request.
22. Bandit with explicit switching costs to prevent policy thrashing between models mid-traffic.
23. Routing keyed on vector-similarity to a memory bank of historically "hard" examples.
24. Adversarially robust routing that bounds worst-case regret per tenant rather than average regret.
25. Federated routing-policy learning that shares only gradients/aggregates across tenants, never request content.
26. A cost-quality Pareto-front explorer that surfaces non-dominated routing policies for the client to choose from.
27. Adaptive budget reallocation (Eddington-style) that shifts spend from easy to hard traffic in real time.
28. Fully homomorphic encryption of replay payloads so Jauge computes quality scores without ever seeing plaintext prompts.
29. Trusted-execution-environment replay runners deployed inside the client's enclave, leaving only signed attestations to Jauge.
30. Differential privacy over measured metrics to hide any single request's contribution to published scores.
31. Zero-knowledge proofs that a reported quality metric was computed faithfully from real replay data.
32. Secure multi-party computation that aggregates cross-client benchmarks without any party revealing raw traffic.
33. An on-prem replay engine shipped to the client, with cryptographic attestations as the only evidence Jauge receives.
34. Strict data-plane/control-plane separation where only encrypted payloads transit Jauge's infrastructure.
35. Client-side enclave signing that proves replay provenance so a metric cannot be forged after the fact.
36. Privacy-preserving set intersection across tenants to discover shared "hard" prompts without exposing them.
37. Per-tenant key management such that even Jauge staff are cryptographically prevented from decrypting payloads.
38. Confidential computing used to evaluate the routing policy itself, not just the replay.
39. Federated evaluation that aggregates quality deltas across clients while raw outputs never leave each client.
40. Homomorphic argmax executed on encrypted embeddings so routing decisions are made blind to request content.
41. Client-specific distillation targeted at each tenant's heaviest-hitting query classes, not a generic student.
42. Distilling only the head (heavy-hitter) traffic to slash cost while keeping the long tail on the full model.
43. Replay-driven distillation that uses a client's real traffic as the supervised teacher-student dataset.
44. Speculative decoding where a small student drafts and the large model verifies only a sparse sample.
45. Per-tenant distillation (never global) to preserve the confidentiality and isolation guarantees.
46. Teacher-ensemble distillation so the student is robust to any single vendor's hidden biases.
47. Adaptive precision/quantization as a cost lever, verified against replay quality thresholds before rollout.
48. Semantic caching and deduplication of near-identical requests to avoid paying twice for the same answer.
49. Prefix caching on the replay pipeline itself to amortize the cost of re-measuring similar prompts.
50. Distillation down to an on-prem small model for data that is legally barred from leaving the client.
51. A counterfactual cost model that answers "what would spend be under every alternative routing policy" on real traffic.
52. Causal estimation of prompt-change effects on cost and quality using do-operator style interventions.
53. Instrumental-variable methods to estimate true quality uplift from routing changes despite confounding traffic.
54. Uplift modeling per request to identify exactly which requests actually gain from a bigger model.
55. Surplus decomposition that separates quality gained from cost saved for any given switch.
56. Causal attribution of a quality dip to a specific upstream model-version bump, not to traffic shift.
57. Synthetic-control estimation of Jauge's own value-add versus a no-intervention baseline.
58. Regression-discontinuity around budget caps to measure the causal quality-elasticity of spending.
59. Counterfactual replay across a full historical window to price every alternative the client never tried.
60. Attribution of total savings between Jauge's actions and organic vendor price drops.
61. An internal market where tenants trade compute/quality budget expressed in replay-verified tokens.
62. Priority auctions for burst capacity during peak load, priced by marginal quality.
63. Shadow pricing of quality: an internal token whose price reflects the marginal cost of one quality point.
64. Expiring budget tokens that smooth demand spikes and prevent hoarding.
65. A secondary market for unused replay-compute slots among tenants.
66. Cost-sharing pools for shared models across tenants, with cryptographic attribution of each share.
67. Per-tenant bid/ask spreads on latency tiers, letting them trade speed for cost dynamically.
68. Cap-and-trade over total monthly spend with tradable tenant allowances.
69. Automated vendor-discount negotiation driven by replay evidence of actual consumption and quality.
70. Reserve pricing for guaranteed-latency service tiers, auctioned off capacity.
71. Auto-grown per-domain tiny benchmarks distilled from each tenant's own real traffic.
72. Few-shot benchmark distillation yielding ~50 prompts that faithfully surrogate a full regression suite.
73. Domain-specific ELO ladders (healthcare, legal, finance) for model ranking on specialized traffic.
74. Auto-curated "killer prompt" sets chosen to maximally separate competing models.
75. Continuously refreshed benchmarks that expire stale items as the traffic distribution drifts.
76. Benchmark personalization to a tenant's own role vocabulary and jargon.
77. Published cross-model compatibility scores per domain, surfaced as usable prices.
78. Difficulty-tiered benchmark suites (easy/medium/hard/insane) for fine-grained ranking.
79. Adversarially hardened benchmark sets resistant to vendor gaming or training-set contamination.
80. Drift-aware benchmark refresh triggered automatically when distribution shift is detected.
81. Automatic prompt rewriting verified by replay to cut token count while holding quality flat.
82. System-prompt compression as a measured cost lever, accepted only when replay shows no quality loss.
83. Per-query few-shot example selection optimized through replay over a candidate pool.
84. Prompt-cache placement optimization deciding where to pin static prefixes for cheapest replay/hits.
85. Retrieval-augmentation tuning that decides how many documents to stuff, measured by replay quality curves.
86. Semantic compression of long contexts before the model call, replay-verified against quality degradation.
87. Output-format steering to shorten completions without losing user-visible quality.
88. Gold-free A/B testing of prompt variants through the replay harness, treating it as a controlled trial.
89. Persona/style detuning to shave tokens on traffic where the user is indifferent to tone.
90. Instruction fusion that merges multiple micro-prompts into a single cheaper call, verified by replay.
91. A "model ratings agency" publishing independent quality/cost ratings backed by replay evidence.
92. Energy and carbon accounting alongside dollar cost, derived from replay-measured compute.
93. SLA-backed quality contracts whose terms are priced and enforced by replay measurements.
94. A model-agnostic quality index — a LIBOR-like benchmark for GenAI output quality.
95. Cross-replay cost-delta detection to expose vendor price arbitrage opportunities.
96. An insurance product where clients pay a premium and Jauge guarantees a floor of verified savings.
97. Outcome-based pricing: Jauge charges only a share of the savings it can prove via replay.
98. Replay-as-a-service for third-party auditors needing audit-grade model evidence.
99. A public vendor leaderboard with cryptographic provenance certificates behind every number.
100. Forensic replay that settles prod incidents by answering "which model version broke production?"
101. Continuous goodness-of-fit monitoring of proxy quality metrics against rare real gold labels.
102. Active learning where Jauge requests the minimum set of targeted labels from a client for maximum information.
103. Uncertainty-quantified quality bounds published for every model-request combination.
104. Replay sampling theory to compute the minimal sample size to certify a route at a given confidence.
105. Sequential probability ratio tests for early-stopping model rollouts the moment a winner is clear.
106. Multi-objective routing treated as constrained optimization over quality, cost, latency, and privacy.
107. Automatic model-version freezes when drift detection catches a vendor silently changing outputs.
108. Changepoint detection on replay-score time series to alert on vendor regressions.
109. Injected canary requests that detect silent vendor model swaps or A/B games.
110. A cross-tenant "model weather" report from aggregated, differentially-private replay signals.
111. Escalation trees — multi-level fallback chains — optimized as a single dynamic program, not hand-tuned.
112. Diversification hedging that routes to avoid single-vendor lock-in and correlated failure risk.
113. Replay-tested cross-vendor automatic failover that insures against vendor outages.
114. A latent difficulty embedding shared across tenants via federated learning, learned privately.
115. Honeypot prompts designed to catch vendors gaming the evaluation with memorized answers.
116. "Quality basis points" as a tradeable internal metric that prices marginal quality.
117. Replay-based procurement scoring that ranks vendors for clients before they commit.
118. Auto-generated compliance and model-comparison reports from replay evidence.
119. Pre-computed routing lookup tables refreshed on schedule for low-latency decisioning.
120. Quality-aware load shedding that degrades or drops the lowest value-density traffic first.
121. Value-density scoring that ranks every request by quality-per-dollar to prioritize spend.
122. Aggressively cheap models on long-tail rare requests where the aggregate savings outrun the risk.
123. Streaming early-exit that stops generation the instant confidence crosses a threshold.
124. Context-window economics that measure the quality-vs-context-length curve to price long inputs.
125. A "regression budget" that spends replay tokens proactively to prevent production regressions before they ship.
