/**
 * Amorçage du portail Jauge.
 *
 * SPA statique sans étape de build : modules ES natifs, aucune dépendance, aucun
 * appel réseau hors de l'API du plan de contrôle servie sur la même origine.
 * Le compte de démonstration (EF-POR-02) est porté par la session locale et
 * envoyé en en-têtes `X-Jauge-*` à chaque appel.
 */

import { creerApi, modePortail } from "./api.js";
import { metaInstantane } from "./statique.js";
import { analyser, demarrer, naviguer } from "./router.js";
import { choisir, choisirTous, html, monter, sur, ecrireTexte } from "./dom.js";
import { CLIENTS_DEMO, ROLES_DEMO, SESSION_DEFAUT } from "./config.js";
import * as vueClient from "./views/client-dashboard.js";
import * as vueParc from "./views/fleet-dashboard.js";
import * as vueApplication from "./views/application.js";
import * as vueCampagne from "./views/campaign.js";
import * as vueRecommandation from "./views/recommendation.js";
import * as vueBascule from "./views/rollout.js";
import * as vueRelecture from "./views/review.js";
import * as vueAPropos from "./views/about.js";

const VUES = {
  client: vueClient,
  fleet: vueParc,
  application: vueApplication,
  campagne: vueCampagne,
  recommandation: vueRecommandation,
  bascule: vueBascule,
  relecture: vueRelecture,
  about: vueAPropos,
};

const ONGLET_PAR_VUE = {
  client: "#/client",
  fleet: "#/fleet",
  about: "#/about",
};

const zoneVue = choisir(document, "#vue");
const zoneBandeau = choisir(document, "#bandeau");
const formulaireDemo = choisir(document, "#demo");
const noteDemo = choisir(document, "#demo-note");
const CLE_SESSION = "jauge.demo";

let session = lireSession();
let jeton = 0;
/** Suffixe ajouté à la note d'en-tête quand le portail tourne sur l'instantané statique. */
let suffixeNote = "";

/** Session de démonstration : stockage de session, repli sur les valeurs par défaut. */
function lireSession() {
  try {
    const brut = window.sessionStorage.getItem(CLE_SESSION);
    if (brut) {
      const charge = JSON.parse(brut);
      if (charge && charge.clientId && charge.role) {
        return { ...SESSION_DEFAUT, ...charge };
      }
    }
  } catch (erreur) {
    // Stockage indisponible : la session reste en mémoire.
  }
  return { ...SESSION_DEFAUT };
}

/** Mémorise la session de démonstration courante. */
function memoriserSession() {
  try {
    window.sessionStorage.setItem(CLE_SESSION, JSON.stringify(session));
  } catch (erreur) {
    // Stockage indisponible : sans conséquence sur la navigation.
  }
}

/** Affiche un bandeau (succès, attention, erreur) au-dessus de la vue. */
function bandeau({ genre = "neutre", titre = "", message = "" }) {
  if (!zoneBandeau) {
    return;
  }
  const classes = {
    succes: "bandeau-succes",
    erreur: "bandeau-erreur",
    attention: "bandeau-attention",
  };
  zoneBandeau.className = `bandeau ${classes[genre] || ""}`;
  monter(
    zoneBandeau,
    html`${titre ? html`<h3>${titre}</h3>` : sur("")}<p>${message}</p>`,
  );
  zoneBandeau.hidden = false;
}

/** Masque le bandeau courant. */
function masquerBandeau() {
  if (zoneBandeau) {
    zoneBandeau.hidden = true;
    zoneBandeau.className = "bandeau";
  }
}

/** Remplit les sélecteurs de client et de rôle du compte de démonstration. */
function remplirSelecteurs() {
  const client = choisir(document, "#demo-client");
  const role = choisir(document, "#demo-role");
  if (client) {
    monter(
      client,
      CLIENTS_DEMO.map(
        (entree) => html`<option value="${entree.id}">${entree.libelle}</option>`,
      ),
    );
    client.value = session.clientId;
  }
  if (role) {
    monter(
      role,
      ROLES_DEMO.map((entree) => html`<option value="${entree.id}">${entree.libelle}</option>`),
    );
    role.value = session.role;
  }
  if (noteDemo) {
    ecrireTexte(
      noteDemo,
      `Signed in as ${session.utilisateur} · client ${session.clientId}${suffixeNote}`,
    );
  }
}

/**
 * Signale le mode statique (GitHub Pages : portail publié sans backend).
 *
 * Les juges doivent savoir d'où viennent les chiffres : un instantané généré par
 * `scripts/export_portail_statique.py` depuis la véritable API, et des écritures
 * simulées dans le navigateur uniquement.
 */
async function annoncerMode() {
  const mode = await modePortail();
  if (mode !== "statique") {
    return;
  }
  const meta = metaInstantane() || {};
  const date = String(meta.genere_le || "").slice(0, 10) || "unknown date";
  suffixeNote = ` · static demo (snapshot ${date}, changes stay in your browser)`;
  remplirSelecteurs();
}

/** Marque l'onglet actif pour les lecteurs d'écran. */
function marquerOnglet(route) {
  const cible = ONGLET_PAR_VUE[route.vue] || "#/client";
  choisirTous(document, ".onglet").forEach((onglet) => {
    if (onglet.getAttribute("href") === cible) {
      onglet.setAttribute("aria-current", "page");
    } else {
      onglet.removeAttribute("aria-current");
    }
  });
}

/** Vue d'erreur : message lisible et bouton de reprise. */
function vueErreur(erreur, route) {
  const statut = erreur && erreur.statut ? ` (HTTP ${erreur.statut})` : "";
  const code = erreur && erreur.code ? ` · ${erreur.code}` : "";
  return html`<section class="carte">
    <h2>Something went wrong</h2>
    <p>
      The portal could not load this view${statut}${code}. The control-plane API answered:
      ${erreur && erreur.message ? erreur.message : "unknown error"}.
    </p>
    <p class="demo-note">
      Check that the API is running and that the demo client/role pair is allowed to read this
      data. Path: ${route.chemin}
    </p>
    <div class="actions">
      <button class="bouton" type="button" id="reessayer">Try again</button>
      <a class="bouton bouton-secondaire" href="#/about">Demo accounts</a>
    </div>
  </section>`;
}

/** Construit le contexte passé à chaque vue. */
function construireContexte(route) {
  return {
    session,
    parametres: route.parametres,
    chemin: route.chemin,
    api: creerApi(session),
    actions: {
      bandeau,
      naviguer,
      recharger: () => afficher(route),
    },
  };
}

/** Rend la vue demandée, avec état de chargement et gestion d'erreur. */
async function afficher(route) {
  const monJeton = (jeton += 1);
  masquerBandeau();
  marquerOnglet(route);
  monter(zoneVue, html`<p class="chargement" role="status">Loading…</p>`);
  const vue = VUES[route.vue] || VUES.client;
  const contexte = construireContexte(route);
  try {
    const contenu = await vue.rendre(contexte);
    if (monJeton !== jeton) {
      return;
    }
    monter(zoneVue, contenu);
    if (typeof vue.monter === "function") {
      vue.monter(contexte, zoneVue);
    }
  } catch (erreur) {
    if (monJeton !== jeton) {
      return;
    }
    monter(zoneVue, vueErreur(erreur, route));
    const bouton = choisir(zoneVue, "#reessayer");
    if (bouton) {
      bouton.addEventListener("click", () => afficher(route));
    }
  }
}

/** Applique la sélection du compte de démonstration. */
function appliquerDemo(evenement) {
  evenement.preventDefault();
  const client = choisir(document, "#demo-client");
  const role = choisir(document, "#demo-role");
  if (client) {
    session.clientId = client.value;
  }
  if (role) {
    session.role = role.value;
  }
  memoriserSession();
  remplirSelecteurs();
  afficher(analyser(window.location.hash));
}

/** Bascule sur un client choisi depuis le tableau de bord du parc. */
function basculerClient(evenement) {
  const cible = evenement.target;
  if (!cible || typeof cible.closest !== "function") {
    return;
  }
  const bouton = cible.closest("[data-client]");
  if (!bouton) {
    return;
  }
  session.clientId = bouton.dataset.client;
  memoriserSession();
  remplirSelecteurs();
  naviguer("#/client");
}

/** Point d'entrée. */
function initialiser() {
  remplirSelecteurs();
  if (formulaireDemo) {
    formulaireDemo.addEventListener("submit", appliquerDemo);
  }
  document.addEventListener("click", basculerClient);
  annoncerMode();
  demarrer(afficher);
}

initialiser();
