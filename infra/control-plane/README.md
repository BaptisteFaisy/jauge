# Jauge — control plane infrastructure (AWS CDK, Python)

Infrastructure as code for the **MSP control plane** of Jauge, the FinOps service
for generative AI on Amazon Bedrock (spec §11.2 / §11.3, ENF-REP-01, ENF-SEC-03,
§17.3). The customer data plane lives in the customer's own account and is
deployed by the client-account template (`infra/client-account`, module M11):
nothing sensitive crosses this stack.

```
Portal (CloudFront + OAC → S3) ──► API Gateway ──► Lambda (ASGI adapter) ──► DynamoDB
        │                              │                    │                  ▲
        └── Cognito user pool ─────────┘                    └──► S3 reports ───┘
                                       │
                        AgentCore Runtime (Strands) ──► AgentCore Gateway (MCP tools) ──► Lambda
                                       └── CloudWatch logs, X-Ray, Budgets, alarms
```

## What gets created

| Component | Resources |
|---|---|
| Identity (EF-POR-01) | Cognito user pool (no self sign-up, optional MFA), public SPA client (authorization code + PKCE), hosted domain, groups `msp-admin` / `client-admin` / `client-viewer` |
| Portal | Private S3 bucket (KMS, versioned, TLS ≥ 1.2) served by CloudFront through an **Origin Access Control**; security headers, HTTP/2+3, TLS 1.2 minimum, SPA error routing |
| API (§15) | REST API Gateway, Cognito authorizer on `/{proxy+}`, open `/health` probe, Lambda Python 3.12 (arm64) running an ASGI→Lambda adapter, `jauge` Lambda layer, access logs, throttling |
| State | One DynamoDB table (on-demand, KMS, PITR, TTL) plus a `par-type` GSI — see “Data model” below |
| Agents (§12) | AgentCore **Runtime** hosting the Strands agents (container image, Cognito JWT authorizer) and AgentCore **Gateway** exposing the agent tools over MCP (IAM authorizer, KMS) |
| Reports | Private S3 bucket (KMS, versioned) for monthly reports |
| Security (ENF-SEC-03) | One customer-managed KMS key with rotation; used by DynamoDB, both buckets and the gateway; TLS-only bucket policies; no public access anywhere |
| Observability | CloudWatch log groups with retention, API access logs, X-Ray tracing, alarms on Lambda errors and API 5XX |
| Cost (§17.3) | AWS Budgets monthly budget with e-mail notifications at 80 % (forecast) and 100 % (actual) |

## Data model — one table, generic keys

All control-plane entities share a single table `<prefix>-state`:

| Attribute | Meaning |
|---|---|
| `PK` | `TENANT#<tenant_id>` — the partition **is** the customer |
| `SK` | `<TYPE>#<id or date>` — `AGG#2026-11-11`, `CAMP#…`, `REC#…`, `SWITCH#…`, `PARAM#…`, `AUDIT#…` |
| `GSI1PK` / `GSI1SK` | `TYPE#<type>` / `<timestamp>#<id>`, index `par-type` (keys only) for the MSP fleet view |

Why one table instead of one table per entity:

1. every access is “by tenant, then by entity”, which a single PK/SK pair serves
   with no cross-table joins or `BatchGetItem` fan-out;
2. one KMS key, one IAM policy, one backup point (PITR) — less to get wrong when
   the solution is duplicated for each managed customer;
3. a rollout and its recommendation must be written coherently
   (`TransactWriteItems` works on a single table, not across two);
4. tenant isolation (ENF-SEC-02) becomes structural: there is no query shape
   that returns another customer's items without crossing the `TENANT#` prefix.

## Prerequisites

- AWS CLI credentials for the MSP account (never commit them).
- Node.js ≥ 18 (the CDK CLI is a Node application; `aws-cdk-lib` itself is
  bundled with the Python package).
- `uv` (the repository already declares the `infra` dependency group:
  `aws-cdk-lib`, `constructs`, `cfn-lint`).

```bash
cd <repo-root>
uv sync --all-groups
npm install -g aws-cdk        # or: npx aws-cdk@latest …
export CDK_DEFAULT_ACCOUNT=<12-digit MSP account id>
export CDK_DEFAULT_REGION=eu-west-1
```

## Synth, diff, deploy

There is **no `cdk.json` in this directory on purpose**: the CI job `iac` runs
`cfn-lint` over every `*.json` under `infra/`, and a `cdk.json` (or any
synthesised template) is not a CloudFormation template. Always pass `--app`:

```bash
cd infra/control-plane

# synthesise (writes cdk.out/, which is git-ignored)
cdk synth --app "uv run python app.py"

# review the change set
cdk diff --app "uv run python app.py"

# deploy
cdk deploy --app "uv run python app.py" --require-approval broadening

# tear down (buckets, the table and the key are RETAINed by design)
cdk destroy --app "uv run python app.py"
```

If you prefer a local `cdk.json`, create it outside version control (it will
fail the `iac` job if committed):

```bash
printf '{"app": "uv run python app.py"}' > cdk.json && echo 'cdk.json' >> .git/info/exclude
```

## Parameters

All parameters are CDK context values under the `jauge.` key
(`-c jauge.<name>=<value>`), so a second MSP installs the same code without
editing it.

| Context key | Default | Purpose |
|---|---|---|
| `jauge.msp_account_id` | `CDK_DEFAULT_ACCOUNT` | MSP account that owns the stack (also used to scope the KMS policy) |
| `jauge.region` | `CDK_DEFAULT_REGION` | Deployment region |
| `jauge.environment` | `dev` | Resource name prefix (`jauge-<env>-…`), deletion protection in `prod` |
| `jauge.portal_domain_name` | — | Custom portal domain (CloudFront alias) |
| `jauge.portal_certificate_arn` | — | ACM certificate for that domain, **must live in `us-east-1`** |
| `jauge.monthly_budget_usd` | `200` | AWS Budgets monthly limit |
| `jauge.budget_alert_emails` | — | Comma-separated list of budget notification recipients |
| `jauge.log_retention_days` | `30` | CloudWatch retention (7, 14, 30, 90 or 365) |
| `jauge.api_stage_name` | `prod` | API Gateway stage name |
| `jauge.agent_image_uri` | `<account>.dkr.ecr.<region>.amazonaws.com/jauge-<env>-agents:latest` | Container image of the Strands agents |
| `jauge.enable_api_access_logs` | `true` | API Gateway access logs (needs the account-level CloudWatch role) |

Example:

```bash
cdk deploy --app "uv run python app.py" \
  -c jauge.environment=demo \
  -c jauge.monthly_budget_usd=100 \
  -c jauge.budget_alert_emails=finops@example.com \
  -c jauge.portal_domain_name=jauge.example.com \
  -c jauge.portal_certificate_arn=arn:aws:acm:us-east-1:123456789012:certificate/…
```

## Tests

```bash
uv run pytest -q -m infra tests/infra   # CDK assertions, no AWS call
bash scripts/ci-local.sh                # full local CI (lint, limits, test, iac)
```

The assertions use `aws_cdk.assertions.Template.from_stack` on an in-memory
`App`: nothing is deployed, nothing is downloaded, no credentials are needed.

## What is left to do on the AWS side

The stack is deliberately conservative: it does not create accounts, images or
certificates it cannot validate. Before a real deployment:

1. **Agent container image.** Build and push the Strands agents image (module
   M7) to ECR in the MSP account, then either use the default name
   (`jauge-<env>-agents:latest`) or pass `jauge.agent_image_uri`. The runtime
   role already has `bedrock:InvokeModel*` on foundation models of the region —
   narrow it to the models the MSP actually allows.
2. **ACM certificate** (only with a custom portal domain): request it in
   **us-east-1**, validate it by DNS (Route 53 or your registrar) and pass its
   ARN. CloudFront cannot use a certificate from another region.
3. **DNS.** The stack outputs `PortalUrl` (the CloudFront domain). Point the
   custom domain at it with a Route 53 alias or a CNAME — no hosted zone is
   created here.
4. **API Gateway CloudWatch role.** Account-level and a singleton: if another
   stack already owns `AmazonAPIGatewayPushToCloudWatchLogs`, the deployment of
   `AWS::ApiGateway::Account` may conflict. Set
   `-c jauge.enable_api_access_logs=false` if that happens (throttling metrics
   and stage logs stay enabled).
5. **First portal user.** Self sign-up is disabled: create the first
   administrator with
   `aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username <email>`
   (see the stack outputs) and add it to `msp-admin`.
6. **Portal content.** Upload the static portal (module M13) to the portal
   bucket with `aws s3 sync portal/ s3://<portal-bucket>/` and invalidate
   `/*` in CloudFront.
7. **Quotas and service availability.** AgentCore Runtime and Gateway must be
   available in the chosen region; ECR image pull, Bedrock model access and the
   Lambda concurrency quota should be checked in the target account. The
   gateway is a per-account resource: one per environment, not per customer.
8. **Deletion.** The state table, both buckets and the KMS key are `RETAIN`ed
   on stack deletion (audit trail and reports survive). Delete them explicitly
   once the data is exported; the KMS key has a 14-day pending window.
9. **Customer accounts.** This stack covers the MSP only. Each managed customer
   needs the M11 template (cross-account role with external ID, log bucket, Glue
   / Athena, replay state machine, its own budget).

## Layout

```
infra/control-plane/
├── app.py                       # CDK entry point (App + ControlPlaneStack)
├── README.md                    # this file
├── jauge_control_plane/
│   ├── config.py                # typed parameters, naming, validation
│   ├── paths.py                 # asset paths anchored on the repo root
│   ├── stack.py                 # ControlPlaneStack: wiring and ordering
│   └── constructs/
│       ├── storage.py           # KMS key, DynamoDB table, reports bucket
│       ├── identity.py          # Cognito user pool, SPA client, groups
│       ├── portal.py            # portal bucket, CloudFront distribution, OAC
│       ├── api.py               # Lambda, layer, API Gateway, authorizer
│       ├── packaging.py         # Lambda layer bundling (no Docker)
│       ├── agents.py            # AgentCore Runtime + Gateway + tools Lambda
│       └── operations.py        # AWS Budgets and CloudWatch alarms
└── lambda/
    ├── handler.py               # ASGI → Lambda adapter (delegates to jauge.api)
    └── tools.py                 # gateway tools (delegates to jauge.api.tools)
```

The `lambda/` modules are **packaging only**: no business rule, no computation.
They import `jauge.api` from the layer, so the API code stays in `src/jauge/api`
(module M8) and is not duplicated here.

## AgentCore L2 assumptions

`aws_cdk.aws_bedrockagentcore` provides L2 constructs (`Runtime`, `Gateway`,
`GatewayTarget`, `LambdaTargetConfiguration`) in `aws-cdk-lib` ≥ 2.276 — the
version resolved by the `infra` dependency group. If an older SDK is used, those
L2 classes do not exist and the same resources must be written as `CfnRuntime` /
`CfnGateway` / `CfnGatewayTarget` (identical CloudFormation properties, 1:1
mapping). **Re-check the L2 mapping on every SDK upgrade**; the CloudFormation
property names used here are those of `AWS::BedrockAgentCore::Runtime` and
`AWS::BedrockAgentCore::Gateway`.
