# Jauge — 3-minute demo script

**Target length:** 3:00 (hard ceiling 3:15; the submission rules ask for about
three minutes)
**Language:** English, spoken
**Source of the timing:** specification §18.2
**Companion documents:** [`HLD.md`](HLD.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) ·
[`TEST-INSTRUCTIONS.md`](TEST-INSTRUCTIONS.md)

> **Rule for every number on screen.** A figure is shown only if the application
> computed it. Nothing is read out that is not visible, and no figure is
> improvised. The *Expected figure* column below names where the number comes
> from; where it says **capture at shooting**, the value is produced by the demo
> run and must be recorded, not guessed. Values quoted as examples come from the
> specification's Annexe B illustration and are **illustrative only**.

---

## 1. Before rolling

### 1.1 Accounts and windows to have open

| # | Window | Why |
|---|---|---|
| 1 | Portal — customer A, **Overview** | Spend per application and per team, reconciled against the invoice |
| 2 | Portal — customer A, **Levers** | Three detected levers, ranked by estimated saving |
| 3 | Portal — **Campaign** detail (ticket classification) | Sample, candidates, cost/quality table |
| 4 | Portal — **Recommendation** | Quantified recommendation, approval button |
| 5 | Portal — **Rollout** | Gateway rule, stages 10 % → 50 % → 100 % |
| 6 | AWS console, **customer A account** — Step Functions execution | Replay running in the customer account |
| 7 | AWS console, **customer A account** — Bedrock evaluation job | Judge scoring pairs |
| 8 | AWS console, **customer A account** — S3 replay-results bucket | Proof that the pairs live here, not at the MSP |
| 9 | AWS console, **MSP account** — AgentCore console | Agents on Runtime, gateway |
| 10 | AWS console, **MSP account** — Bedrock model invocation logs / CloudTrail | Cross-account calls, audit trail |
| 11 | Slide: architecture diagram (`docs/architecture.svg`) | Closing frame |

### 1.2 Demo data

- Customer **A**: three applications — a support assistant on a large model, a
  ticket classifier on the same large model while a small one suffices, a nightly
  document summariser left on the Standard tier.
- Customer **B**: one application, to show the fleet view.
- All traffic is synthetic. No real customer data.

### 1.3 Browser hygiene

- One browser profile per AWS account, distinct colours or a visible account
  banner. A judge must never wonder which account is on screen.
- Hide the bookmark bar, close mail and chat, silence notifications.
- Zoom the portal to a readable size before recording (1440p capture, 1080p
  output).

---

## 2. The script

### 0:00 – 0:20 · The problem

**Say**
> "This company runs generative AI in production on Amazon Bedrock. Its inference
> bill grows every month, and it cannot answer three questions: which application
> and which team spend the money, whether a cheaper model would do the same job,
> and whether a change actually reduced the bill. Saying 'this model is cheaper'
> convinces nobody. Jauge replaces the opinion with a proof."

**Show** — the invoice line for Amazon Bedrock, then cut to the portal Overview
with spend already split per application and per team.

**Expected figure** — total daily spend for customer A, as displayed by the
Overview (**capture at shooting**).

---

### 0:20 – 0:50 · Attribution, reconciled

**Say**
> "Every dollar is attributed to an application, a team and a request type,
> computed from the invocation logs, then reconciled against the invoice. The gap
> is shown, not hidden — an honest gap tells you where the logs are incomplete.
> Three savings levers are already detected, ranked by estimated saving."

**Show** — Overview: spend per application, per team, the reconciliation line
with its gap. Then the Levers page: oversized model, missing prompt cache,
deferrable processing, each with its estimated monthly saving.

**Expected figure** — attribution coverage and reconciliation gap
(**capture at shooting**; production target is < 3 % gap, > 95 % coverage —
these are *targets*, not measurements).

---

### 0:50 – 1:30 · The replay campaign

**Say**
> "The ticket classifier runs on a large model. Jauge draws a reproducible sample
> of 200 real requests, replays them on the current model twice — once for a fresh
> reference, once to measure the model's own variability — then on three cheaper
> candidates. Everything runs inside the customer's account, on the Flex tier.
> A judge model scores each pair, and deterministic checks catch anything a
> generous judge would let through."

**Show** — campaign detail: sample size, seed, candidates, estimated cost shown
*before* launch. Then, in the **customer account**, the Step Functions execution
running and the evaluation job. Back to the portal: the cost/quality table, with
the equivalence rate, its confidence interval, the self-agreement column, latency
and cost per request for each candidate.

**Expected figure** — equivalence rate and interval per candidate, cost per
request before/after (**capture at shooting**). Illustrative shape, from the
specification's Annexe B: 0.975 equivalence, interval low 0.943, self-agreement
0.985, cost per request 0.0041 → 0.0009.

**Do not skip** — the self-agreement column. It is the answer to "but 97 % is not
100 %": the current model is not 100 % equivalent to itself either.

---

### 1:30 – 2:05 · Recommendation, approval, staged rollout

**Say**
> "The agent writes the recommendation — target model, measured quality, expected
> monthly saving, risks, rollout plan. Every number in it was computed by code;
> the agent only writes the sentences. The application owner approves. Then the
> switch: a gateway rule sends 10 % of traffic to the target, then 50, then 100.
> The application's code and model id never change."

**Show** — the recommendation, the approval click, then the Rollout page: the
rule, stage 10 %, online quality re-judged at the stage, then 50 % and 100 %.

**Expected figure** — expected monthly saving and the reduction percentage
(**capture at shooting**; the 20–40 % band in the specification is a *production
target*, not a measured result).

---

### 2:05 – 2:30 · The negative case and the automatic rollback

**Say**
> "Now the support assistant. A candidate looks cheap, but on this application it
> fails: the judge rejects it, and the failing request families are listed. Jauge
> says keep your model — with the evidence. And if quality drops after a switch,
> nobody has to notice at three in the morning: the rule is removed
> automatically."

**Show** — the rejected candidate with its failing families. Then the rollback:
a rollout whose online quality fell below the alert threshold, the rule removed,
the alert sent.

**Expected figure** — the rejected candidate's equivalence rate vs the 95 %
threshold and the failing families (**capture at shooting**).

**Why this segment matters** — it is the difference between a cost dashboard and
a decision tool. Keep it even if the timing is tight.

---

### 2:30 – 2:45 · Verified saving, fleet view

**Say**
> "Thirty days later, cost per request before and after is compared at constant
> volume, and checked against the tagged invoice line. Here is the fleet: two
> customers, what was saved, what is still on the table."

**Show** — savings verification page (before/after cost per request, observed vs
expected saving), then the MSP fleet view with both customers.

**Expected figure** — observed vs expected saving per customer
(**capture at shooting**).

---

### 2:45 – 3:00 · Architecture and close

**Say**
> "Two planes. The customer's account holds the logs, the requests, the replay and
> the judging — the text never leaves it. The MSP account holds aggregates,
> agents and the portal. Strands agents on AgentCore Runtime do the reasoning;
> the numbers stay in code. That is Jauge: attribute, prove, apply."

**Show** — the architecture diagram (`docs/architecture.svg`), held still, then
the repository URL.

---

## 3. Word budget

At a normal presentation pace (~150 words per minute) 3 minutes hold roughly
450 words. The script above is about 430 — leave room for breathing rather than
adding sentences. If a segment overruns, cut from 2:30 (fleet view), never from
2:05 (negative case) or 0:50 (replay).

---

## 4. Shooting checklist

### 4.1 Screenshots to capture for the submission and the README

- [ ] Portal Overview — spend per application and per team (customer A)
- [ ] Portal Overview — reconciliation gap line
- [ ] Levers page — three levers ranked by estimated saving
- [ ] Campaign detail — sample size, seed, candidates, estimated cost
- [ ] Step Functions execution — replay running (customer A account)
- [ ] Bedrock evaluation job — judge scoring (customer A account)
- [ ] S3 replay-results bucket — pairs stored in the customer account
- [ ] Campaign results table — equivalence rate, interval, self-agreement, cost
- [ ] Recommendation document — quality / cost / latency blocks
- [ ] Approval confirmation
- [ ] Gateway rule — stage 10 %, then 100 %
- [ ] Rollout page — online quality per stage
- [ ] Rejected candidate — failing request families
- [ ] Automatic rollback — rule removed, alert
- [ ] Savings verification — before/after cost per request
- [ ] MSP fleet view — two customers
- [ ] Architecture diagram
- [ ] IAM cross-account role — permissions policy showing **no** read on the log
      bucket (this single screenshot is the privacy claim made visible)

### 4.2 Technical checks before recording

- [ ] Demo environment reset to a known state (`docs/TEST-INSTRUCTIONS.md`)
- [ ] Deterministic seed confirmed: the same sample on every take
- [ ] Portal served from the public URL judges will use
- [ ] Demo credentials verified against
      [`TEST-INSTRUCTIONS.md`](TEST-INSTRUCTIONS.md#3-demo-accounts-planned)
- [ ] No real account id, no key, no personal data visible in any frame
- [ ] Audio level checked; no third-party music, no third-party brand visible
- [ ] Recording at 1080p minimum; text legible when the video is played at
      default size
- [ ] Final cut under 3:15, uploaded as **public** on YouTube, English audio

### 4.3 Numbers to write down during the shoot

Fill this table at the end of the recording session — it feeds the submission
description and the README, and nothing in it may be typed from memory.

| Figure | Value measured | Where it comes from |
|---|---|---|
| Attribution coverage (customer A) | | Portal Overview |
| Reconciliation gap (customer A) | | Portal Overview |
| Campaign sample size / candidates | | Campaign detail |
| Equivalence rate per candidate | | Campaign results |
| Self-agreement of the current model | | Campaign results |
| Cost per request before / after | | Campaign results |
| Expected monthly saving | | Recommendation |
| Observed saving after 30 days | | Savings verification |
| Rejected candidate rate + failing families | | Campaign results |

> Until this table is filled, the README and the submission description carry
> **targets**, explicitly labelled as targets. No measured figure is published
> from an estimate.

---

## 5. Voice-over notes

- Say "replay", not "simulation": the requests are real, only the model changes.
- Say "inside the customer's account" whenever the replay appears on screen — it
  is the design claim judges will test.
- Never say "the AI decided". Say "the code computed, the agent wrote, the owner
  approved".
- Avoid superlatives. The numbers are the argument.
