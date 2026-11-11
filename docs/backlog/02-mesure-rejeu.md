# Backlog — Science de la mesure & du rejeu

> Tout ce qui rend la preuve plus solide : juges, biais, variance, échantillonnage, statistiques.
>
> 125 idées générées le 2026-11-11 par un sous-agent dédié à cet axe,
> à partir de l'état réel de `main`. **Non priorisées** : c'est un réservoir, pas un plan.
> Le top 10 retenu est dans [`README.md`](README.md).

1. Measure with paired evaluation where the same request hits both models and comparison is made on identical inputs only, eliminating sampling noise from different prompts.
2. Report the Wilson confidence interval on the win/tie/loss rate of the candidate against the incumbent, not just a point estimate.
3. Require a minimum non-inferiority margin defined before the trial starts, so the cheaper model must be within a precommitted quality delta rather than "not obviously worse".
4. Use a pre-registered evaluation protocol (metrics, judge prompts, thresholds, sample plan) stored immutable before any rejeu runs to remove post-hoc tuning.
5. Re-run the same sample twice and measure judge agreement to quantify judge noise separately from model noise.
6. Disentangle three variance sources — model stochasticity, judge stochasticity, and sampling error — and report each with its own confidence interval.
7. Use deterministic judge anchors (exact string, JSON schema, numeric tolerance) as primary gates and reserve the LLM judge for semantic-only dimensions.
8. Report inter-judge agreement (Krippendorff's alpha or Cohen's kappa) when multiple judges score the same outputs.
9. Use an odd-numbered panel of judges with majority vote and require a supermajority before declaring a candidate answer acceptable.
10. Blind the judge to which model produced each output by shuffling and stripping all model identifiers.
11. Randomize output order left/right for pairwise preference judgments to cancel positional bias.
12. Test the judge for position bias, verbosity bias, and formatting bias on a calibration set before trusting its verdicts.
13. Calibrate the judge's confidence by comparing its predicted correctness against observed correctness on labeled gold data.
14. Deduplicate or dedup-flag repeated prompts in the sample so popular requests do not silently dominate the quality estimate.
15. Use stratified sampling by request type, app, team, and token-length bucket, with strata large enough to yield per-stratum confidence intervals.
16. Oversample rare but high-value request types (e.g. code generation, agentic reasoning) so long-tail failures are not hidden by frequent trivial requests.
17. Estimate and report the quality of the long tail separately, since mean quality can stay flat while rare hard cases regress.
18. Detect regression per request-type stratum rather than only at the aggregate level, surfacing localized quality loss in one app or team.
19. Compute an alert on any per-stratum win-rate drop beyond its own confidence band, not just the aggregate drop.
20. Use sequential/adaptive sampling (SPRT or group-sequential) to stop early when non-inferiority is already proven, and to extend when the margin is unclear.
21. Precompute the required sample size from a power analysis on the expected effect size and variance, not an arbitrary fixed number.
22. Set a minimum number of samples per stratum that is proportional to the dollar spend in that stratum, so proof weight matches cost weight.
23. Weight quality metrics by the actual cost of each request type so the headline proof is cost-weighted, aligning with the FinOps goal.
24. Measure the correlation between quality and cost per request and report it, so it is known whether cheaper also means simpler/easier requests.
25. Use inverse-probability / importance weighting when the rejoué sample differs from production traffic distribution to correct population bias.
26. Replay full multi-turn and agentic trajectories, not single isolated turns, to catch compounding errors that one-shot eval misses.
27. Replay tool-calling sequences and verify that the candidate calls the correct tools with correct arguments, not only final text.
28. Evaluate instruction-following and constraint adherence deterministically (schema, allowed actions, format), which are cheap and objective gates.
29. Add a "refusal safety" check: the cheaper model must not refuse or over-refuse requests the incumbent handles correctly.
30. Add a "hallucination/faithfulness" check against the provided context for RAG-style requests using entailment or citation verification.
31. Use a second, independent judge model from a different vendor to cross-check the primary judge's verdicts.
32. Use the incumbent model itself as one of the judges, but never as the sole judge, to avoid self-preference bias.
33. Disqualify any judge from scoring its own outputs by construction.
34. Compute judge bias per model by measuring the judge's win-rate on identical-quality outputs, and correct for asymmetry.
35. Report the "ground-truth" fraction of the sample — items verifiable by unit tests or reference answers — and weight those higher.
36. Synthesize reference answers from expensive frontier models on a small gold subset and treat agreement with reference as a proxy for correctness.
37. Build a labeled regression suite of curated prompts with known-correct answers that every model change must pass deterministically.
38. Version the evaluation harness, the judge prompt, and the sample set together, and require the same versions when comparing across time.
39. Store raw inputs, outputs, judge scores, and seed values in an append-only, tamper-evident audit log for the proof.
40. Record the exact temperature, top-p, and seed used for both models so variance estimates are reproducible.
41. Run each model with a fixed seed set and report sensitivity across seeds, so a lucky seed cannot produce a false win.
42. Report the distribution of per-request score differences, not just the mean, to expose a fat tail of regressions hidden by the average.
43. Detect and flag "safe wins" — requests both models get right — separately from "risky" requests where only one model is correct.
44. Identify requests where the candidate is better and quantify whether those wins are economically meaningful or just noise.
45. Flag requests where the candidate is worse and trace them to a specific failure mode (reasoning, math, format, length).
46. Bucket failures by error taxonomy and report the Pareto of error causes the candidate introduces versus the incumbent.
47. Use a correctness rubric with granular, pre-scored dimensions rather than a single pass/fail, to make degradations measurable.
48. Add a "does the response actually solve the task" functional check (e.g. code compiles/executes/tests pass) as the highest-weight criterion.
49. For code outputs, execute generated code in a sandbox and treat passing unit tests as a deterministic ground-truth gate.
50. For math/quantitative outputs, compare against a symbolic/numeric solver result as a deterministic ground-truth gate.
51. For JSON/structured outputs, validate against the JSON Schema and treat schema violations as hard, non-negotiable failures.
52. Include a latency and token-cost measurement per rejoué request so the proof covers the full cost-quality frontier, not cheapness alone.
53. Report quality-per-dollar as the headline metric, so readers compare value directly rather than raw quality.
54. Control for prompt length and difficulty using a difficulty score (e.g. incumbent's own error rate) when comparing models.
55. Match or block on difficulty when sampling so both models face the same difficulty distribution.
56. Exclude or separately report requests the incumbent itself fails, since a cheaper model cannot be expected to beat a hard baseline.
57. Use a "known answer" ASR-style metric: candidate must produce a response containing the reference answer span or equivalent meaning.
58. Add a semantic-equivalence judge check against the reference answer rather than exact match to allow valid paraphrases of correct answers.
59. Use multiple prompt variants per logical request to average out prompt-sensitivity and report prompt robustness.
60. Test sensitivity to instruction rephrasing on a subsample, so the proof is not an artifact of one particular prompt wording.
61. Include adversarial/boundary cases in the sample (edge inputs, empty, long, mixed-language) to prove robustness at the margins.
62. Measure abstention/uncertainty behavior: the cheaper model should not produce confident wrong answers the incumbent flags as uncertain.
63. Report calibration of confidence: when each model says "high confidence," how often is it actually correct.
64. Add a chain-of-thought traceability check: verify the reasoning steps are logically consistent, not just that the final answer is plausible.
65. Evaluate intermediate steps for multi-step reasoning tasks using a per-step judge, catching errors masked by a lucky final answer.
66. Compare the two models' error rate on the specific reasoning step where incumbent is known to be strong, to find hidden step-level regressions.
67. Run the rejeu on a holdout window of production traffic that was excluded from any prior tuning, preventing training-set contamination.
68. Use recent production traffic (last N days) for the replay window so results reflect current workload, not historical patterns.
69. Freeze the sample once drawn and never re-select, so evidence cannot be cherry-picked after seeing results.
70. Use cross-validation of the evaluation itself: split judges/sample and check the conclusion is stable across folds.
71. Bootstrap the per-stratum and aggregate metrics to produce honest confidence intervals from the actual score distribution.
72. Compute the number needed to flip: how many requests would have to change outcome to overturn the non-inferiority conclusion.
73. Sensitivity analysis: report how the verdict changes under alternative weighting (equal weights, cost weights, spend weights).
74. Report the verdict under the most pessimistic plausible weighting to show the conclusion is not an artifact of a chosen metric.
75. Add a cost floor measurement: verify the savings persist at constant volume by comparing like-for-like volume before and after cutover.
76. Run the candidate and incumbent on the identical snapshot of requests with the identical judge on the same day/hour to control drift.
77. A/B the judge prompt itself to prove the judge is not overfitting to one phrasing of the rubric.
78. Assign each judge a randomized subset and a fixed overlap for anchor agreement, so judge reliability is measured continuously.
79. Tag a small subset of known-gold items into the scored set as a "trap" to detect judge drift or a judge that rates randomly.
80. Monitor judge drift over the course of a long trial and exclude or re-calibrate judges whose agreement with gold decays.
81. Report the judge's false-accept and false-reject rates against labeled gold before accepting its quality verdict.
82. Use only outputs generated in the same environment and model version/chunk that will be deployed, so lab results match production.
83. Pin the candidate model version, temperature, and reasoning-effort settings to the exact production config throughout the proof.
84. Add shadow traffic: route a live copy of production requests to the candidate in parallel and diff silently before switching.
85. Run a canary: route a small, reversible percentage of live traffic to the candidate with an automatic rollback on quality dip.
86. Use a multi-armed bandit with the incumbent as control over a bounded window, letting real outcomes decide allocation.
87. Define a precommitted rollback trigger (e.g. per-stratum win-rate below threshold) before any canary starts.
88. Compare candidate vs incumbent on the rate of human escalations or support tickets after shadow deployment, as a real-world quality signal.
89. Measure downstream task success (e.g. order completed, code merged, ticket closed) rather than only response quality.
90. Include a "did the user have to re-prompt" signal: count retries/follow-ups as a negative quality indicator for both models.
91. Quantify token inflation: check the cheaper model isn't "as good" only by emitting far more tokens (hiding cost in volume).
92. Normalize quality by tokens consumed, so a model that burns more tokens to reach the same answer doesn't get credit for free.
93. Verify the savings are net of any increased token consumption or latency, not a gross list-price comparison.
94. Test convergence: for a subset, sample multiple candidate outputs and check whether majority answer equals the incumbent's answer.
95. Use self-consistency (multiple sampled answers per request) as a judge-free signal of the candidate's answer reliability.
96. Report the candidate's self-consistency rate vs the incumbent's, as it correlates with correctness and reduces dependence on a flawed judge.
97. Detect "one of the samples agrees" cherry-picking by always comparing with all samples, never a selected best sample.
98. For pairwise preference, use a Bradley-Terry model to estimate a stable win probability and its uncertainty across the sample.
99. Estimate the minimum detectable regression given the sample size and variance, so readers know what the trial could have missed.
100. State the statistical power achieved for each hypothesis (overall and per stratum) rather than only reporting a pass.
101. Report effect sizes with 95% confidence intervals, not just p-values, for every comparison made.
102. Use multiplicity correction (e.g. Holm-Bonferroni or false-discovery-rate control) when testing many strata to avoid false positives.
103. Report both a per-stratum family-wise and aggregate result so a single lucky stratum cannot drive the conclusion.
104. Separate exploration findings from confirmatory results; any hypothesis generated post-hoc must be marked as exploratory, not proof.
105. Include negative results explicitly: show which slices regressed, even if the aggregate stays acceptable.
106. Add a time-of-day/day-of-week stratification to ensure the cheaper model performs at peak load, not just average load.
107. Stratify by language, region, and locale if the workload is multilingual, so a localized regression is visible.
108. Stratify by input modality and content type (image+text, long context, code, JSON) to prove coverage across modalities.
109. Add a long-context stress stratum to prove the cheaper model retains fidelity on very long inputs, where cheap models commonly degrade.
110. Include a latency-SLA check per request type, so quality proof doesn't come with an unacceptable latency regression.
111. Report availability and error rates (timeouts, throttles, malformed responses) for the candidate vs incumbent in the rejeu.
112. Measure the candidate's adherence to the safety/content policy on the sample, so cost savings don't come with higher policy-violation risk.
113. Add a red-team slice of adversarial or jailbreak-style prompts to prove the cheaper model isn't more easily manipulated.
114. Compare the two models' confidence calibration via reliability diagrams on the same scored sample, exposing overconfidence in the cheaper model.
115. Measure the rate of "silent" wrong answers that look plausible to a naive judge, using a stronger judge to catch them on a subsample.
116. Use a stronger-than-both frontier model as an arbiter on disagreements between candidate and incumbent to resolve close calls.
117. Report inter-model agreement (candidate vs incumbent raw) as a cheap signal: high agreement reduces dependence on judge quality.
118. Compute a "cost of quality" curve across model tiers (price vs judged quality) so the cheaper model can be shown as on the frontier.
119. Test the next-cheaper tier too, so the recommendation picks the cheapest model that still meets quality, not just any cheaper one.
120. Validate the cost model against the CUR 2.0 invoice independently of the quality trial, so the dollar-savings claim is separately audited.
121. Reconcile rejoué token counts against billed token counts to prove the replay reflects real spend, not idealized tokens.
122. Require the trial to be reproducible by a third party from the audit log, sample, and harness versions alone.
123. Publish the protocol, metrics, and thresholds alongside results so the proof is independently reviewable and falsifiable.
124. Include a per-stratum win/loss/tie confusion-style table for transparency, showing exactly where each model wins, loses, and ties.
125. Conclude with a pre-specified decision rule (e.g. "proceed only if aggregate and every high-spend stratum is non-inferior") so the verdict follows automatically from the data.
