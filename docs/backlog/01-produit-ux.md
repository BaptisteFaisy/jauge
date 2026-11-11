# Backlog — Produit & expérience utilisateur

> Portail, tableaux de bord, parcours, alertes, onboarding, simulation, exports.
>
> 125 idées générées le 2026-11-11 par un sous-agent dédié à cet axe,
> à partir de l'état réel de `main`. **Non priorisées** : c'est un réservoir, pas un plan.
> Le top 10 retenu est dans [`README.md`](README.md).

1. **First-run "Connect Bedrock" wizard** that ingests the customer's Bedrock invocation logs read-only via a pre-scoped IAM role and shows a live cost preview before any data leaves the account.
2. **"Dollar-to-app" attribution map** that renders every inference dollar as a nested tree: application → team → request type → model ID, expandable down to a single log line.
3. **One-click "Prove it" button** on any model tile that launches a replay comparison of that model against cheaper candidates using the customer's own sampled traffic.
4. **Wilson confidence score gauge** shown as a percentage dial on every model recommendation, with the sample size and margin of error explicitly labeled for the MSP sales call.
5. **Rollout staging slider** (5% → 25% → 50% → 100%) that turns a proven migration into an AgentCore gateway rule without leaving the portal.
6. **Constant-volume savings tracker** that compares post‑migration spend against a volume-normalized baseline so the customer sees savings independent of traffic growth.
7. **"Unattributed spend" spotlight panel** that surfaces dollars with no team/app tag as a red line item with a one-click "fix now" flow.
8. **MSP white-label header** replacing Jauge branding with the MSP's own logo, colors, and support email per customer tenant.
9. **Per-customer tenant switcher** in the MSP's global header so one MSP engineer hops between all client accounts without re-login.
10. **Request-type tag taxonomy** that auto-group similar untagged prompts into suggested categories instead of forcing manual labeling.
11. **Data-residency status badge** ("nothing leaves your VPC") shown persistently beside every chart to reinforce the on-premise data story.
12. **Model "shelf price" vs "actual blended cost" toggle** comparing list price to the customer's real committed/spend-based effective rate.
13. **Savings opportunity heat map** plotting every model × app pair by monthly spend and candidate-switch upside, sized by dollar value.
14. **"Cheaper but just as good" recommendation cards** pairing each candidate with the concrete evidence that backs it (replay score + judge agreement + deterministic checks).
15. **Lifecycle stage filter bar** (attribute → prove → apply → monitor) so users jump straight to where a given app sits in the Jauge journey.
16. **Budget waterfall chart** showing plan vs actual vs forecast for a team, with the variance broken into model, volume, and rate effects.
17. **Configurable per-team budget thresholds** with daily, weekly, and monthly envelopes that differ per application and request type.
18. **Proactive threshold alerts** that fire at 70/90/110% of budget and land in email, Slack, and the portal bell with a direct link to the offending app.
19. **"Who's burning budget" leaderboard** ranking teams by spend velocity (current run-rate vs plan) to drive internal accountability conversations.
20. **Model-usage funnel** from total requests → sampled requests → replayed requests → judged requests, exposing attrition at each proof step.
21. **Judge-vs-live outcome diff viewer** showing where the judge LLM disagreed with deterministic checks, flagged for the MSP to review before committing.
22. **Export-ready "proof pack" PDF** bundling replayed sample, judge scores, Wilson interval, and projected savings into one shareable deliverable per migration.
23. **Shareable read-only report link** with expiring tokens so a non-seat stakeholder can open a dashboard without billing for a full user seat.
24. **Scheduled report email** (daily digests to finance, weekly to engineering) with configurable recipients and a "no email if under threshold" quiet mode.
25. **Notification preference matrix** letting each user pick per-channel (email/Slack/pager) and per-severity delivery independently.
26. **Anomaly detection feed** flagging sudden spend spikes per app/model with a suggested cause hint (e.g. "volume +40%, same model" vs "model switched").
27. **"Impact of my change" before/after panel** that shows the actual dollar delta of a just-applied gateway rule after the first 24 hours.
28. **Rollback button on live gateway rules** that reverts the last tier instantly and records the reason, tying the revert to a cost impact line.
29. **Change-log timeline per application** listing every rule, tier, threshold edit, and tag change with who did it and the resulting spend effect.
30. **RBAC seat types** (MSP engineer, customer admin, team lead, viewer, finance) with distinct default landing pages matching each role's job.
31. **Single sign-on (SAML/OIDC)** onboarding that auto-provisions the customer's first admin from their IdP and syncs team groups into Jauge teams.
32. **Team mapping importer** from the customer's existing tag convention (CSV or IdP group) so teams exist on day one instead of being hand-created.
33. **Guided onboarding checklist** with a progress bar (connect logs → import teams → set first budget → prove first model) that gates nothing but shows momentum.
34. **MVP "read-only trial" mode** where the MSP enables attribution charts first and defers prove/apply until after a 14-day observation window.
35. **Cost simulation "what-if" calculator** that takes current traffic and lets users drag a model swap or traffic growth to project next-quarter spend.
36. **"Switch me" scenario builder** comparing up to three candidate models side by side on projected cost, quality score, and risk in one matrix.
37. **Reserved commitment optimizer** suggesting which model mix to move onto provisioned throughput based on observed steady-state volume.
38. **Cross-region price delta checker** surfacing when the same model is cheaper in another Bedrock region the customer already operates in.
39. **Latency-aware recommendations** that only suggest a cheaper model when its observed replay latency stays within the app's configured SLO.
40. **Per-app quality SLO input** (e.g. "exact-match answers only") that filters which candidate models are even eligible to be proposed.
41. **Hallucination/refusal guardrails panel** showing deterministic-check failure rates per candidate so the MSP can preempt quality regressions.
42. **"Saved so far" cumulative counter** pinned to the top of every customer dashboard, computed from rules ever applied at constant volume.
43. **Savings proof-obligation tracker** listing each "applied" migration still awaiting its 30-day verification window, with a due date.
44. **Chargeback-ready export** in the customer's own cost-center format (CSV matching their CUR 2.0 columns) for handoff to their finance team.
45. **FinOps/Lens-style maturity score** ("observe → attribute → optimize → automate") computed from how fully Jauge is deployed across the estate.
46. **Dark-mode and density toggles** tuned for the two core audiences: executives skimming big numbers and engineers drilling into log detail.
47. **One-glance "pulse" overview card** per app showing spend, top model, budget health, and next recommended action in four compact tiles.
48. **Searchable global command palette** (app name, model ID, team, or request type) that jumps to any dashboard in one keystroke.
49. **Embedded Bedrock log line inspector** showing the raw invocation log behind any aggregated number, with request tags extracted inline.
50. **Tag coverage % meter** per app nudging toward 100% attribution with the exact few tags that would close the gap.
51. **Duplicate-tag conflict resolver** detecting when two teams claim the same request tag and prompting the admin to arbitrate ownership.
52. **Cost attribution override manager** letting a finance seat reassign a disputed dollar amount and stamping the override for audit.
53. **"Explain this number" tooltip** on every figure that walks down (invoice → CUR → logs → tags) so no chart is a black box.
54. **Comparable-period selector** (previous week/month/quarter, or a custom range) applied consistently across every view with one control.
55. **Seasonality toggle** that de-trends weekdays/holidays before computing anomalies and savings so weekend dips don't look like wins.
56. **Model retirement radar** warning when a model in use is deprecated or end-of-lifed, with a pre-built migration path and date.
57. **Bulk model migration canvas** letting an MSP apply the same proven switch across many apps/tiers at once instead of one at a time.
58. **Dry-run mode for gateway rules** that previews the exact requests that would be rerouted before any traffic actually changes.
59. **"What would this have cost last month" backtest** applying a new rule to last month's logs to show the savings it *would have* produced.
60. **Side-by-side before/after request samples** showing the same real prompt answered by the old and new model with judge scores under each.
61. **Natural-language cost question bar** ("Where is Team Data spending money this week?") answered with a scoped chart, not a generic search list.
62. **Weekly executive summary email** under 200 words with the top three numbers an operator needs: spend, worst budget, biggest opportunity.
63. **Customer-facing demo/sample tenant** pre-loaded with synthetic Bedrock data so a prospect can explore Jauge in seconds during a sales call.
64. **Onboarding "sandbox policy" toggle** that simulates a recommended rule's effect on copy data before the customer permits production mode.
65. **First-dollar-to-second-attribution sanity check** cross-validating attribution against the CUR 2.0 totals with a green/red reconciliation badge.
66. **Reconciliation diff view** showing any gap between attributed dollars and the bill line item, drilling to the unallocated remainder.
67. **Provider/invoice currency and timezone settings** per tenant so finance reads numbers in their local denomination and fiscal calendar.
68. **Bud-to-actual alert snooze** ("snooze until budget period resets") so teams aren't re-paged on a known overage all month.
69. **Escalation chain on critical alerts** (team lead → finance → MSP) with contact methods configurable in each tenant profile.
70. **Alert dedup digest** collapsing ten threshold breaches on one app into a single message with the worst offender listed first.
71. **Cost center tree editor** mirroring the customer's org chart so budgets and reports roll up exactly the way their finance team models it.
72. **Budget season handoff** letting admins copy last quarter's budgets and shift them forward with an optional growth multiplier in one click.
73. **"Over/under spend" variance indicator** on each team card with a simple arrow and % so a managers' landing page reads like a traffic light board.
74. **Model catalog page** listing all Bedrock models in use and available, with price, region, latency profile, and a Jauge "switch worth it" verdict per model.
75. **Quality threshold wizard** that helps a customer pick the acceptable regression tolerance before recommending any cheaper model.
76. **Sampling control** letting the MSP choose replay sample size (or let Jauge auto-size it to hit a target Wilson interval width).
77. **Hydration-vs-generation toggle** for request types so price-per-token comparisons are apples-to-apples across models with different token metering.
78. **Token-efficiency metric** ("tokens in → useful output") surfacing prompt bloat that inflates cost regardless of model choice.
79. **Prompt-length outliers list** flagging the 10 most expensive real prompts by input tokens so engineering can trim them.
80. **Per-request-type cost-per-unique-user metric** tying inference spend to real end users, not raw volume, for unit economics.
81. **Free-margin-to-serve calculator** showing contribution per request type so product teams see which features are loss-making at current model prices.
82. **"Apply to this team only" scope picker** on every recommendation so a rollout can target a friendly pilot team before the whole app.
83. **Pilot-vs-control group toggle** running the new model on a subset of traffic and comparing outcomes to the held-out control group.
84. **Automated rollback trigger** that reverts a gateway rule if the quality score falls below the configured floor for a sustained window.
85. **Quality regression incident feed** tied to a specific rollout, so the MSP sees "rollout 45% dip" as its own event with context.
86. **Post-migration confidence re-check** re-running the judge on post-switch live traffic to confirm lab results matched production.
87. **"Proof vs reality" gap report** quantifying any divergence between replayed quality and observed production quality after a switch.
88. **Savings certificate (social proof) generator** producing a client-branded one-pager the MSP can hand to its own sales team as a case-study attachment.
89. **Multi-tenant consolidated savings board** for the MSP itself, totaling attributed savings across all clients to demo aggregate value.
90. **Customer-facing API/activity status panel** showing ingestion freshness ("logs last synced 2 min ago") so users trust the numbers are live.
91. **Data ingestion lag alert** warning when Bedrock logs stop flowing or fall behind so dashboards never silently go stale.
92. **Retention and purge controls** visible in the portal so customers know exactly how long raw logs are kept and can shorten the window.
93. **Compliance note block** (SOC2 / data-locality statement) surfaced on the data-residency page alongside the "nothing leaves here" badge.
94. **First-run empty-state illustrations** per pillar (attribute/prove/apply) with a one-line CTA, so a new tenant is never a blank grid.
95. **Breadcrumb-heavy navigation** preserving the app→model→request-type drill path so deep dives are easily retraced and shared by URL.
96. **Deep-linkable dashboard state** where every filter/range lives in the URL so an MSP can paste a scoped view into a support ticket.
97. **Bookmarkable custom views** ("Team Data weekly spend") saved per user and visible on the left nav as shortcuts.
98. **Bulk notification digest frequency control** (instant, hourly rollup, daily, weekly) per alert severity to stop pager fatigue.
99. **Incognito "finance eye" toggle** hiding absolute dollar figures behind percentages for screen-share demos to non-authorized viewers.
100. **"Most improved" and "top saver" weekly badges** on teams that beat budget or adopted a cheaper model, gamifying cost discipline.
101. **Historical model-price snapshot** so savings math always uses the price that was in effect at the time, never today's price applied to last month.
102. **Price-change notification** alerting the MSP when a Bedrock model's price drops so they can re-run proofs and capture new upside.
103. **"Re-run this proof" button** on any historical recommendation to refresh the comparison with current prices and latest traffic.
104. **Rollout schedule planner** proposing safe migration windows (low-traffic hours) per app based on its observed traffic curve.
105. **Approval workflow on apply** requiring a named customer owner to sign off before any tier beyond dry-run goes live.
106. **Owner-aware recommendations** auto-assigning each savings opportunity to the team lead who can actually approve its rollout.
107. **Stakeholder summary tab** per recommendation listing who must sign off, what they'll see change, and the projected dollar impact for each.
108. **Service-level "trust tier" indicator** per customer (observe→attribute→optimize) used by Jauge to progressively unlock prove/apply features.
109. **Colocated code-free integration status** showing Bedrock log-source and AgentCore gateway health as green/yellow/red in one tile.
110. **Env selector (dev/staging/prod)** scoping dashboards per environment so noisy dev spend doesn't distort production budget reads.
111. **Per-model "spend concentration" list** showing which few prompts drive the majority of a model's cost (Pareto view).
112. **Long-tail request cleanup nudge** estimating savings from retiring or caching repeat near-identical cheap requests.
113. **Smart-cache hint** flagging high-frequency identical prompts as candidates for response caching to cut inference spend without a model change.
114. **Cross-app duplicate detection** finding the same prompt implemented in multiple apps and suggesting a shared cached path.
115. **Cost-per-workspace/per-dashboard widget** if Jauge itself is multi-tenant, giving each customer team its own scoped view of only their apps.
116. **Export format library** (CSV, Parquet, XLSX, and a raw CUR-mapped dump) each produced with one click from any filtered view.
117. **Scheduled export to cloud storage** (S3 bucket owned by the customer) so finance pipelines ingest Jauge numbers automatically.
118. **Webhook notifications** for alerts and daily digests so the MSP or customer can pipe Jauge events into their own tooling.
119. **In-app announcement rail** for the MSP to push updates, success stories, or new model support notes to specific tenants.
120. **Model-version pinning display** so a recommendation is always tied to an exact model version/ARN and never ambiguously names a family.
121. **"Confidence in attribution" percentage per dollar** (high from log tags, low from heuristics) so finance knows which numbers are firm.
122. **Heuristic-attribution transparency panel** listing every inference rule that classified untagged traffic, editable by the MSP.
123. **One-page "Jauge reset" for a new quarter** archiving last quarter into a frozen read-only snapshot and zeroing budgets cleanly.
124. **Offboarding/export-on-exit button** that dumps the customer's complete data (configs, rules, history) in a portable format on termination.
125. **Post-mortem-style cost story generator** that turns any big overage into an auto-written timeline ("spike → cause → action → result") for review boards.
