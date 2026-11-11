# Deploying Jauge in your AWS account

This document is for the client (or the judge running the demo): it installs the
**Jauge data plane** in your own AWS account, in one command, from one
CloudFormation template.

Everything that carries request or response text — invocation logs, replay
results, the queries themselves — stays in your account. The Jauge control
plane, which runs in the MSP account, only receives **aggregates, scores and
identifiers**.

- Template: [`infra/client-account/template.yaml`](../infra/client-account/template.yaml)
- Script: [`infra/client-account/deploy.sh`](../infra/client-account/deploy.sh)
- Requirements covered: EF-ATT-01, ENF-SEC-01, ENF-SEC-03, ENF-REP-01,
  ENF-OBS-01, spec sections 8.1, 11.2, 16 and 17.3.

## 1. What gets created

| Resource | Logical name | Purpose |
|---|---|---|
| IAM role (cross-account) | `MspCrossAccountRole` | The only way in for the MSP: external ID required, least privilege, **no read on log or replay-result buckets** |
| KMS key + alias | `DataKey`, `DataKeyAlias` | Encryption at rest for every bucket and for query output (ENF-SEC-03) |
| S3 bucket (invocation logs) | `LogsBucket` | Bedrock model invocation logs; KMS-encrypted, versioned, public access blocked, expires after `RetentionDays` |
| S3 bucket (replay results) | `ReplayResultsBucket` | Output of replay campaigns, `<logs-bucket>-replay-results`, same protection |
| S3 bucket (query output) | `AthenaResultsBucket` | `<logs-bucket>-athena-results`, **aggregates only** — the single bucket the MSP role may read |
| Glue database + tables | `CatalogDatabase`, `InvocationLogsTable`, `CurTable`, `AggregatesTable` | `jauge_<env>` with `invocation_logs` (JSON), `cur2` (CUR 2.0 Parquet) and `aggregates` (daily cost and quality per application — the only table the MSP queries) |
| Athena workgroup | `AthenaWorkGroup` | Enforced settings, per-query data-scanned ceiling, KMS-encrypted output |
| Lambda + IAM role | `ReplayWorkerFunction`, `ReplayWorkerRole` | Replay worker (the packaged `src/jauge/replay` module replaces the placeholder code at build time) |
| Step Functions state machine + role | `ReplayStateMachine`, `ReplayExecutionRole` | Replay orchestration: sample → replay each candidate → publish results; X-Ray tracing on |
| CloudWatch log group | `ReplayLogGroup` | State machine and worker logs, retention `RetentionDays` |
| SNS topic | `AlertTopic` | Budget and alarm notifications (optional e-mail subscription) |
| AWS Budgets | `AccountBudget` | Monthly cost budget, alerts at 80 % actual and 100 % forecasted |
| CloudWatch alarms | `ReplayFailuresAlarm`, `ReplayWorkerErrorsAlarm` | Failing executions and worker errors |

## 2. Prerequisites

- AWS CLI v2, credentials allowed to create IAM roles, S3 buckets, KMS keys,
  Glue tables, an Athena workgroup, a Step Functions machine and a budget.
- The region of your data plane (`eu-west-1` in the demo). Deploy the stack in
  that region: every ARN in the template uses the stack region.
- A globally unique bucket name, e.g. `acme-jauge-logs`.
- The MSP account ID (12 digits) and an external ID of at least 16 characters
  (the script generates one if you do not supply it).

## 3. Deploy in one command

```bash
./infra/client-account/deploy.sh \
  --msp-account 111122223333 \
  --logs-bucket acme-jauge-logs \
  --region eu-west-1 \
  --profile my-client-profile \
  --alert-email finops@acme.example
```

The script prints the external ID **once**, at the end. Send it to the MSP over
a secure channel; store it in your secret manager. Everything else is in the
stack outputs.

Dry run (nothing is created, the resolved command is printed):

```bash
./infra/client-account/deploy.sh --msp-account 111122223333 \
  --logs-bucket acme-jauge-logs --dry-run
```

Equivalent raw command, if you prefer not to use the script:

```bash
aws cloudformation deploy \
  --template-file infra/client-account/template.yaml \
  --stack-name jauge-client \
  --region eu-west-1 \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
      MspAccountId=111122223333 ExternalId=<32-char-secret> \
      EnvironmentName=demo DataRegion=eu-west-1 LogsBucketName=acme-jauge-logs \
      RetentionDays=90 MonthlyBudgetUsd=200 AlertEmail=finops@acme.example
```

## 4. Parameters

| Parameter | Default | Description |
|---|---|---|
| `MspAccountId` | — (required) | AWS account ID of the MSP control plane. Only this account can assume the role. |
| `ExternalId` | — (required, ≥ 16 chars) | Secret external ID enforced by `sts:AssumeRole`. Rotate by updating this parameter. |
| `EnvironmentName` | `demo` | Suffix for every resource name (`^[a-z0-9-]{2,20}$`). |
| `DataRegion` | `eu-west-1` | Region of the data plane. Must equal the deployment region; `deploy.sh` passes `--region` to both so they cannot drift. |
| `LogsBucketName` | — (required) | Invocation-log bucket. Two derived buckets are created: `<name>-replay-results`, `<name>-athena-results`. |
| `RetentionDays` | `90` | Retention for logs, replay results and CloudWatch logs (ENF-CONF-03). One of 7, 14, 30, 60, 90, 180, 365, 400, 731. |
| `AthenaBytesScannedCutoff` | `10737418240` | Per-query data-scanned ceiling in bytes (10 GiB). |
| `MonthlyBudgetUsd` | `200` | Monthly cost budget for the data plane. |
| `AlertEmail` | empty | Optional address subscribed to the alert topic. |

## 5. What the MSP can and cannot do

The cross-account role is the entire surface. It is assumable only by the MSP
account, only with the external ID.

| Granted | Scope | Why |
|---|---|---|
| `athena:StartQueryExecution`, `GetQueryExecution`, `GetQueryResults`, `StopQueryExecution`, `ListQueryExecutions`, `GetWorkGroup`, `ListDatabases`, `GetDataCatalog` | `jauge-<env>` workgroup and data catalogs | Read aggregates (cost per application, per team, per model) |
| `glue:GetDatabase(s)`, `GetTable(s)`, `GetPartition(s)`, `BatchGetPartition` | catalog, `database/*`, `table/*/*` | Discover the tables it queries — and nothing else: the row-level protection is that the `invocation_logs` table points at objects the role cannot read, so a query against it fails with `AccessDenied` |
| `s3:GetObject`, `PutObject`, `ListBucket`, `GetBucketLocation`, `AbortMultipartUpload`, `ListMultipartUploadParts` | `<logs-bucket>-athena-results` only | Athena writes and reads query output, where the `aggregates` table lives — **aggregates only** |
| `states:StartExecution`, `DescribeExecution`, `ListExecutions`, `GetExecutionHistory`, `StopExecution` | `jauge-replay-<env>` machine and its executions | Launch and follow a replay campaign |
| `ce:GetCostAndUsage`, `GetCostForecast`, `GetDimensionValues`, `GetTags` | account-wide (Cost Explorer has no resource scope) | Reconcile recomputed cost with the invoice |

| Not granted | Detail |
|---|---|
| `s3:GetObject`, `s3:GetObjectVersion`, `s3:ListBucket`, `s3:ListBucketVersions` on `<logs-bucket>` and `<logs-bucket>-replay-results` | **The MSP role never reads the content of the logs.** An explicit `Deny` on `s3:*` covers both buckets, so no future policy edit can silently widen it. |
| `bedrock:InvokeModel` | Replay inference runs in **your** account under `ReplayWorkerRole`, never under the MSP role. |
| Anything on IAM, EC2, Organizations, CloudTrail, KMS administration | Out of scope. |
| Reading the S3 objects behind `invocation_logs` and `cur2` | The catalog entries are visible, the data is not. A `SELECT` on `invocation_logs` returns `AccessDenied`: the request text never leaves your account, and the MSP cannot even list the keys. |
| Reading the S3 objects behind `aggregates` | Allowed, by design: this table holds counts, tokens, recomputed cost and invoiced cost, never request or response text. |

Your CloudTrail records every `AssumeRole` and every API call the MSP makes:
the trail is the audit path (spec section 16).

## 6. After deployment

0. Check the `AggregatesTableName` output: the daily job (attribution module)
   writes Parquet aggregates under `s3://<logs-bucket>-athena-results/aggregates/`.
1. Point **Bedrock model invocation logging** at `LogsBucket`
   (`s3://<logs-bucket>/AWSLogs/<account-id>/BedrockModelInvocationLogs/`),
   with the `application`, `team` and `environment` labels in request metadata
   (EF-ATT-02, EF-ATT-03).
2. Turn the same labels into **cost allocation tags** and enable the **CUR 2.0**
   export to `s3://<logs-bucket>/cur2/` (EF-ATT-08).
3. Give the MSP the role ARN (`RoleArn` output) and the external ID, out of band.
4. Confirm the budget alert reaches `finops@acme.example`.

## 7. Rotating the external ID

```bash
aws cloudformation update-stack \
  --stack-name jauge-client --region eu-west-1 \
  --use-previous-template --capabilities CAPABILITY_NAMED_IAM \
  --parameters ParameterKey=MspAccountId,UsePreviousValue=true \
               ParameterKey=ExternalId,ParameterValue=<new-32-char-secret> \
               ParameterKey=EnvironmentName,UsePreviousValue=true \
               ParameterKey=DataRegion,UsePreviousValue=true \
               ParameterKey=LogsBucketName,UsePreviousValue=true \
               ParameterKey=RetentionDays,UsePreviousValue=true \
               ParameterKey=AthenaBytesScannedCutoff,UsePreviousValue=true \
               ParameterKey=MonthlyBudgetUsd,UsePreviousValue=true \
               ParameterKey=AlertEmail,UsePreviousValue=true
```

The trust policy changes immediately: sessions already assumed stay valid until
they expire (one hour at most, `MaxSessionDuration: 3600`). Rotate on MSP staff
changes and at least every 180 days. `deploy.sh` with a new `--external-id`
does the same thing.

## 8. Uninstall

```bash
# Buckets and the KMS key are retained on purpose: they hold your data.
aws cloudformation delete-stack --stack-name jauge-client --region eu-west-1
aws cloudformation wait stack-delete-complete --stack-name jauge-client --region eu-west-1

# Then, deliberately, empty and delete the three buckets, and schedule the key:
aws s3 rm "s3://acme-jauge-logs" --recursive
aws s3 rb "s3://acme-jauge-logs"
aws s3 rb "s3://acme-jauge-logs-replay-results" --force
aws s3 rb "s3://acme-jauge-logs-athena-results" --force
aws kms schedule-key-deletion --key-id alias/jauge-demo-data --pending-window-in-days 7
```

Retention policies (`DeletionPolicy: Retain`) protect your logs from an
accidental stack deletion. The MSP role disappears with the stack, so access is
revoked in one step.

## 9. Cost of the data plane

Storage and requests only: S3 for logs (a few dollars a month at demo volume),
Athena per query with the data-scanned ceiling above, Step Functions transitions
(negligible), KMS key (1 USD/month), CloudWatch logs (cents). The
`AccountBudget` alarm is set at the value you passed in `MonthlyBudgetUsd`.

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `AccessDenied` on `s3:GetObject` for the MSP | Expected for the log and replay-result buckets. The MSP only reads `<logs-bucket>-athena-results`. |
| `An error occurred (AccessDenied) when calling AssumeRole` | Wrong external ID, or the caller is not in `MspAccountId`. |
| Athena: `Insufficient permissions to read the query output` | The workgroup output location was changed outside the template; redeploy the stack. |
| `cfn-lint` locally | `uv run cfn-lint infra/client-account/template.yaml` — the template is lint-clean, errors and warnings. |
| Budget alert never fires | `AlertEmail` was empty at deploy time, or the subscription is still pending confirmation in the inbox. |
