/**
 * Campagne de rejeu — résultats par candidat (Annexe B).
 *
 * Taux d'équivalence, intervalle de confiance, latences et coût par requête de
 * chaque candidat, familles de requêtes en échec, statut de la campagne.
 * Les scores sont agrégés côté plan de contrôle : aucun texte de requête ni de
 * réponse n'est exposé ici (ENF-CONF-01).
 */

import { html, carte, mesure, tableau, etiquette, vide, fil, sur } from "../dom.js";
import { usd, entier, ratio, millisecondes, libelle, listeTextes, texteOuTiret } from "../format.js";

/** Carte d'en-tête : état, échantillon, juge, coût. */
function carteCampagne(campagne) {
  const cartes = [
    mesure("Status", libelle(campagne.statut), "campaign lifecycle"),
    mesure("Sample size", entier(campagne.taille_echantillon), "requests replayed"),
    mesure("Estimated cost", usd(campagne.cout_estime_usd), "USD, before replay"),
    mesure("Actual cost", usd(campagne.cout_reel_usd), "USD, after replay"),
  ];
  return html`<div class="grille">${cartes}</div>`;
}

/** Paramètres de rejeu reproduits à l'identique (§13.2). */
function carteParametres(campagne) {
  return carte(
    "Replay parameters",
    html`<p>
      Judge model ${texteOuTiret(campagne.modele_juge_id)} · candidate models
      ${listeTextes(campagne.modeles_candidats)} · seed ${entier(campagne.graine_aleatoire)} ·
      sample ${entier(campagne.taille_echantillon)} requests drawn from the client's own logs.
    </p>
    <p>
      Only aggregated scores are returned by the control plane; request and response text stays in
      the client account.
    </p>`,
  );
}

/** Protocole pré-enregistré : empreintes, version, marge et seuils (§12). */
function carteProtocole(protocole) {
  const empreinte = (protocole && protocole.protocole_empreinte) || null;
  if (!empreinte) {
    return carte(
      "Protocole pré-enregistré",
      vide("Aucun protocole pré-enregistré pour cette campagne."),
    );
  }
  const seuils = (protocole && protocole.seuils) || {};
  const lignes = [
    ["Empreinte du protocole", texteOuTiret(protocole.protocole_empreinte)],
    ["Empreinte de l'échantillon", texteOuTiret(protocole.echantillon_empreinte)],
  ];
  if (protocole.protocole_version) {
    lignes.push(["Version", texteOuTiret(protocole.protocole_version)]);
  }
  if (protocole.marge_non_inferiorite !== null && protocole.marge_non_inferiorite !== undefined) {
    lignes.push(["Marge de non-infériorité", ratio(protocole.marge_non_inferiorite)]);
  }
  if (Object.prototype.hasOwnProperty.call(seuils, "seuil_equivalence")) {
    lignes.push(["Seuil d'équivalence", ratio(seuils.seuil_equivalence)]);
  }
  if (Object.prototype.hasOwnProperty.call(seuils, "borne_basse_intervalle")) {
    lignes.push(["Borne basse de l'intervalle", ratio(seuils.borne_basse_intervalle)]);
  }
  return carte(
    "Protocole pré-enregistré",
    tableau({ entetes: ["Champ", "Valeur"], lignes }),
  );
}

/** Une ligne par candidat, avec l'intervalle de confiance de Wilson. */
function ligneCandidat(resultat) {
  const intervalle = `${ratio(resultat.intervalle_confiance_bas)} – ${ratio(resultat.intervalle_confiance_haut)}`;
  return [
    html`${resultat.modele_id}${resultat.est_reference ? sur(" (reference)") : sur("")}`,
    html`<span class="nombre">${entier(resultat.nombre_requetes_rejouees)}</span>`,
    html`<span class="nombre">${ratio(resultat.taux_equivalence)}</span>`,
    html`<span class="nombre">${intervalle}</span>`,
    html`<span class="nombre">${ratio(resultat.auto_accord_modele_actuel)}</span>`,
    html`<span class="nombre">${resultat.accord_juge_humain === null || resultat.accord_juge_humain === undefined ? "—" : ratio(resultat.accord_juge_humain)}</span>`,
    html`<span class="nombre">${entier(resultat.echecs_deterministes)}</span>`,
    html`<span class="nombre">${usd(resultat.cout_par_requete_usd)}</span>`,
    html`<span class="nombre">${millisecondes(resultat.latence_p50_ms)}</span>`,
    html`<span class="nombre">${millisecondes(resultat.latence_p95_ms)}</span>`,
    etiquette(resultat.conforme_seuils ? "within thresholds" : "below thresholds", resultat.conforme_seuils ? "ok" : "alerte"),
  ];
}

/** Tableau des résultats consolidés par candidat. */
function tableauResultats(resultats) {
  if (!Array.isArray(resultats) || resultats.length === 0) {
    return carte("Results per candidate", vide("No consolidated result yet for this campaign."));
  }
  return carte(
    "Results per candidate",
    tableau({
      legende: "Equivalence, confidence interval, latency and cost per request reported by the API.",
      entetes: [
        "Model",
        "Requests",
        "Equivalence",
        "95 % interval",
        "Self-agreement",
        "Judge/human",
        "Deterministic failures",
        "Cost / request",
        "p50",
        "p95",
        "Thresholds",
      ],
      lignes: resultats.map(ligneCandidat),
    }),
  );
}

/** Familles de requêtes en échec, par candidat. */
function tableauFamilles(resultats) {
  const avecEchecs = (Array.isArray(resultats) ? resultats : []).filter(
    (resultat) => Array.isArray(resultat.familles_en_echec) && resultat.familles_en_echec.length > 0,
  );
  if (avecEchecs.length === 0) {
    return carte("Failing request families", vide("No request family below the quality bar."));
  }
  return carte(
    "Failing request families",
    tableau({
      entetes: ["Model", "Families"],
      lignes: avecEchecs.map((resultat) => [
        resultat.modele_id,
        listeTextes(resultat.familles_en_echec),
      ]),
    }),
  );
}

/** Vue complète d'une campagne. */
export async function rendre(ctx) {
  const campagneId = ctx.parametres.campagneId;
  const campagne = await ctx.api.campagne(campagneId);
  const resultats = await ctx.api.resultats(campagneId).catch(() => []);
  const protocole = await ctx.api.protocoleCampagne(campagneId).catch(() => null);
  const entete = html`${fil([
    { libelle: "Client dashboard", href: "#/client" },
    { libelle: `Application ${libelle(campagne.application_id)}`, href: `#/app/${campagne.application_id}` },
    { libelle: `Campaign ${campagne.id}`, href: `#/campaign/${campagne.id}` },
  ])}
  <h1 class="titre-page">Campaign ${campagne.id}</h1>
  <p class="sous-titre">
    Replay campaign on ${libelle(campagne.application_id)} — quality proven by replay, not by
    opinion.
  </p>`;
  const lienRelecture = html`<p class="actions">
    <a class="bouton bouton-secondaire" href="#/campaign/${campagne.id}/review"
      >Human review sample</a
    >
    <a class="bouton bouton-secondaire" href="#/app/${campagne.application_id}"
      >Application detail</a
    >
  </p>`;
  return html`${entete}${carteCampagne(campagne)}${carteParametres(campagne)}
  ${carteProtocole(protocole)}${tableauResultats(resultats)}${tableauFamilles(resultats)}${carte("Next step", lienRelecture)}`;
}
