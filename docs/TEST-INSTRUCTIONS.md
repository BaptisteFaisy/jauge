# Jauge — test instructions for judges

**Everything here runs today, offline.** Section 1 needs no AWS account, no
network access and no credentials. Sections 2 to 4 run the seeded demo (console
summary, API and portal) directly on your machine with a single command.

What is **not** done yet is a real AWS deployment (a public portal URL), the video
and the Devpost submission — see [`SUBMISSION.md`](SUBMISSION.md). Every step below
carries a status line so nothing is presented as working before it is.

| Status | Meaning |
|---|---|
| **Available** | In the repository on `main`, runs as written |
| **Pending gesture** | Needs a human action outside the repository (AWS deployment, video, submission) |

---

## 1. What runs today (available)

### 1.1 Requirements

- Linux or macOS, Python **3.12+** (`python3 --version`)
- [`uv`](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- No AWS credentials, no network once dependencies are fetched

### 1.2 Install and run the test suite

```bash
git clone https://github.com/BaptisteFaisy/jauge.git
cd jauge

uv sync --all-groups      # dependencies: runtime + dev + infra + agents
uv run pytest             # fully offline, no AWS account, no credentials
```

Expected output — the last line is the summary (`-q` is already set in
`pyproject.toml`, so do not add a second `-q` or the summary is suppressed):

```
...                                                                      [100%]
1078 passed in 2:43
```

That is the state of `main` at the time of writing; the summary line pytest prints
is the source of truth (the duration depends on your machine).

The suite covers every delivered module:

| Test file | What it proves |
|---|---|
| `tests/engine/test_calculator.py` | Request cost from the dated price table (Annexe D), tier fallback to Standard, unknown price raises `TarifInconnuError` |
| `tests/engine/test_attribution.py` | Daily aggregation per application/team/model, reconciliation gap and unattributed share |
| `tests/engine/test_stats.py` | Wilson confidence interval, percentiles, minimum sample size, monthly saving, percentage drop |
| `tests/evaluation/test_scoring.py`, `test_decision.py` | Equivalence rate with its Wilson interval; the three conditions of the decision rule tested separately; rejection carries its reasons; the "excellent at 95 %" case when the current model agrees with itself only 96 % |
| `tests/evaluation/test_checks.py` | Deterministic checks: valid JSON, schema respected, identical label, bounded length |
| `tests/evaluation/test_judge.py`, `test_human_review.py` | Judge selection and capability registry; 30-pair human sub-sample and judge/human agreement |
| `tests/evaluation/test_confidentialite.py` | No request or response text ever appears in a decision, a candidate result or a review sub-sample |
| `tests/store/test_memory_store.py` | The store contract and tenant isolation (customer B on customer A's data → refusal) |
| `tests/pricing_data/test_loader.py`, `test_dates.py`, `test_seed.py` | Dated price-table loader and validation: an invalid row names the offending line; a price change takes effect on its date without recomputing the past |
| `tests/replay/test_sampling.py`, `test_executor.py`, `test_cost.py`, `test_client.py`, `test_statemachine.py` | Stratified reproducible sample (same seed → same sample), replay of the current model twice for its own variability, Flex-tier runs, campaign cost estimate and cap, Step Functions definition |
| `tests/agents/test_orchestrator.py`, `test_tools.py`, `test_guardrails.py`, `test_schemas.py`, `test_ports.py`, `test_traces_sessions.py`, `test_strands_integration.py` | Orchestrator plus four specialists used as tools, guardrails (no request text reaches an agent tool), schema-validated outputs, traces and sessions — all offline |
| `tests/attribution/` | Invocation-log parsing, tagging, aggregation, CUR 2.0 reconciliation, unattributed spend and its causes, performance on a large log volume |
| `tests/levers/` | The savings-lever rules (oversized model, missing prompt cache, deferrable processing, repeated requests, retries, over-long outputs), estimated saving and ranking |
| `tests/api/` | Every control-plane endpoint, authentication and roles, JWKS verification, audit, settings, and 36 tenant-isolation cases (customer B on customer A's data → refusal) |
| `tests/reports/` | Monthly report builder, written synthesis through the LLM interface, S3 storage, confidentiality (no request text in a report) |
| `tests/rollout/` | Gateway-rule creation, staged rollout, automatic rollback, ticket for an app off-gateway, constant-volume savings verification, confidentiality |
| `tests/infra/`, `tests/infra-client/` | CDK assertions on the control plane and Lambda packaging; the per-customer CloudFormation template, its least-privilege cross-account role and explicit denials |
| `tests/portal/` | The static portal is served by the API, its structure, and the endpoints it calls |
| `tests/demo/` | The synthetic applications, traffic generator, CUR lines, scenario and seed summary — deterministic, no real data |
| `tests/integration/` | The wiring of the agent and API ports onto the real modules (M17): factories, the offline end-to-end journey, chat, tenant isolation through the integrated API |
| `tests/recette/` | The end-to-end acceptance scenarios R1–R13, including the R10/R11 negative security tests and the R13 documentation-consistency checks |

### 1.3 The same checks the CI runs

```bash
bash scripts/ci-local.sh
```

Four gates, each under 5 minutes in CI: `ruff check` + `ruff format --check`,
file/function size limits (`scripts/check_limits.py`), `pytest`, and
`cfn-lint` + CDK assertions on the infrastructure templates. It must end with
`==> CI locale verte`.

### 1.4 Read the design in five minutes

1. [`HLD.md`](HLD.md) §3 — the two planes and why request text cannot leave the
   customer account.
2. [`HLD.md`](HLD.md) §8 — how quality is measured (sample, judge, deterministic
   checks, decision rule, self-agreement).
3. [`ARCHITECTURE.md`](ARCHITECTURE.md) §4 — the gateway rule that performs the
   switch.
4. `src/jauge/models/campaign.py` — `PaireRejoueeDataPlane`, the only type that
   carries text, and its data-plane-only contract.
5. `src/jauge/store/protocol.py` — every read takes `tenant_id` first.
6. `src/jauge/evaluation/decision.py` — the decision rule, with the relative
   reading of the current model's self-agreement.

---

## 2. Local demo (available)

> **Status: available.** `demo/` (M12), `src/jauge/api/` (M8) and the ASGI
> entry point `jauge.api.app:app` are merged. Both the console demo and the
> single-command portal run as written.

```bash
uv sync --all-groups
uv run python -m demo.seed     # seeds an in-memory store, prints the summary
```

`demo.seed` creates the demonstration environment of specification §18.1 and
prints the figures the code computed. **Demo output, not a customer result** — on
`main` today it reports a mean reconciliation gap of **0.57 %** (14/14 days inside
the 3 % target), an estimated **628.58 $/month** of savings across eight detected
levers, a `ticket-classifier` recommendation `PENDING_APPROVAL`, a
`support-assistant` campaign `REJECTED` with evidence, and a `doc-summarizer`
rollout at stage 50 % with online quality 0.995:

| Tenant | Application | Current model | Scenario (as seeded) |
|---|---|---|---|
| `t-demo-a` | `support-assistant` | large model | Cheaper candidate **rejected** with evidence — "keep your model" |
| `t-demo-a` | `ticket-classifier` | large model (oversized) | Cheaper (medium) candidate accepted → recommendation **pending approval** |
| `t-demo-a` | `doc-summarizer` | large model, Standard tier (nightly) | Deferrable-processing lever → rollout **in progress** at stage 50 % |
| `t-demo-b` | `kb-search` | medium model | Fleet view, second tenant |

All requests are generated. No real customer data is used at any point.

### 2.1 The portal and API (single command)

> **Status: available.** `jauge.api.app:app` seeds the demo scenario on first
> access and wires the API ports (campaigns, reports, chat, staged rollout) onto
> the real modules, then serves both the API and the static portal:

```bash
uv run uvicorn jauge.api.app:app --reload   # then open http://127.0.0.1:8000
```

With `JAUGE_MODE_DEMO=0`, the same entry point starts an **empty**,
Cognito-protected application without demo data.

---

## 3. Demo accounts

> **Status: available.** `src/jauge/api/auth.py` is merged (M8). In demo mode the
> API reads identity from two headers instead of a Cognito token. Demo mode is on
> by default (`JAUGE_MODE_DEMO=1`) and is **refused when `JAUGE_ENV=production`**.
> Start the server with the launcher of section 2.1 first.

The two demo tenants are `t-demo-a` (customer A, three applications) and
`t-demo-b` (customer B, one application). The six roles are the values of
`RoleUtilisateur`:

| Header | Value | Effect |
|---|---|---|
| `X-Jauge-Tenant` | `t-demo-a` or `t-demo-b` | Selects the tenant the request is scoped to |
| `X-Jauge-Role` | `administrateur_plateforme`, `analyste_finops`, `responsable_compte`, `responsable_application`, `relecteur`, `directeur` | Selects the role and therefore the permitted actions |

| Demo identity | Headers | Sees | Can |
|---|---|---|---|
| MSP platform admin | `X-Jauge-Tenant: t-demo-a`, `X-Jauge-Role: administrateur_plateforme` | Everything, both tenants | Everything |
| MSP FinOps analyst | `t-demo-a` / `analyste_finops` | Aggregates of both tenants | Launch campaigns, prepare recommendations |
| Customer A application owner | `t-demo-a` / `responsable_application` | Customer A only | Approve or reject a recommendation on their applications |
| Customer A reviewer | `t-demo-a` / `relecteur` | Customer A, request text (data-plane UI only) | Review a 30-pair sub-sample |
| Customer B executive | `t-demo-b` / `directeur` | Customer B only | Read dashboards and reports |

The account pre-filled for judges is `juge-hackathon` (role
`administrateur_plateforme`, tenant `t-demo-a`); `GET /v1/demo/identity` returns it
together with the header values to use.

**Negative test to try yourself** (this is the security claim, so verify it):
request a customer A resource while scoped to customer B. The answer is a refusal
(`403`, `CloisonnementError`) — never an empty list, never another tenant's data:

```bash
# expected: 403, not 200 with someone else's data
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Jauge-Tenant: t-demo-b' -H 'X-Jauge-Role: directeur' \
  http://127.0.0.1:8000/v1/applications/support-assistant
```

In the hosted demo the same identities exist as Cognito users, with the
credentials published in the submission form and nowhere else.

---

## 4. Guided test path (minute by minute)

> **Status: available.** Each step runs against the seeded demo store through the
> portal served by the launcher of section 2.1 (portal M13 + API M8 are merged).
> Timing assumes a warm environment. Read screens (overview, levers, campaign,
> recommendation, costs, reconciliation) and the approval mutation are verified;
> some later states (a rollout already at stage 50 %, the rejected candidate) are
> pre-set by the seed so you can observe them directly.

| Time | Step | What you should see | Requirement |
|---|---|---|---|
| 0:00 | Sign in as **customer A application owner** | Customer A overview only — no customer B data anywhere in the navigation | `EF-POR-01`, `ENF-SEC-02` |
| 0:30 | Open **Overview** | Spend per application and per team for the last 14 days, plus a reconciliation line with its gap percentage | `EF-RAP-01`, `EF-ATT-06` |
| 1:30 | Open **Levers** | At least three levers ranked by estimated monthly saving (oversized model, missing prompt cache, deferrable processing) | `EF-LEV-01..06` |
| 2:30 | Open **`ticket-classifier` → campaign** | Sample size 200, seed 42, two candidates plus the current model as reference, estimated campaign cost shown **before** launch | `EF-REJ-01`, `ENF-COUT-01` |
| 3:30 | Inspect the results table | Per candidate: equivalence rate, confidence interval, self-agreement of the current model, deterministic failures, p50/p95 latency, cost per request | `EF-EVA-03`, `EF-EVA-04` |
| 5:00 | Compare candidate vs threshold | The accepted candidate clears 95 % with a lower bound above 90 %; the rejected one is below, with its deterministic failures listed | §13.4, `R5` |
| 6:00 | Open the **recommendation** | Target model, quality block, cost block (before/after, monthly volume, expected saving), latency block, risks, rollout plan `[10, 50, 100]`, status `PENDING_APPROVAL` | `EF-REC-01` |
| 7:00 | Try **rollout** without approving | Refused (`ApprobationRequiseError`) — a rollout is impossible without approval | `EF-REC-02`, `ENF-IA-02`, `R6` |
| 7:30 | **Approve**, then start the rollout | Gateway rule created, stage 10 %, online quality re-judged and displayed at the stage | `EF-BAS-01..02`, `R7` |
| 9:00 | Advance to 50 %, then 100 % | Stage indicator moves, each stage shows its own online quality | `EF-BAS-02` |
| 10:00 | Open **savings verification** | Cost per request before vs after at constant volume, observed vs expected saving | `EF-VER-01`, `R12` |
| 11:00 | Open the **rejected** candidate (support assistant) | Rejected with its evidence — equivalence below the application's 97 % threshold and deterministic failures; "keep your model" is a valid, proven outcome | `R5` |
| 12:00 | Trigger the **degraded quality** scenario | Rule removed automatically, alert visible, rollout marked `annule_automatiquement` | `EF-BAS-03`, `R8` |
| 13:00 | Switch to **customer B** | Customer B sees only its own application; the fleet view is visible to MSP roles only | `EF-RAP-02`, `R11` |
| 14:00 | Launch a campaign above the tenant cap | Refused with `PlafondDepasseError` | `ENF-COUT-01`, `R9` |
| 15:00 | Ask a cost question in **chat** | The agent answers from computed aggregates; it never returns request text | `EF-RAP-04`, `ENF-IA-01`, `ENF-CONF-01` |

### 4.1 Acceptance scenarios (specification §22)

`R1`–`R13` are the normative acceptance scenarios; they are exercised as
automated end-to-end tests in `tests/recette/` (M15). The guided path above walks
through `R2`, `R3`, `R4`, `R5`, `R6`, `R7`, `R8`, `R9`, `R11` and `R12` from the
UI. `R10` (MSP role denied on the log bucket) and `R13` (install into a blank
account by following the README) are console/deployment checks, not UI checks.

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: demo` when starting `uvicorn` | Not run from the repository root, or demo package absent | `cd` to the repository root and run `uv run uvicorn jauge.api.app:app` |
| `uv run pytest -q` prints dots but no summary | `-q` is already set in `pyproject.toml`; a second `-q` silences the summary | Run `uv run pytest` |
| `uv: command not found` | `uv` not installed or not on `PATH` | `curl -LsSf https://astral.sh/uv/install.sh \| sh`, then `export PATH="$HOME/.local/bin:$PATH"` |
| `ModuleNotFoundError: jauge` | Running `pytest` from outside the project or without `uv run` | Use `uv run pytest` from the repository root |
| `ruff format --check` fails after an edit | Formatting drift | `uv run ruff format .` |
| `check_limits.py` fails | A file exceeded 500 lines or a function 50 lines | Split the file; the rule is non-negotiable (`docs/MODULES.md`) |
| Tests try to reach the network | A fake adapter was replaced by a real client | Tests must stay offline; report it |

---

## 6. Where the numbers come from

Nothing in Jauge is estimated by a language model. If a judge wants to verify a
figure:

| Figure | Computed by |
|---|---|
| Cost of a request | `src/jauge/engine/calculator.py` (`calculer_cout_requete`, Annexe D) |
| Daily aggregate, reconciliation gap | `src/jauge/engine/attribution.py` |
| Equivalence rate and confidence interval | `src/jauge/engine/stats.py` (`intervalle_wilson`) |
| Minimum sample size | `src/jauge/engine/stats.py` (`taille_echantillon_minimale`) |
| Expected monthly saving | `src/jauge/engine/stats.py` (`economie_mensuelle`) |
| Reduction percentage | `src/jauge/engine/stats.py` (`baisse_pourcentage`) |

The agents receive these values already computed and only write prose around
them (`ENF-IA-01`).
