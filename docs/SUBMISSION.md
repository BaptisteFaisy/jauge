# Jauge — submission checklist

**Event:** AWS MSP AI Hackathon — **Track 2**, *Deliver AI-Driven Outcomes for Customers*
**Special prize targeted:** Best Strands SDK Implementation
**Deadline:** Friday 11 December 2026, 12:00 PT (21:00 Paris)
**Judging ends:** 22 January 2027 — the solution must stay online until then.

This page has two parts: the submission checklist (§19.1 of the specification) and
the one-sentence answer to each of the four judging criteria (§19.2).

---

## 1. Submission checklist

Legend — **Done**: already true in the repository. **Partly done**: code
delivered, a live step remains. **Pending**: needs a human action outside the
repository (AWS deployment, video, Devpost submission). Every module of
`docs/MODULES.md` (M1–M18) is merged on `main`; no line waits on code any more.

### 1.1 Track and description

| # | Item | Status | Where / action |
|---|---|---|---|
| 1 | Track 2 selected, AWS AI services listed | **Pending** | Devpost form. Services to list: Strands Agents, Amazon Bedrock (invocation logs, model evaluation with an LLM judge, service tiers, batch), AgentCore Runtime, AgentCore Gateway |
| 2 | English description: features, problem, measured results | **Pending** | Draft from [`README.md`](../README.md) and [`HLD.md`](HLD.md) §1; measured results only after the shoot table in [`DEMO-SCRIPT.md`](DEMO-SCRIPT.md#43-numbers-to-write-down-during-the-shoot) is filled |
| 3 | Public portal URL + demo credentials in the test instructions | **Done (static)** | https://baptistefaisy.github.io/jauge/ — published on GitHub Pages from this repository, serving a snapshot recorded from the real API. No credentials needed: the client/role selector is on the page. A **live** URL (real API, Bedrock replay, gateway rules) still needs the AWS deployment |
| 4 | Repository accessible to the judges (`testing@devpost.com`, `aws-msp@amazon.com`) | **Done** | The repository is **public**: https://github.com/BaptisteFaisy/jauge — no invite needed. Put this URL in the Devpost form |
| 5 | Code actually calls an AWS AI service at runtime | **Partly done** | The real adapters are in the repository: `BedrockModeleClient` (`bedrock-runtime`, `Converse`) in `src/jauge/replay/client.py`, and `BedrockJugeClient` (Bedrock evaluation jobs + S3 dataset) in `src/jauge/evaluation/judge.py`. What remains: run them **live** against AWS in the demo environment and show it in the video — the test suite stays offline on purpose |
| 6 | ~3-minute video, public on YouTube, English, no third-party brand or music | **Pending** | Script ready: [`DEMO-SCRIPT.md`](DEMO-SCRIPT.md) |
| 7 | Architecture diagram | **Done** | [`architecture.mmd`](architecture.mmd) + [`architecture.svg`](architecture.svg), explained in [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 8 | High-Level Design document | **Done** | [`HLD.md`](HLD.md) |
| 9 | Test instructions for judges | **Done** | [`TEST-INSTRUCTIONS.md`](TEST-INSTRUCTIONS.md) — reflects the real state of `main`: full offline test suite, seeded console demo and single-command `uvicorn jauge.api.app:app` portal |
| 10 | README as submission front page | **Done** | [`README.md`](../README.md) |
| 11 | Solution kept online until 22 January 2027 | **Pending** | Minimal traffic baseline from January; AWS Budgets alarms delivered as code per account (§17.3) |

### 1.2 Repository hygiene before submission

| # | Item | Status |
|---|---|---|
| 12 | `bash scripts/ci-local.sh` green | **Done** on every PR |
| 13 | No secret in the repository (keys, account ids, tokens) | **Done** — checked by review; no credential is committed |
| 14 | No customer data: the demo traffic is synthetic | **Done** |
| 15 | License stated (proprietary) | **Done** — `pyproject.toml`, `README.md` |
| 16 | `docs/MODULES.md` reflects what is delivered | **Done** — status per module |
| 17 | Every requirement cited (`EF-*`, `ENF-*`, `R*`) traces to code or a document | **Done** — see the traceability table in section 3 |

---

## 2. The four judging criteria (25 % each)

One sentence each, with the pointer to the evidence.

### 2.1 Technical implementation

> Jauge composes Strands agents (an orchestrator plus four specialists used as
> tools) on AgentCore Runtime, and uses Amazon Bedrock in depth — invocation logs
> for attribution, model-evaluation jobs with an LLM judge for quality, service
> tiers for replay cost, several models per campaign — while the model switch
> itself is a weighted AgentCore Gateway rule rather than a code change.

**Evidence:** [`ARCHITECTURE.md`](ARCHITECTURE.md#8-agents) (agent topology and
tool list) · [`HLD.md`](HLD.md#4-aws-components-and-responsibilities) (component
table) · [`HLD.md`](HLD.md#9-staged-rollout-and-rollback) (gateway rule, stages,
rollback) · video segment 0:50–2:05 · screenshots *Step Functions execution*,
*evaluation job*, *gateway rule*, *AgentCore console*.

### 2.2 Design

> Jauge is built on two planes with privacy enforced structurally — the MSP
> cross-account role cannot read the log or result buckets, so request text cannot
> leave the customer account even by mistake — with one account per customer,
> infrastructure as code, mandatory human approval before any traffic change, and
> automatic rollback when online quality drops.

**Evidence:** [`HLD.md`](HLD.md#3-two-planes-privacy-by-construction) and
[`HLD.md`](HLD.md#10-security) · [`ARCHITECTURE.md`](ARCHITECTURE.md#2-the-two-planes)
· the IAM screenshot showing the role's policy without read access to the log
bucket · negative tests `R10` / `R11` (M15) · the `Store` protocol taking
`tenant_id` as first argument (`src/jauge/store/protocol.py`).

### 2.3 Impact

> Jauge turns an opaque inference bill into a per-application number, a measured
> quality figure next to it, and a saving verified at constant volume on the
> invoice — so the decision to change a model is made on evidence rather than on
> opinion.

**Evidence:** the bill-reduction percentage measured during the demo (fill the
table in [`DEMO-SCRIPT.md`](DEMO-SCRIPT.md#43-numbers-to-write-down-during-the-shoot)
— until then the README shows the 20–40 % band as a **target**, not a result) ·
quality measured alongside it in the same table · [`HLD.md`](HLD.md#1-context-and-objectives)
(indicator definitions) · [`HLD.md`](HLD.md#7-attribution-model-and-bill-reconciliation)
(how the number is reconciled).

### 2.4 Innovation

> The proof by replay — re-running the customer's own requests against candidate
> models, judging equivalence with a confidence interval and a self-agreement
> baseline, then switching traffic through a gateway rule without touching the
> application's code — is what no cost dashboard does, and it is what turns a
> suggestion into a decision.

**Evidence:** [`HLD.md`](HLD.md#8-quality-measurement-method) (sample, judge,
deterministic checks, decision rule, self-agreement) · [`HLD.md`](HLD.md#9-staged-rollout-and-rollback)
· video segment 0:50–2:30 (including the rejected candidate) ·
`engine/stats.py` (`intervalle_wilson`, `taille_echantillon_minimale`) as the
deterministic core of the claim.

---

## 3. Traceability — requirement to evidence

| Requirement | Evidence |
|---|---|
| `EF-ATT-04` request cost from a dated price table | `src/jauge/engine/calculator.py`, `src/jauge/models/pricing.py`, `tests/engine/test_calculator.py` |
| `EF-ATT-05` daily aggregation | `src/jauge/engine/attribution.py` (`agreger_requetes`), `src/jauge/models/cost.py`, `tests/engine/test_attribution.py` |
| `EF-ATT-06` bill reconciliation | `src/jauge/engine/attribution.py` (`rapprocher_facture`), `RapprochementJour` |
| `EF-ATT-07` unattributed spend and causes | `RapprochementJour.part_non_attribuee_pourcentage`, `causes_non_attribuees` |
| `EF-REJ-01` stratified reproducible sample | `src/jauge/replay/sampling.py`, `tests/replay/test_sampling.py` |
| `EF-REJ-02`, `EF-REJ-03`, `EF-REJ-04` replay, Flex tier, measurements | `src/jauge/replay/executor.py` (+ `client.py` boto3 adapter, `cost.py`, `statemachine.py`), `tests/replay/` |
| `EF-EVA-01`, `EF-EVA-02` judge and deterministic checks | `src/jauge/evaluation/judge.py`, `checks.py`, `tests/evaluation/test_judge.py`, `test_checks.py` |
| `EF-EVA-03` rate and confidence interval | `src/jauge/engine/stats.py` (`intervalle_wilson`), `ResultatCandidat.intervalle_confiance_*`, `tests/evaluation/test_scoring.py` |
| `EF-EVA-04` natural variability of the current model | `src/jauge/replay/executor.py` (the current model is replayed twice), `ResultatCandidat.auto_accord_modele_actuel` |
| `EF-EVA-05` human review and judge/human agreement | `src/jauge/evaluation/human_review.py`, `tests/evaluation/test_human_review.py` |
| §12 agents as tools, guardrails, traces | `src/jauge/agents/` (`orchestrator.py`, `tools*.py`, `guardrails.py`, `schemas.py`, `traces.py`), `tests/agents/` |
| `EF-REC-01` recommendation format (Annexe B) | `src/jauge/models/recommendation.py` (`Recommandation`, `MetriquesQualite`, `MetriquesCout`, `MetriquesLatence`) |
| `EF-REC-02` human approval required | `StatutRecommandation`, `approvedBy`, `ApprobationRequiseError` |
| `EF-BAS-01..03` staged rollout and rollback | `BasculeProgressive` (`palier_actuel_pourcentage`, `seuil_alerte`, `annule_automatiquement`) |
| `EF-VER-01` constant-volume verification | `VerificationEconomie`, `engine/stats.economie_mensuelle` |
| `ENF-CONF-01` text stays in the customer account | `PaireRejoueeDataPlane` docstring and type boundary; `tests/evaluation/test_confidentialite.py`; `src/jauge/replay/sampling.py` carries metadata only; `HLD.md` §3 |
| `ENF-IA-03` replayed content is data, not instruction | `src/jauge/agents/guardrails.py`, `tests/agents/test_guardrails.py` |
| `ENF-SEC-02` tenant isolation | `src/jauge/store/protocol.py` (`tenant_id` first), `CloisonnementError`, `tests/store/test_memory_store.py` |
| `ENF-IA-01` no number computed by a model | `HLD.md` §12 (D6), `engine/**` docstrings |
| `ENF-COUT-01` campaign cost capped | `src/jauge/replay/cost.py` (`EstimationCampagne`, `verifier_plafond`), `Tenant.plafond_campagne_usd`, `PlafondDepasseError` |
| `ENF-IA-02` no rollout without approval | `src/jauge/agents/guardrails.py` (`exiger_validation_humaine`, `interdire_action_agent`), `ApprobationRequiseError` |
| `ENF-REP-01` infrastructure as code | `infra/control-plane/` (M10), `infra/client-account/` (M11), README deployment section |
| `ENF-OBS-01` traces and alarms | [`ARCHITECTURE.md`](ARCHITECTURE.md#12-observability) |
| `R10` MSP role denied on the log bucket | M15 acceptance test; IAM screenshot in the video |
| `R11` customer A refused on customer B data | `tests/api/test_isolation.py` (36 cases), `tests/recette/test_r11_cloisonnement.py` (M15) |
| `EF-EVA-03` cost-weighted quality, regression detected per stratum | `src/jauge/evaluation/ponderation.py`, `strates.py`, `tests/evaluation/test_ponderation.py`, `test_strates.py` |
| `EF-VER-01` counterfactual backtest at constant volume, with dispersion | `src/jauge/replay/backtest.py`, `tests/replay/test_backtest.py`, `tests/api/test_route_backtest.py` |
| Evidence verifiable by a third party: signed proof pack, hash chain, replayed verification | `src/jauge/reports/proof_pack*.py`, `scellement.py`, `tests/reports/test_proof_pack*.py` |
| `EF-LEV-06` token diet, compression proven by replay | `src/jauge/levers/token_diet.py`, `src/jauge/replay/variantes.py`, `variantes_preuve.py`, `tests/levers/test_token_diet.py`, `tests/replay/test_variantes.py`, `test_variantes_preuve.py` |
| `EF-ADM-01`, `EF-ADM-02` customer kill-switch, journaled, enforced on the processing routes | `src/jauge/api/killswitch.py`, `src/jauge/api/routes/killswitch.py`, `tests/api/test_killswitch.py`, `tests/api/test_killswitch_routes.py` |
| `EF-RAP-02` cross-customer benchmark under k-anonymity and DP noise | `src/jauge/reports/anonymisation.py`, `src/jauge/api/routes/benchmark.py`, `tests/reports/test_anonymisation.py` |
| `EF-EVA-02` judge bias measured, not assumed | `src/jauge/evaluation/calibration.py`, `aptitude_judge.py`, `jeux_calibration_corpus.py`, `tests/evaluation/test_calibration.py`, `test_biais.py` |
| `EF-EVA-01` pre-registered protocol, immutable and hashed | `src/jauge/evaluation/protocole.py`, `src/jauge/replay/scellement.py`, `tests/evaluation/test_protocole.py`, `tests/replay/test_scellement.py` |
| Ground truth instead of the judge where one exists | `src/jauge/evaluation/verite_terrain.py`, `bac_a_sable.py`, `tests/evaluation/test_verite_terrain.py`, `test_bac_a_sable.py` |
| `EF-BAS-01..03` shadow traffic then canary before the switch | `src/jauge/rollout/shadow.py`, `canary.py`, `devis.py`, `journal.py`, `tests/rollout/test_shadow.py`, `test_canary.py` |
| `EF-ATT-04` historised price catalogue, proofs reopened by a price drop | `src/jauge/pricing_data/catalogue.py`, `surveillance.py`, `impact_prix.py`, `src/jauge/api/routes/pricing.py`, `tests/pricing_data/`, `tests/api/test_route_pricing.py` |

Every module of `docs/MODULES.md` (M1–M18) is merged on `main`; each shipped its
own pull request with its own tests. The ten tracks selected in `docs/backlog/README.md` shipped the same way — PR #45
(kill-switch, k-anonymity and DP noise), #46 (token diet), #47 (counterfactual
backtest), #48 (cost-weighted quality per stratum), #51 (signed proof pack), #54
(judge calibration), #56 (pre-registered protocol), #60 (historised price
catalogue), #64 (shadow then canary) and #67 (ground-truth gates) — plus PR #53,
which wired their public exports, routers and guards together.

---

## 4. What must be confirmed before pressing submit

Ordered by blocking risk. Items 1–4 block the submission itself; items 5–9 block
claims made in the description.

1. **AWS Partner Network enrolment** (§23 Q1) — Track 2 eligibility.
2. **Repository shared** with the two judging addresses, read-only.
3. **Portal public URL** and demo credentials working from outside the MSP
   network, tested in a private browser window.
4. **Video public on YouTube**, English audio, no third-party music or brand,
   under 3:15.
5. **Weighted gateway rule syntax** (§23 Q4–Q5) — if the weighted split does not
   work as expected, the demo falls back to a ticket-based switch (`EF-BAS-04`)
   and the video must say so.
6. **Flex tier and batch availability** in the chosen region for the candidate
   models (§23 Q7) — changes the replay cost figure, not the method.
7. **Invocation-log field names** (§23 Q2) — parser only; the demo uses synthetic
   logs shaped as documented.
8. **Measured figures** from the shoot table — until they exist, the description
   quotes targets, labelled as targets.
9. **Commercial assumptions** (savings share, target reduction, §23 Q10) — after
   the hackathon; do not publish them as results.

---

## 5. Known gaps, stated plainly

| Gap | Consequence for judging | Mitigation |
|---|---|---|
| The public portal is a **static snapshot**, not the live control plane: https://baptistefaisy.github.io/jauge/ serves recorded API answers, so replay, judge and gateway calls are refused with an explicit `501 static_demo` | Judges can click through every screen and every guard rail (403 cross-tenant, 409 without approval, staged rollout) but cannot trigger a real Bedrock replay | A live URL needs the AWS deployment (credits, Bedrock enabled); local live mode is one command: `uv run uvicorn jauge.api.app:app` |
| Multi-turn and tool-using applications are out of v1 | A judge may ask about agentic workloads | Declared as a limit in `HLD.md` §2.2 and §13.1, with the reason (replay fidelity) |
| Judge bias is **measured**, not eliminated | Legitimate question on measurement rigour | The calibration suite now measures position, verbosity and formatting bias, golden trap items, false-accept and false-reject rates (`evaluation/calibration.py`); deterministic checks, 30-pair human review and the self-agreement column remain, declared in `HLD.md` §8.7. Measured is not eliminated — the thresholds stay where the customer puts them |
| The ten backlog tracks are delivered as **modules**, but three of them have no HTTP entry point yet: the shadow-then-canary traffic, the ground-truth gates and the pre-registered protocol are callable from the code, not from the API or the portal | A judge clicking through the portal will not see them | The modules are documented in `HLD.md` §14 and exposed as public exports; the API routes are the next increment, and the demo script shows the module usage |
| The execution sandbox is a **subprocess**, not a security boundary: in `garde-python` mode a determined candidate can bypass the language-level network ban, and nothing prevents writing outside the working directory with the rights of the Jauge account | A sceptical judge may probe the isolation | Stated in the module header, isolation level announced in every result, `exiger_namespace=True` refuses execution rather than degrading it; production should run `nsjail` or a dedicated container when `bwrap` is absent |
| The correspondence between the AWS Price List model identifiers and Jauge's own is not confirmed on a real account | The AWS price source stays a hand-maintained table for now | The adapter exists and is exercised only on its pure conversion functions; the limitation is documented in `SourceTarifsAws`, and the demo table (`data/pricing/`) remains the source of truth (`HLD.md` §13.2, Q3) |
