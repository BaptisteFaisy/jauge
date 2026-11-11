# Jauge — test instructions for judges

**Everything in section 1 runs today, offline, in under a minute.** It needs no
AWS account, no network access and no credentials.

Sections 2 to 4 describe the demo environment (portal, seeded data, demo
accounts). Those parts are **planned**: they belong to modules that are still in
flight (`docs/MODULES.md`, M8 API, M12 demo, M13 portal). Each step below carries
a status line so nothing is presented as working before it is.

| Status | Meaning |
|---|---|
| **Available** | In the repository on `main`, runs as written |
| **Planned** | Not in the repository yet; the interface is specified here, not implemented |

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
uv run pytest -q          # 22 tests, ~0.1 s, fully offline
```

Expected output:

```
......................                                                   [100%]
22 passed in 0.04s
```

The suite covers what is delivered today:

| Test file | What it proves |
|---|---|
| `tests/engine/test_calculator.py` | Request cost from the dated price table (Annexe D), tier fallback to Standard, unknown price raises `TarifInconnuError` |
| `tests/engine/test_attribution.py` | Daily aggregation per application/team/model, reconciliation gap and unattributed share |
| `tests/engine/test_stats.py` | Wilson confidence interval, percentiles, minimum sample size, monthly saving, percentage drop |
| `tests/store/test_memory_store.py` | The store contract and tenant isolation (customer B on customer A's data → refusal) |

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

---

## 2. Local demo (planned)

> **Status: planned.** `demo/` (M12) and `src/jauge/api/` (M8) are not in the
> repository yet. The commands below are the intended interface and are listed so
> that the target is explicit. Running them today fails with
> `No module named 'demo'` / `No module named 'jauge.api'`.

```bash
uv sync --all-groups
uv run python -m demo.seed                    # populate the in-memory store
uv run uvicorn jauge.api.app:app --reload     # API + portal on http://127.0.0.1:8000
```

`demo.seed` will create the demonstration environment of specification §18.1:

| Tenant | Application | Current model | Scenario |
|---|---|---|---|
| `client-a` | `support-assistant` | large model | Candidate too weak → rejected with evidence |
| `client-a` | `ticket-classifier` | large model | Candidate accepted → staged rollout to a small model |
| `client-a` | `nightly-summarizer` | large model, Standard tier | Deferrable processing lever |
| `client-b` | `doc-qa` | medium model | Fleet view, second tenant |

All requests are generated. No real customer data is used at any point.

---

## 3. Demo accounts (planned)

> **Status: planned.** `src/jauge/api/auth.py` does not exist yet (M8). The
> interface below is the intended demo mode, described so that judges and the
> team work from the same target.

Demo mode is enabled by a single environment variable and is **refused when
`JAUGE_ENV=production`**. It lets a judge switch identity with two HTTP headers
instead of going through Cognito:

| Header | Value | Effect |
|---|---|---|
| `X-Jauge-Tenant` | `client-a` or `client-b` | Selects the tenant the request is scoped to |
| `X-Jauge-Role` | `platform-admin`, `finops-analyst`, `account-manager`, `app-owner`, `reviewer`, `executive` | Selects the role and therefore the permitted actions |

| Demo identity | Headers | Sees | Can |
|---|---|---|---|
| MSP platform admin | `X-Jauge-Tenant: client-a`, `X-Jauge-Role: platform-admin` | Everything, both tenants | Everything |
| MSP FinOps analyst | `client-a` / `finops-analyst` | Aggregates of both tenants | Launch campaigns, prepare recommendations |
| Customer A application owner | `client-a` / `app-owner` | Customer A only | Approve or reject a recommendation on their applications |
| Customer A reviewer | `client-a` / `reviewer` | Customer A, request text (data-plane UI only) | Review a 30-pair sub-sample |
| Customer B executive | `client-b` / `executive` | Customer B only | Read dashboards and reports |

**Negative test to try yourself** (this is the security claim, so verify it):
call any endpoint with `X-Jauge-Tenant: client-b` while requesting a customer A
resource. The answer must be a refusal (`403`, `CloisonnementError`), never an
empty list and never another tenant's data.

```bash
# expected: 403, not 200 with someone else's data
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'X-Jauge-Tenant: client-b' -H 'X-Jauge-Role: executive' \
  http://127.0.0.1:8000/v1/applications/support-assistant
```

In the hosted demo the same identities exist as Cognito users, with the
credentials published in the submission form and nowhere else.

---

## 4. Guided test path (planned — minute by minute)

> **Status: planned.** Each step depends on the portal (M13) and the API (M8).
> Timing assumes a warm environment.

| Time | Step | What you should see | Requirement |
|---|---|---|---|
| 0:00 | Sign in as **customer A application owner** | Customer A overview only — no customer B data anywhere in the navigation | `EF-POR-01`, `ENF-SEC-02` |
| 0:30 | Open **Overview** | Spend per application and per team for the last 14 days, plus a reconciliation line with its gap percentage | `EF-RAP-01`, `EF-ATT-06` |
| 1:30 | Open **Levers** | At least three levers ranked by estimated monthly saving (oversized model, missing prompt cache, deferrable processing) | `EF-LEV-01..06` |
| 2:30 | Open **`ticket-classifier` → campaign** | Sample size 200, seed 42, three candidates, estimated campaign cost shown **before** launch | `EF-REJ-01`, `ENF-COUT-01` |
| 3:30 | Inspect the results table | Per candidate: equivalence rate, confidence interval, self-agreement of the current model, deterministic failures, p50/p95 latency, cost per request | `EF-EVA-03`, `EF-EVA-04` |
| 5:00 | Compare candidate vs threshold | The accepted candidate clears 95 % with a lower bound above 90 %; the rejected one is below, with its failing request families listed | §13.4, `R5` |
| 6:00 | Open the **recommendation** | Target model, quality block, cost block (before/after, monthly volume, expected saving), latency block, risks, rollout plan `[10, 50, 100]`, status `PENDING_APPROVAL` | `EF-REC-01` |
| 7:00 | Try **rollout** without approving | Refused (`ApprobationRequiseError`) — a rollout is impossible without approval | `EF-REC-02`, `ENF-IA-02`, `R6` |
| 7:30 | **Approve**, then start the rollout | Gateway rule created, stage 10 %, online quality re-judged and displayed at the stage | `EF-BAS-01..02`, `R7` |
| 9:00 | Advance to 50 %, then 100 % | Stage indicator moves, each stage shows its own online quality | `EF-BAS-02` |
| 10:00 | Open **savings verification** | Cost per request before vs after at constant volume, observed vs expected saving | `EF-VER-01`, `R12` |
| 11:00 | Open the **rejected** candidate (support assistant) | Rejected with its failing families — "keep your model" is a valid, proven outcome | `R5` |
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
| `No module named 'demo'` / `'jauge.api'` | Those modules are not delivered yet (M12 / M8) | Use section 1: the test suite is the runnable part today |
| `uv: command not found` | `uv` not installed or not on `PATH` | `curl -LsSf https://astral.sh/uv/install.sh \| sh`, then `export PATH="$HOME/.local/bin:$PATH"` |
| `ModuleNotFoundError: jauge` | Running `pytest` from outside the project or without `uv run` | Use `uv run pytest -q` from the repository root |
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
