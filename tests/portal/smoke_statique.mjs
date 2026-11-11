/**
 * Fumée du mode statique du portail — exécutée par pytest via Node.
 *
 * Vérifie, sans navigateur ni réseau, que `portal/js/statique.js` :
 *  - détecte le mode statique depuis un instantané ;
 *  - sert les réponses enregistrées (mêmes formes que l'API) ;
 *  - refuse l'accès aux données d'un autre client (403, ENF-SEC-02) ;
 *  - refuse une bascule sans approbation (409, ENF-IA-02) ;
 *  - déroule les paliers 10 → 50 → 100, puis clôture et refus de rollback ;
 *  - refuse explicitement ce qui exige le plan de contrôle réel (501).
 *
 * `window` et `fetch` sont bouchonnés : l'instantané est lu sur disque.
 * Sortie attendue : `SMOKE OK` et un code de retour 0.
 */

import { readFileSync } from "node:fs";
import { argv } from "node:process";

const cheminInstantane = argv[2];
if (!cheminInstantane) {
  console.error("usage: node smoke_statique.mjs <chemin/demo.json>");
  process.exit(2);
}
const instantane = JSON.parse(readFileSync(cheminInstantane, "utf8"));

// --- bouchons d'environnement navigateur --------------------------------------
globalThis.window = { location: { pathname: "/jauge/index.html" } };
globalThis.fetch = async (url) => {
  const chemin = String(url);
  if (chemin.endsWith("data/demo.json")) {
    return { ok: true, status: 200, json: async () => instantane };
  }
  return { ok: false, status: 404, json: async () => ({}) };
};

const statique = await import("../../portal/js/statique.js");
const { detecterMode, servirStatique, metaInstantane, racine } = statique;

let echecs = 0;
function verifier(condition, message) {
  if (!condition) {
    console.error("  KO  " + message);
    echecs += 1;
  }
}

// --- détection ------------------------------------------------------------------
const mode = await detecterMode();
verifier(mode === "statique", `mode détecté = ${mode} (attendu : statique)`);
verifier(racine() === "/jauge/", `racine = ${racine()} (attendu : /jauge/)`);
verifier(Boolean(metaInstantane()), "métadonnées de l'instantané absentes");

const clients = (instantane.meta || {}).clients || [];
verifier(clients.length >= 2, "l'instantané doit porter au moins deux clients");
const [clientA, clientB] = clients;
const sessionA = { clientId: clientA, role: "responsable_application", utilisateur: "alice" };
const sessionB = { clientId: clientB, role: "directeur", utilisateur: "bob" };
const sessionAdmin = { clientId: clientA, role: "administrateur_plateforme", utilisateur: "msp" };

// --- lectures -------------------------------------------------------------------
const listeApps = servirStatique("GET", `/v1/tenants/${clientA}/applications`, null, sessionA);
verifier(listeApps.ok && Array.isArray(listeApps.corps), "applications du client A illisibles");
verifier(listeApps.corps.length > 0, "aucune application pour le client A");
const appId = listeApps.corps[0].id;

const couts = servirStatique("GET", `/v1/applications/${appId}/costs`, null, sessionA);
verifier(couts.ok, `coûts de ${appId} illisibles`);

// --- cloisonnement (ENF-SEC-02) ---------------------------------------------------
const refuse = servirStatique("GET", `/v1/applications/${appId}`, null, sessionB);
verifier(!refuse.ok && refuse.statut === 403, "le client B ne doit pas lire l'application du client A");
verifier(
  refuse.corps?.error?.code === "tenant_isolation_violation",
  "le refus doit porter le code tenant_isolation_violation",
);
const adminOk = servirStatique("GET", `/v1/applications/${appId}`, null, sessionAdmin);
verifier(adminOk.ok, "l'administrateur de plateforme doit lire toutes les applications");

// --- approbation obligatoire puis bascule par paliers (ENF-IA-02, EF-BAS-02) -------
let recoEnAttente = null;
for (const app of listeApps.corps) {
  const reponse = servirStatique("GET", `/v1/applications/${app.id}/recommendations`, null, sessionA);
  recoEnAttente = (reponse.corps || []).find((r) => r.status === "PENDING_APPROVAL") || null;
  if (recoEnAttente) break;
}
if (!recoEnAttente) {
  console.error("  KO  aucune recommandation en attente dans l'instantané");
  echecs += 1;
} else {
  const avant = servirStatique("POST", `/v1/recommendations/${recoEnAttente.id}/rollout`, {}, sessionA);
  verifier(!avant.ok && avant.statut === 409, "une bascule sans approbation doit être refusée");
  verifier(avant.corps?.error?.code === "approval_required", "le refus doit dire approval_required");

  const decision = servirStatique(
    "POST",
    `/v1/recommendations/${recoEnAttente.id}/decision`,
    { decision: "approuver", motif: "feu vert" },
    sessionA,
  );
  verifier(decision.ok && decision.corps.status === "APPROVED", "approbation locale non enregistrée");

  const deuxieme = servirStatique(
    "POST",
    `/v1/recommendations/${recoEnAttente.id}/decision`,
    { decision: "refuser" },
    sessionA,
  );
  verifier(!deuxieme.ok && deuxieme.statut === 409, "une recommandation déjà tranchée est retranchable");

  const demarrage = servirStatique(
    "POST",
    `/v1/recommendations/${recoEnAttente.id}/rollout`,
    { paliers: [10, 50, 100] },
    sessionA,
  );
  verifier(demarrage.ok && demarrage.statut === 201, "démarrage refusé après approbation");
  const bascule = demarrage.corps?.bascule || {};
  verifier(bascule.palier_actuel_pourcentage === 10, "le premier palier doit être 10 %");
  verifier(bascule._paliers === undefined, "les champs internes ne doivent pas fuiter");

  const palier2 = servirStatique("POST", `/v1/rollouts/${bascule.id}/advance`, null, sessionA);
  verifier(palier2.ok && palier2.corps.palier_actuel_pourcentage === 50, "palier 50 % non atteint");
  const palier3 = servirStatique("POST", `/v1/rollouts/${bascule.id}/advance`, null, sessionA);
  verifier(palier3.ok && palier3.corps.palier_actuel_pourcentage === 100, "palier 100 % non atteint");
  const cloture = servirStatique("POST", `/v1/rollouts/${bascule.id}/advance`, null, sessionA);
  verifier(cloture.ok && cloture.corps.actif === false, "la bascule doit se clôre après 100 %");

  const lecture = servirStatique("GET", `/v1/rollouts/${bascule.id}`, null, sessionA);
  verifier(lecture.ok && lecture.corps.id === bascule.id, "la bascule locale doit être relisible");

  const arriere = servirStatique("POST", `/v1/rollouts/${bascule.id}/rollback`, null, sessionA);
  verifier(!arriere.ok && arriere.statut === 409, "un rollback sur bascule close doit être refusé");
}

// --- ce qui exige le plan de contrôle réel ----------------------------------------
const campagne = servirStatique(
  "POST",
  `/v1/applications/${appId}/campaigns`,
  { modeles_candidats: ["x"] },
  sessionA,
);
verifier(!campagne.ok && campagne.statut === 501, "le lancement de campagne doit refuser en statique");
verifier(campagne.corps?.error?.code === "static_demo", "le refus doit dire pourquoi (static_demo)");

if (echecs > 0) {
  console.error(`SMOKE KO : ${echecs} vérification(s) en échec`);
  process.exit(1);
}
console.log("SMOKE OK");
