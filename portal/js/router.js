/**
 * Routeur minimal sur le fragment d'URL (`#/...`).
 *
 * Un SPA statique sans build n'a pas de serveur pour réécrire les chemins :
 * toute la navigation tient donc dans le fragment, ce qui rend le portail
 * ouvrable directement par l'API du plan de contrôle (aucune configuration).
 */

const ROUTES = [
  { motif: [], vue: "client" },
  { motif: ["client"], vue: "client" },
  { motif: ["fleet"], vue: "fleet" },
  { motif: ["about"], vue: "about" },
  { motif: ["app", ":appId"], vue: "application" },
  { motif: ["app", ":appId", "recommendation", ":recoId"], vue: "recommandation" },
  { motif: ["campaign", ":campagneId"], vue: "campagne" },
  { motif: ["campaign", ":campagneId", "review"], vue: "relecture" },
  { motif: ["rollout", ":basculeId"], vue: "bascule" },
];

/** Découpe un fragment d'URL en segments décodés. */
function segments(hash) {
  const brut = String(hash || "").replace(/^#/, "").replace(/^\/+/, "");
  if (!brut) {
    return [];
  }
  return brut
    .split("/")
    .filter((partie) => partie.length > 0)
    .map((partie) => decodeURIComponent(partie));
}

/**
 * Résout un fragment en { vue, parametres, chemin }.
 * Un fragment inconnu retombe sur le tableau de bord client.
 */
export function analyser(hash) {
  const morceaux = segments(hash);
  for (const route of ROUTES) {
    if (route.motif.length !== morceaux.length) {
      continue;
    }
    const parametres = {};
    const correspond = route.motif.every((attendu, index) => {
      if (attendu.startsWith(":")) {
        parametres[attendu.slice(1)] = morceaux[index];
        return true;
      }
      return attendu === morceaux[index];
    });
    if (correspond) {
      return { vue: route.vue, parametres, chemin: `#/${morceaux.join("/")}` };
    }
  }
  return { vue: "client", parametres: {}, chemin: "#/client" };
}

/** Change de vue en modifiant le fragment (déclenche `hashchange`). */
export function naviguer(hash) {
  const cible = String(hash || "#/client");
  if (window.location.hash === cible) {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    return;
  }
  window.location.hash = cible;
}

/** Abonne le routeur aux changements de fragment et rend la vue courante. */
export function demarrer(rappel) {
  const declencher = () => rappel(analyser(window.location.hash));
  window.addEventListener("hashchange", declencher);
  declencher();
}
