# Jauge — FinOps for generative AI on Amazon Bedrock

**Attribute** every inference dollar to an application, a team and a request
type. **Prove** that a cheaper model does the job, by replaying real requests
against candidate models and measuring quality. **Apply** the change through a
gateway rule in stages, then verify the saving on the bill.

Jauge is sold by an MSP as a subscription with a share of verified savings. It
is built for the *AWS MSP AI Hackathon* (Track 2 — Deliver AI-Driven Outcomes
for Customers).

> Internal working documents are in French under `docs/fr/`. All submission
> deliverables (this README, `docs/HLD.md`, `docs/ARCHITECTURE.md`, the portal)
> are in English.

## Why

A company running generative AI in production gets a growing inference bill and
cannot answer three questions:

1. Which application and which team produce the spend?
2. Would a cheaper model do the same job — **with evidence**, not an opinion?
3. Did the change actually reduce the bill, at constant volume?

Saying “this model is cheaper” convinces nobody. Showing “on 200 of your real
requests, 97 % of the answers are judged equivalent, and the bill drops by
$992 per month” closes the decision.

## Two planes, privacy by construction

| Plane | Where | Holds |
|---|---|---|
| **Data plane** | Customer AWS account | Invocation logs, request/response text, replay execution, judge evaluation, results |
| **Control plane** | MSP AWS account | Strands agents, portal, aggregates, scores, recommendations, rollout control |

Request and response text never leaves the customer account. The MSP
cross-account role has **no read access** to the log or result buckets: it can
only start the replay state machine and read aggregate tables. Every number
shown to a human is computed by code — never by a language model.

## Architecture

```
Customer account (data plane)                MSP account (control plane)
─────────────────────────────                ────────────────────────────
Applications ──▶ AgentCore Gateway           Portal (CloudFront + S3)
                     │  routing rules         │
                     ▼                        ▼
              Amazon Bedrock            API Gateway + Lambda ── Cognito
                     │                        │
                     ▼                        ▼
         S3 invocation logs             Strands agents on AgentCore Runtime
                     │                  (orchestrator + 4 specialists)
                     ▼                        │
         Glue / Athena tables                 ▼
                     │                  DynamoDB (aggregates, campaigns,
                     ▼                   recommendations, rollouts)
         Step Functions replay machine
                     │
                     ▼
         Bedrock judge evaluation jobs
```

Full diagram and design rationale: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
high-level design: [`docs/HLD.md`](docs/HLD.md).

## Repository layout

| Path | Content |
|---|---|
| `src/jauge/models/` | Pydantic entities — the shared vocabulary of every module |
| `src/jauge/engine/` | Deterministic computation: request cost, aggregation, bill reconciliation, statistics |
| `src/jauge/store/` | Persistence contract + in-memory implementation (DynamoDB adapter alongside) |
| `src/jauge/attribution/` | Invocation-log ingestion, tagging, unattributed spend |
| `src/jauge/levers/` | Savings-lever detection rules |
| `src/jauge/replay/` | Stratified reproducible sampling and replay execution |
| `src/jauge/evaluation/` | LLM-as-a-judge scoring, deterministic checks, decision rule |
| `src/jauge/rollout/` | Gateway rules, staged rollout, automatic rollback, savings verification |
| `src/jauge/agents/` | Strands agents (orchestrator + cost analyst + replay planner + results reader + writer) |
| `src/jauge/api/` | Control-plane REST API (FastAPI) |
| `src/jauge/reports/` | Monthly report generation |
| `infra/control-plane/` | AWS CDK app for the MSP account |
| `infra/client-account/` | Single CloudFormation template onboarded per customer |
| `demo/` | Synthetic traffic generator and demo applications |
| `portal/` | Static web portal (no build step) |
| `docs/` | HLD, architecture, demo script, submission checklist |
| `tests/` | Unit, isolation (negative security tests) and end-to-end acceptance tests |

Work breakdown and module contracts: [`docs/MODULES.md`](docs/MODULES.md).

## Quickstart (local, no AWS account needed)

```bash
uv sync --all-groups          # install dependencies
uv run pytest -q              # run the test suite
uv run python -m demo.seed    # populate the in-memory store with demo data
uv run uvicorn jauge.api.app:app --reload   # serve API + portal on :8000
```

The demo runs entirely on the in-memory store with synthetic traffic: no
customer data, no AWS calls, no cost.

## Deploying

1. **Customer account** — deploy `infra/client-account/template.yaml`
   (CloudFormation). It creates the cross-account role (with an external ID and
   no read access to logs), the log bucket with KMS encryption, Glue/Athena
   tables and the replay state machine. See `docs/deploy-client.md`.
2. **MSP account** — `cdk deploy` from `infra/control-plane/`: API, Cognito,
   DynamoDB, portal on CloudFront, agents on AgentCore Runtime.
3. Enable Bedrock model invocation logging to S3 in the customer account,
   activate cost-allocation tags, and connect the CUR 2.0 export.

One customer = one template. Another MSP can install the whole solution by
following this README.

## Development and CI

Every pull request runs four jobs, each capped at **5 minutes**:

| Job | What it checks |
|---|---|
| `lint` | `ruff check` + `ruff format --check` |
| `limits` | Files < 500 lines, functions < 50 lines |
| `test` | pytest (unit, isolation, acceptance) |
| `iac` | `cfn-lint` on CloudFormation templates + CDK assertion tests |

Plus the repository baseline: `ci-budget` (no job may declare a timeout above
5 minutes), `stale-base-guard` and the `hermes-lite` freshness gate. Merges go
through the Mergify queue onto `main` — direct pushes are blocked by branch
protection.

```bash
bash scripts/ci-local.sh      # run the whole CI locally
```

## Security and privacy

- Cross-account access through a least-privilege role with an external ID.
- Strict tenant isolation, enforced by negative tests (`tests/isolation/`).
- Encryption at rest (KMS) and in transit everywhere.
- No rollout without explicit human approval; automatic rollback if online
  quality drops below the threshold.
- Replay request content is **data**, never an instruction for Jauge's agents.

## License

Proprietary — all rights reserved.
