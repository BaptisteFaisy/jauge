/**
 * Client HTTP du portail Jauge.
 *
 * Tous les appels visent l'API du plan de contrôle (§15) servie sur la même
 * origine que le portail : aucune URL absolue, aucun service tiers, aucun CDN.
 * Le compte de démonstration (EF-POR-02) est transmis par les en-têtes
 * `X-Jauge-Tenant` et `X-Jauge-Role`.
 */

import { DELAI_API_MS } from "./config.js";
import { detecterMode, racine, servirStatique } from "./statique.js";

const ENTETE_TENANT = "X-Jauge-Tenant";
const ENTETE_ROLE = "X-Jauge-Role";
const ENTETE_UTILISATEUR = "X-Jauge-User";

/** Erreur d'API normalisée, affichable telle quelle par les vues. */
export class ErreurApi extends Error {
  constructor(message, { statut = 0, code = "", details = null } = {}) {
    super(message);
    this.name = "ErreurApi";
    this.statut = statut;
    this.code = code;
    this.details = details;
  }
}

/** Mode de fonctionnement résolu une fois : "live", "statique" ou "indisponible". */
let promesseMode = null;

/** Mode du portail, résolu une seule fois : API réelle ou instantané statique. */
export function modePortail() {
  if (!promesseMode) {
    promesseMode = detecterMode();
  }
  return promesseMode;
}

/** Chemin encodé d'une ressource, sans injection possible dans l'URL. */
function segment(valeur) {
  return encodeURIComponent(String(valeur ?? ""));
}

/**
 * Construit un client d'API lié à une session de démonstration.
 * @param {{clientId: string, role: string, utilisateur: string}} session
 */
export function creerApi(session) {
  function entetes() {
    return {
      Accept: "application/json",
      [ENTETE_TENANT]: session.clientId,
      [ENTETE_ROLE]: session.role,
      [ENTETE_UTILISATEUR]: session.utilisateur,
    };
  }

  async function appeler(methode, chemin, corps) {
    const mode = await modePortail();
    if (mode === "statique") {
      // Portail publié sans backend : l'instantané `data/demo.json` répond, avec
      // les mêmes formes JSON, le même cloisonnement et les mêmes refus.
      const resultat = servirStatique(methode, chemin, corps, session);
      if (!resultat.ok) {
        const erreur = (resultat.corps && resultat.corps.error) || {};
        throw new ErreurApi(erreur.message || `Static demo error ${resultat.statut}`, {
          statut: resultat.statut,
          code: erreur.code || "static_demo",
          details: erreur.details || null,
        });
      }
      return resultat.corps;
    }
    const controleur = new AbortController();
    const minuteur = setTimeout(() => controleur.abort(), DELAI_API_MS);
    const options = { method: methode, headers: entetes(), signal: controleur.signal };
    if (corps !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(corps);
    }
    let reponse;
    try {
      reponse = await fetch(racine() + chemin.replace(/^\//, ""), options);
    } catch (erreur) {
      clearTimeout(minuteur);
      if (erreur && erreur.name === "AbortError") {
        throw new ErreurApi("The API did not answer in time.", { code: "timeout" });
      }
      throw new ErreurApi("The API is unreachable.", { code: "network" });
    }
    clearTimeout(minuteur);
    return lireReponse(reponse);
  }

  async function lireReponse(reponse) {
    const type = reponse.headers.get("content-type") || "";
    let charge = null;
    if (type.includes("application/json")) {
      charge = await reponse.json().catch(() => null);
    }
    if (!reponse.ok) {
      const erreur = (charge && charge.error) || {};
      throw new ErreurApi(erreur.message || `API error ${reponse.status}`, {
        statut: reponse.status,
        code: erreur.code || "",
        details: erreur.details || null,
      });
    }
    return charge;
  }

  return {
    /** `GET /v1/tenants` — clients visibles par l'appelant. */
    clients: () => appeler("GET", "/v1/tenants"),

    /** `GET /v1/tenants/{id}` — fiche d'un client. */
    client: (id) => appeler("GET", `/v1/tenants/${segment(id)}`),

    /** `GET /v1/tenants/{id}/applications` — applications d'un client. */
    applications: (id) => appeler("GET", `/v1/tenants/${segment(id)}/applications`),

    /** `GET /v1/applications/{id}` — fiche d'une application. */
    application: (id) => appeler("GET", `/v1/applications/${segment(id)}`),

    /** `GET /v1/applications/{id}/costs` — coûts par jour et par modèle. */
    couts: (id) => appeler("GET", `/v1/applications/${segment(id)}/costs`),

    /** `GET /v1/tenants/{id}/reconciliation` — rapprochement avec la facture. */
    rapprochement: (id) => appeler("GET", `/v1/tenants/${segment(id)}/reconciliation`),

    /** `GET /v1/applications/{id}/levers` — leviers détectés. */
    leviers: (id) => appeler("GET", `/v1/applications/${segment(id)}/levers`),

    /** `GET /v1/applications/{id}/ombre` — état de l'observation fantôme/canari. */
    ombre: (id) => appeler("GET", `/v1/applications/${segment(id)}/ombre`),

    /** `GET /v1/campaigns/{id}` — état d'une campagne de rejeu. */
    campagne: (id) => appeler("GET", `/v1/campaigns/${segment(id)}`),

    /** `GET /v1/campaigns/{id}/results` — résultats agrégés par candidat. */
    resultats: (id) => appeler("GET", `/v1/campaigns/${segment(id)}/results`),

    /** `GET /v1/campagnes/{id}/protocole` — protocole pré-enregistré (empreintes). */
    protocoleCampagne: (id) => appeler("GET", `/v1/campagnes/${segment(id)}/protocole`),

    /** `GET /v1/applications/{id}/recommendations` — recommandations (Annexe B). */
    recommandations: (id) =>
      appeler("GET", `/v1/applications/${segment(id)}/recommendations`),

    /** `POST /v1/recommendations/{id}/decision` — approbation ou refus (EF-REC-02). */
    decider: (id, decision, motif) =>
      appeler("POST", `/v1/recommendations/${segment(id)}/decision`, {
        decision,
        motif: motif || null,
      }),

    /** `POST /v1/recommendations/{id}/rollout` — bascule par paliers (§8.6). */
    basculer: (id, paliers, motif) =>
      appeler("POST", `/v1/recommendations/${segment(id)}/rollout`, {
        paliers,
        motif: motif || null,
      }),

    /** `GET /v1/rollouts/{id}` — état d'une bascule. */
    bascule: (id) => appeler("GET", `/v1/rollouts/${segment(id)}`),

    /** `POST /v1/rollouts/{id}/rollback` — retour arrière (EF-BAS-03). */
    annulerBascule: (id) => appeler("POST", `/v1/rollouts/${segment(id)}/rollback`),

    /** `GET /v1/tenants/{id}/savings` — économies attendues et constatées. */
    economies: (id) => appeler("GET", `/v1/tenants/${segment(id)}/savings`),

    /** `POST /v1/applications/{id}/campaigns` — nouvelle campagne de rejeu. */
    lancerCampagne: (id, corps) =>
      appeler("POST", `/v1/applications/${segment(id)}/campaigns`, corps),

    /** `GET /v1/campaigns/{id}/reviews` — échantillon de relecture (extension EF-EVA-05). */
    relectures: (id) => appeler("GET", `/v1/campaigns/${segment(id)}/reviews`),

    /** `POST /v1/campaigns/{id}/reviews` — vote humain (extension EF-EVA-05). */
    voter: (id, corps) => appeler("POST", `/v1/campaigns/${segment(id)}/reviews`, corps),
  };
}
