/**
 * Page « About & demo accounts » — EF-POR-02, §19.1.
 *
 * Décrit aux jugateurs le compte de démonstration, les rôles et les points
 * d'API consommés. Contenu statique en anglais, sans aucune donnée métier.
 */

import { html, carte, tableau, fil } from "../dom.js";
import { CLIENTS_DEMO, ROLES_DEMO, SESSION_DEFAUT } from "../config.js";

/** Points d'API consommés, tels qu'ils apparaissent dans le cahier des charges. */
const POINTS_API = [
  ["GET", "/v1/tenants", "Clients visible to the caller"],
  ["GET", "/v1/tenants/{id}", "Client record"],
  ["GET", "/v1/tenants/{id}/applications", "Applications of a client"],
  ["GET", "/v1/applications/{id}", "Application record"],
  ["GET", "/v1/applications/{id}/costs", "Cost per day and per model"],
  ["GET", "/v1/tenants/{id}/reconciliation", "Reconciliation with the AWS invoice"],
  ["GET", "/v1/applications/{id}/levers", "Detected saving levers"],
  ["GET", "/v1/applications/{id}/recommendations", "Recommendations (Annex B)"],
  ["POST", "/v1/recommendations/{id}/decision", "Approve, reject or ask for a wider campaign"],
  ["POST", "/v1/recommendations/{id}/rollout", "Staged gateway rollout"],
  ["GET", "/v1/rollouts/{id}", "Rollout state and online quality"],
  ["POST", "/v1/rollouts/{id}/rollback", "Manual rollback"],
  ["GET", "/v1/tenants/{id}/savings", "Savings expected and measured"],
  ["GET", "/v1/campaigns/{id}", "Replay campaign"],
  ["GET", "/v1/campaigns/{id}/results", "Results per candidate"],
  ["POST", "/v1/applications/{id}/campaigns", "Launch a wider campaign"],
  ["GET", "/v1/campaigns/{id}/reviews", "Human review sample (extension)"],
  ["POST", "/v1/campaigns/{id}/reviews", "Human verdict (extension)"],
];

/** Carte des identifiants de démonstration à saisir dans le sélecteur. */
function carteComptes() {
  const clients = CLIENTS_DEMO.map((client) => [client.libelle, client.id]);
  const roles = ROLES_DEMO.map((role) => [role.libelle, role.id]);
  return html`<div class="grille">
    ${carte(
      "Demo clients",
      tableau({ entetes: ["Client", "Identifier"], lignes: clients }),
    )}
    ${carte("Demo roles", tableau({ entetes: ["Role", "Header value"], lignes: roles }))}
  </div>`;
}

/** Rappel du contrat d'authentification de démonstration. */
function carteAuthentification() {
  return carte(
    "How the demo account works",
    html`<p>
      The portal is served by the control-plane API on the same origin. The demo selector sends
      <code>X-Jauge-Tenant</code> and <code>X-Jauge-Role</code> headers on every call; the API
      applies the §6 roles and the per-client isolation (ENF-SEC-02).
    </p>
    <p>
      Default selection: client <code>${SESSION_DEFAUT.clientId}</code>, role
      <code>${SESSION_DEFAUT.role}</code>, user <code>${SESSION_DEFAUT.utilisateur}</code>.
      Switching to another client with a client role demonstrates the isolation refusal.
    </p>`,
  );
}

/** Tableau des points d'API consommés par le portail. */
function carteApi() {
  return carte(
    "API endpoints used by this portal",
    tableau({
      legende: "Every figure displayed by the portal comes from one of these endpoints.",
      entetes: ["Method", "Path", "Purpose"],
      lignes: POINTS_API,
    }),
  );
}

/** Vue complète de la page d'information. */
export async function rendre() {
  const entete = html`${fil([{ libelle: "About", href: "#/about" }])}
    <h1 class="titre-page">About this portal &amp; demo accounts</h1>
    <p class="sous-titre">
      Jauge attributes every dollar of generative-AI inference to an application and a team, proves
      a cheaper model with a replay campaign, then applies the change in stages through an
      AgentCore gateway rule.
    </p>`;
  return html`${entete}${carteComptes()}${carteAuthentification()}${carteApi()}`;
}
