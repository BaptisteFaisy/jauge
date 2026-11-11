/**
 * Configuration du portail : comptes de démonstration et capacités par rôle.
 *
 * EF-POR-02 (compte de démonstration pour les juges) : le sélecteur de client et
 * de rôle de l'en-tête alimente les en-têtes `X-Jauge-Tenant` et `X-Jauge-Role`
 * lus par l'API du plan de contrôle (§15, module M8). Aucun chiffre métier ici :
 * uniquement des identifiants, des libellés anglais et les droits du §6.
 */

/** Clients de démonstration amorcés par le module M12. */
export const CLIENTS_DEMO = [
  { id: "t-demo-a", libelle: "Client A — Demo (3 applications)" },
  { id: "t-demo-b", libelle: "Client B — Demo (1 application)" },
];

/** Rôles du §6, avec la valeur d'en-tête attendue par l'API. */
export const ROLES_DEMO = [
  { id: "analyste_finops", libelle: "FinOps analyst (MSP)" },
  { id: "administrateur_plateforme", libelle: "Platform administrator (MSP)" },
  { id: "responsable_compte", libelle: "Account manager (MSP)" },
  { id: "responsable_application", libelle: "Application owner (client)" },
  { id: "relecteur", libelle: "Reviewer (client)" },
  { id: "directeur", libelle: "Finance / technical director (client)" },
];

/** Sélection pré-remplie : client A, analyste FinOps (EF-POR-02). */
export const SESSION_DEFAUT = {
  clientId: "t-demo-a",
  role: "analyste_finops",
  utilisateur: "judge-demo",
};

/** Rôles du MSP : vue transverse du parc (EF-RAP-02). */
export const ROLES_MSP = ["administrateur_plateforme", "analyste_finops", "responsable_compte"];

/** Rôles autorisés à trancher une recommandation (§8.5, EF-REC-02). */
export const ROLES_DECISION = ["administrateur_plateforme", "responsable_application"];

/** Rôles autorisés à lancer ou annuler une bascule (§8.6). */
export const ROLES_BASCULE = ["administrateur_plateforme", "responsable_application"];

/** Rôles autorisés à relire un échantillon de paires (§13.5, EF-EVA-05). */
export const ROLES_RELECTURE = ["relecteur", "administrateur_plateforme"];

/**
 * Applications de secours, utilisées seulement si l'API n'expose pas la liste
 * des applications d'un client (point d'API absent de la version déployée).
 * Valeur d'affichage seule : aucune donnée métier n'en est déduite.
 */
export const APPLICATIONS_SECOURS = {
  "t-demo-a": ["ticket-classifier", "support-assistant", "doc-summarizer"],
  "t-demo-b": ["kb-search"],
};

/** Paliers de bascule proposés par défaut (Annexe C, EF-BAS-01). */
export const PALIERS_PROPOSES = [10, 50, 100];

/** Taille d'échantillon proposée pour une campagne plus large (§8.5). */
export const TAILLE_CAMPAGNE_ELARGIE = 500;

/** Délai maximal d'un appel d'API, en millisecondes. */
export const DELAI_API_MS = 15000;

/** Décisions acceptées par `POST /v1/recommendations/{id}/decision`. */
export const DECISIONS = {
  approuver: "Approve",
  refuser: "Reject",
  campagne_elargie: "Request a wider campaign",
};
