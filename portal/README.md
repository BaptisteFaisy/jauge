# Jauge portal

Static single-page application for the Jauge control plane. **No build step, no
npm, no bundler**: plain HTML, CSS and native ES modules. The control-plane API
(module M8) serves this directory as-is, so `portal/index.html` works as soon as
the API is up.

```
portal/
  index.html            shell: demo account selector, navigation, main area
  assets/app.css        single stylesheet (no external font, no CDN)
  js/
    config.js           demo clients, §6 roles, capability lists
    api.js              HTTP client (X-Jauge-* headers, error normalisation)
    dom.js              safe templating: every API value is HTML-escaped
    format.js           display formatting only (no business computation)
    chart.js            CSS histogram, geometry only
    router.js           hash router (#/client, #/app/<id>, …)
    main.js             bootstrap, demo session, error and loading states
    views/              one module per screen
```

## Screens

| Screen | Route | Requirement |
|---|---|---|
| Client dashboard — spend per application and team, trend, levers, savings realised vs expected | `#/client` | EF-RAP-01 |
| Managed fleet — all clients, savings, reconciliation alerts | `#/fleet` | EF-RAP-02 |
| Application detail — cost per request, models, daily history, levers, recommendations | `#/app/<appId>` | EF-RAP-01 |
| Replay campaign — per-candidate equivalence, confidence interval, latency, cost | `#/campaign/<campaignId>` | §13.3, Annex B |
| Recommendation — approve / reject / request a wider campaign | `#/app/<appId>/recommendation/<recoId>` | EF-REC-02 |
| Rollout — 10 / 50 / 100 % steps, online quality, rollback | `#/rollout/<rolloutId>` | EF-BAS-01..04 |
| Human review — 30-pair sample, vote, judge/human agreement | `#/campaign/<campaignId>/review` | EF-EVA-05 |
| About & demo accounts — identifiers, roles, endpoints used | `#/about` | EF-POR-02, §19.1 |

## Demo account for the judges (EF-POR-02)

The selector in the header sends two headers on every API call:
`X-Jauge-Tenant` and `X-Jauge-Role`. Pre-filled values: **client `t-demo-a`**,
**role `analyste_finops`**, user `judge-demo`.

| Client | Identifier |
|---|---|
| Client A — Demo (3 applications) | `t-demo-a` |
| Client B — Demo (1 application) | `t-demo-b` |

| Role (§6) | Header value |
|---|---|
| FinOps analyst (MSP) | `analyste_finops` |
| Platform administrator (MSP) | `administrateur_plateforme` |
| Account manager (MSP) | `responsable_compte` |
| Application owner (client) | `responsable_application` |
| Reviewer (client) | `relecteur` |
| Finance / technical director (client) | `directeur` |

Suggested walkthrough: open `#/client` as `analyste_finops` on `t-demo-a`, then
switch to `responsable_application` to approve a recommendation and start the
rollout, then to `relecteur` to vote on the human-review sample. Switching to
client `t-demo-b` with a client role demonstrates the per-client isolation
(ENF-SEC-02): the API refuses the cross-client reads and the portal shows the
refusal.

## Rules followed by this code

- **Every figure comes from the API.** The browser formats and lays out values;
  it never sums, averages or derives a business metric. Park-level totals are
  therefore not computed here (see the pull-request notes).
- **No external resource.** No CDN, no web font, no analytics, no URL outside
  the portal's own origin.
- **No injection.** All interpolations go through `js/dom.js`, which escapes
  `& < > " '`. `innerHTML` is used in exactly one place (`monter`).
- **Errors are visible.** Failed calls render a readable panel with the HTTP
  status, the API error code and a retry button; long calls time out.
- **Accessibility.** Skip link, landmarks, `aria-current` on the active tab,
  labelled form controls, `role="status"` / `role="alert"` for loading and
  errors, contrast above 7:1 for text.

## Tests

Static and local-serving tests, no Node, no network:

```bash
uv run pytest -q tests/portal
```
