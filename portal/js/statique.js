/**
 * Mode statique du portail : servir la démonstration sans backend.
 *
 * Quand le portail est publié sur un hébergement statique (GitHub Pages), il n'y
 * a ni API ni Lambda : ce module sert alors les réponses enregistrées dans
 * `data/demo.json`, produit par `scripts/export_portail_statique.py` à partir de
 * la **vraie** application FastAPI (mêmes formes JSON, aucune donnée recalculée
 * ici — ENF-IA-01).
 *
 * Ce qui reste fidèle à l'API, volontairement :
 * - le **cloisonnement** (ENF-SEC-02) : un rôle client qui demande les données
 *   d'un autre client reçoit le même 403 `tenant_isolation_violation` ;
 * - l'**approbation obligatoire** (ENF-IA-02) : une bascule sur une
 *   recommandation non approuvée reçoit le même 409 `approval_required` ;
 * - les paliers 10 → 50 → 100 et le retour arrière.
 *
 * Ce qui est local au navigateur : les écritures (décision, bascule, palier,
 * rollback) modifient l'instantané en mémoire et sont perdues au rechargement.
 * Un bandeau le dit explicitement à l'utilisateur.
 */

const CHEMIN_INSTANTANE = "data/demo.json";
const CHEMIN_SONDE = "health";
const PALIERS_PAR_DEFAUT = [10, 50, 100];
const STATUTS_DECISION = { approuver: "APPROVED", refuser: "REJECTED" };

let instantane = null;
let proprietaires = new Map();
const surcharges = new Map();
let compteurRegles = 0;

/** Racine de publication : `/` en local, `/jauge/` sur GitHub Pages. */
export function racine() {
  const chemin = window.location.pathname || "/";
  return chemin.slice(0, chemin.lastIndexOf("/") + 1);
}

/** URL résolue depuis la racine de publication (jamais de chemin absolu). */
function resoudre(chemin) {
  return racine() + String(chemin).replace(/^\//, "");
}

/**
 * Détecte le mode de fonctionnement.
 * @returns {Promise<"live"|"statique"|"indisponible">}
 */
export async function detecterMode() {
  if (await sonderApi()) {
    return "live";
  }
  return (await chargerInstantane()) ? "statique" : "indisponible";
}

/** L'API répond-elle sur la même origine ? (sonde `/health`) */
async function sonderApi() {
  try {
    const reponse = await fetch(resoudre(CHEMIN_SONDE), { headers: { Accept: "application/json" } });
    if (!reponse.ok) return false;
    const corps = await reponse.json();
    return Boolean(corps && corps.status === "ok");
  } catch {
    return false;
  }
}

/** Charge l'instantané statique ; rend `true` s'il est utilisable. */
async function chargerInstantane() {
  try {
    const reponse = await fetch(resoudre(CHEMIN_INSTANTANE), {
      headers: { Accept: "application/json" },
    });
    if (!reponse.ok) return false;
    const charge = await reponse.json();
    if (!charge || !charge.reponses) return false;
    instantane = charge;
    proprietaires = indexerProprietaires(charge.reponses);
    return true;
  } catch {
    return false;
  }
}

/** Métadonnées de l'instantané (date de génération, avertissement). */
export function metaInstantane() {
  return (instantane && instantane.meta) || null;
}

/** `true` si le portail tourne sur l'instantané statique. */
export function estStatique() {
  return Boolean(instantane);
}

/** Réinitialise les écritures locales (rechargement de la démo). */
export function reinitialiser() {
  surcharges.clear();
  compteurRegles = 0;
}

/** Construit la carte « entité → client propriétaire » depuis l'instantané. */
function indexerProprietaires(reponses) {
  const carte = new Map();
  for (const [clef, valeur] of Object.entries(reponses)) {
    if (!clef.startsWith("GET ")) continue;
    const chemin = clef.slice(4);
    const tenant = (chemin.match(/^\/v1\/tenants\/([^/]+)/) || [])[1] || null;
    const objets = Array.isArray(valeur) ? valeur : [valeur];
    for (const objet of objets) {
      if (!objet || typeof objet !== "object") continue;
      const identifiant = objet.id;
      if (!identifiant) continue;
      const proprietaire = objet.tenant_id || objet.tenantId || (tenant && decodeURIComponent(tenant));
      if (proprietaire) carte.set(clefDepuisChemin(chemin, identifiant), proprietaire);
    }
  }
  return carte;
}

/** Préfixe interne de chaque ressource (clé des cartes locales). */
const PREFIXES = {
  tenants: "tenant",
  applications: "app",
  campaigns: "campagne",
  rollouts: "bascule",
  recommendations: "reco",
};

/** Ressource visée par un chemin d'API (`applications`, `rollouts`…), ou "". */
function ressourceDe(chemin) {
  return (String(chemin).match(/^\/v1\/([^/?]+)/) || [])[1] || "";
}

/** Clé d'entité : `<ressource>:<identifiant>` (ex. `app:ticket-classifier`). */
function clefEntite(ressource, identifiant) {
  return `${PREFIXES[ressource] || ressource}:${identifiant}`;
}

/** Clé d'entité déduite d'un chemin d'API et d'un identifiant. */
function clefDepuisChemin(chemin, identifiant) {
  return clefEntite(ressourceDe(chemin), identifiant);
}

/** Entité visée par un chemin d'API, ou `null` si le chemin ne porte pas d'identifiant. */
function entiteDuChemin(chemin) {
  const trouve = String(chemin).match(
    /^\/v1\/(tenants|applications|campaigns|rollouts|recommendations)\/([^/?]+)/,
  );
  if (!trouve) return null;
  return clefEntite(trouve[1], decodeURIComponent(trouve[2]));
}

/** Refus de cloisonnement si la ressource appartient à un autre client. */
function verifierCloisonnement(chemin, session) {
  if (!session || session.role === "administrateur_plateforme") return null;
  const clef = entiteDuChemin(chemin);
  if (!clef) return null;
  const proprietaire = proprietaires.get(clef);
  if (!proprietaire || proprietaire === session.clientId) return null;
  return {
    ok: false,
    statut: 403,
    corps: {
      error: {
        code: "tenant_isolation_violation",
        message: "Access denied: this resource belongs to another customer.",
        details: { ressource: clef, client_appelant: session.clientId },
      },
    },
  };
}

/**
 * Sert un appel en mode statique.
 * @returns {{ok: boolean, statut: number, corps: any}}
 */
export function servirStatique(methode, chemin, corps, session) {
  const refus = verifierCloisonnement(chemin, session);
  if (refus) return refus;
  if (methode === "GET") return servirLecture(chemin, session);
  if (methode === "POST") return servirEcriture(chemin, corps, session);
  return erreur(405, "method_not_allowed", `Static demo: ${methode} is not supported.`);
}

/** Lecture : instantané + écritures locales éventuelles. */
function servirLecture(chemin, session) {
  const clef = `GET ${chemin}`;
  const brut = instantane.reponses[clef];
  if (brut === undefined) {
    const locale = surcharges.get(clef);
    if (locale !== undefined) return { ok: true, statut: 200, corps: locale };
    const bascule = surcharges.get(clefDepuisChemin(chemin, dernierSegment(chemin)));
    if (bascule !== undefined) return { ok: true, statut: 200, corps: bascule };
    return erreur(404, "not_found", `Static demo: no recorded answer for ${clef}.`);
  }
  if (brut && brut._statut) {
    return erreur(brut._statut, "upstream_error", String(brut._corps || "").slice(0, 200));
  }
  return { ok: true, statut: 200, corps: appliquerSurcharges(chemin, brut, session) };
}

/** Dernier segment d'un chemin (identifiant de ressource). */
function dernierSegment(chemin) {
  const morceaux = String(chemin).split("/").filter(Boolean);
  return decodeURIComponent(morceaux[morceaux.length - 1] || "");
}

/** Applique les écritures locales aux réponses de l'instantané. */
function appliquerSurcharges(chemin, corps, session) {
  if (chemin === "/v1/tenants") return filtrerClients(corps, session);
  const copie = JSON.parse(JSON.stringify(corps));
  if (Array.isArray(copie)) {
    return copie.map((item) => (item && item.id ? surchargeOu(clefDepuisChemin(chemin, item.id), item) : item));
  }
  if (copie && copie.id) return surchargeOu(clefDepuisChemin(chemin, copie.id), copie);
  return copie;
}

/** Liste des clients : filtrée selon le rôle, comme le fait l'API. */
function filtrerClients(clients, session) {
  if (!Array.isArray(clients)) return clients;
  if (session && (session.role === "administrateur_plateforme" || session.role === "analyste_finops")) {
    return clients;
  }
  return clients.filter((client) => client && client.id === (session || {}).clientId);
}

/** Rend la version locale d'une entité si elle a été modifiée. */
function surchargeOu(clef, objet) {
  return surcharges.has(clef) ? surcharges.get(clef) : objet;
}

/** Écriture : décisions, bascule, paliers et retour arrière, en local. */
function servirEcriture(chemin, corps, session) {
  if (/^\/v1\/recommendations\/[^/]+\/decision$/.test(chemin)) {
    return deciderLocalement(dernierSegmentAvant(chemin), corps || {}, session);
  }
  if (/^\/v1\/recommendations\/[^/]+\/rollout$/.test(chemin)) {
    return basculerLocalement(dernierSegmentAvant(chemin), corps || {});
  }
  if (/^\/v1\/rollouts\/[^/]+\/advance$/.test(chemin)) {
    return avancerLocalement(dernierSegmentAvant(chemin));
  }
  if (/^\/v1\/rollouts\/[^/]+\/rollback$/.test(chemin)) {
    return annulerLocalement(dernierSegmentAvant(chemin), session);
  }
  return erreur(
    501,
    "static_demo",
    "Static demo: this action needs the live control plane (replay, judge and gateway calls)."
  );
}

/** Segment d'identifiant avant le dernier (ex. `reco` dans `/v1/recommendations/x/decision`). */
function dernierSegmentAvant(chemin) {
  const morceaux = String(chemin).split("/").filter(Boolean);
  return decodeURIComponent(morceaux[morceaux.length - 2] || "");
}

/** Retrouve une recommandation dans l'instantané (ou sa version locale). */
function trouverRecommandation(recoId) {
  const clef = `reco:${recoId}`;
  if (surcharges.has(clef)) return surcharges.get(clef);
  for (const [cle, valeur] of Object.entries(instantane.reponses)) {
    if (!cle.startsWith("GET /v1/applications/") || !cle.endsWith("/recommendations")) continue;
    const trouve = (Array.isArray(valeur) ? valeur : []).find((item) => item && item.id === recoId);
    if (trouve) return JSON.parse(JSON.stringify(trouve));
  }
  return null;
}

/** `POST …/decision` : même règle d'état que l'API (une seule décision possible). */
function deciderLocalement(recoId, corps, session) {
  const reco = trouverRecommandation(recoId);
  if (!reco) return erreur(404, "not_found", `Recommendation ${recoId} introuvable.`);
  const statut = STATUTS_DECISION[corps.decision];
  if (!statut) {
    return erreur(
      422,
      "validation_error",
      `Static demo: decision "${corps.decision}" is not accepted by the API contract.`
    );
  }
  if (reco.status !== "PENDING_APPROVAL") {
    return erreur(409, "invalid_state_transition", "This recommendation was already decided.", {
      recommandation_id: recoId,
      statut: reco.status,
    });
  }
  const maj = {
    ...reco,
    status: statut,
    approvedBy: (session && (session.utilisateur || session.role)) || "demo",
    approvalNotes: corps.motif || null,
  };
  surcharges.set(`reco:${recoId}`, maj);
  return { ok: true, statut: 200, corps: maj };
}

/** `POST …/rollout` : refus sans approbation, sinon règle de passerelle au palier 1. */
function basculerLocalement(recoId, corps) {
  const reco = trouverRecommandation(recoId);
  if (!reco) return erreur(404, "not_found", `Recommendation ${recoId} introuvable.`);
  if (reco.status !== "APPROVED") {
    return erreur(
      409,
      "approval_required",
      "No rollout without a human approval of the recommendation.",
      { recommandation_id: recoId, statut: reco.status }
    );
  }
  const paliers = Array.isArray(corps.paliers) && corps.paliers.length ? corps.paliers : PALIERS_PAR_DEFAUT;
  compteurRegles += 1;
  const bascule = {
    id: `ro-demo-${suffixeCompteur(compteurRegles)}`,
    tenant_id: reco.tenantId,
    recommandation_id: reco.id,
    application_id: reco.applicationId,
    modele_cible: reco.targetModel,
    regle_passerelle_id: `regle-demo-${compteurRegles}`,
    palier_actuel_pourcentage: paliers[0],
    qualite_en_ligne_mesuree: (reco.quality || {}).equivalenceRate ?? 1,
    seuil_alerte: (reco.quality || {}).confidenceIntervalLow ?? 0.9,
    actif: true,
    annule_automatiquement: false,
    motif_annulation: null,
    _paliers: paliers,
  };
  surcharges.set(`bascule:${bascule.id}`, bascule);
  surcharges.set(`reco:${recoId}`, { ...reco, status: "ROLLING_OUT" });
  return {
    ok: true,
    statut: 201,
    corps: { mode: "passerelle", bascule: sansInterne(bascule), message: "Staged rollout started (static demo)." },
  };
}

/** Identifiant court et stable pour une bascule locale. */
function suffixeCompteur(n) {
  return String(n).padStart(4, "0");
}

/** Retire les champs internes (paliers mémorisés) avant de rendre une bascule. */
function sansInterne(bascule) {
  const copie = { ...bascule };
  delete copie._paliers;
  return copie;
}

/** `POST …/advance` : palier suivant, ou clôture à 100 %. */
function avancerLocalement(basculeId) {
  const bascule = surcharges.get(`bascule:${basculeId}`);
  if (!bascule) return erreur(404, "not_found", `Rollout ${basculeId} introuvable en mode statique.`);
  if (!bascule.actif) return erreur(409, "invalid_state_transition", "This rollout is already closed.");
  const paliers = bascule._paliers || PALIERS_PAR_DEFAUT;
  const index = paliers.indexOf(bascule.palier_actuel_pourcentage);
  const suivant = index >= 0 ? paliers[index + 1] : undefined;
  const maj = suivant === undefined
    ? { ...bascule, actif: false }
    : { ...bascule, palier_actuel_pourcentage: suivant };
  surcharges.set(`bascule:${basculeId}`, maj);
  return { ok: true, statut: 200, corps: sansInterne(maj) };
}

/** `POST …/rollback` : règle retirée, recommandation annulée. */
function annulerLocalement(basculeId, session) {
  const bascule = surcharges.get(`bascule:${basculeId}`) || basculeInstantane(basculeId);
  if (!bascule) return erreur(404, "not_found", `Rollout ${basculeId} introuvable.`);
  if (!bascule.actif) return erreur(409, "invalid_state_transition", "This rollout is already stopped.");
  const maj = {
    ...bascule,
    actif: false,
    annule_automatiquement: false,
    motif_annulation: `Manual rollback requested by ${(session && session.utilisateur) || "demo"}`,
  };
  surcharges.set(`bascule:${basculeId}`, maj);
  if (maj.recommandation_id) {
    const reco = trouverRecommandation(maj.recommandation_id);
    if (reco) surcharges.set(`reco:${reco.id}`, { ...reco, status: "ROLLED_BACK" });
  }
  return { ok: true, statut: 200, corps: sansInterne(maj) };
}

/** Bascule présente dans l'instantané (celles amorcées par la démo). */
function basculeInstantane(basculeId) {
  const brut = instantane.reponses[`GET /v1/rollouts/${encodeURIComponent(basculeId)}`];
  if (!brut || brut._statut) return null;
  return JSON.parse(JSON.stringify(brut));
}

/** Réponse d'erreur au format de l'API (`{"error": {code, message, details}}`). */
function erreur(statut, code, message, details = null) {
  return { ok: false, statut, corps: { error: { code, message, details } } };
}
