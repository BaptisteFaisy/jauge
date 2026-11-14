/**
 * Détail d'une application — coût par requête, modèles, historique, leviers.
 *
 * S'appuie sur `GET /v1/applications/{id}`, `.../costs`, `.../levers` et
 * `.../recommendations` (§15). Le coût par requête n'est affiché que lorsqu'un
 * bloc `cost` de recommandation le fournit (Annexe B) : il n'est jamais recalculé.
 */

import { html, carte, mesure, tableau, etiquette, vide, fil, sur } from "../dom.js";
import {
  usd,
  entier,
  ratio,
  jour,
  libelle,
  texteOuTiret,
  millisecondes,
} from "../format.js";

/** Cartes de synthèse de la période. */
function cartesPeriode(couts) {
  if (!couts) {
    return carte("Period", vide("Cost data is not available for this application."));
  }
  const cartes = [
    mesure("Spend in period", usd(couts.cout_total_usd), "USD, recalculated by the API"),
    mesure("Requests in period", entier(couts.requetes_total), "invocations"),
    mesure("Period", `${jour(couts.periode_debut)} → ${jour(couts.periode_fin)}`, "API window"),
  ];
  return html`<div class="grille">${cartes}</div>`;
}

/** Tableau des modèles utilisés sur la période (ventilation fournie par l'API). */
function tableauModeles(couts) {
  const parModele = (couts && couts.par_modele) || {};
  const entrees = Object.entries(parModele);
  if (entrees.length === 0) {
    return carte("Models", vide("No model usage recorded in this period."));
  }
  return carte(
    "Models",
    tableau({
      entetes: ["Model", "Spend (USD)"],
      lignes: entrees.map(([modele, cout]) => [
        modele,
        html`<span class="nombre">${usd(cout)}</span>`,
      ]),
    }),
  );
}

/** Historique quotidien : une ligne par jour et par modèle, tel que rendu par l'API. */
function tableauHistorique(couts) {
  const lignes = ((couts && couts.par_jour) || [])
    .slice()
    .sort((a, b) => String(b.jour).localeCompare(String(a.jour)))
    .slice(0, 40);
  if (lignes.length === 0) {
    return carte("Daily history", vide("No daily aggregate in this period."));
  }
  return carte(
    "Daily history",
    tableau({
      legende: "Most recent days first; every figure is an API aggregate.",
      entetes: ["Day", "Model", "Requests", "Cost (USD)"],
      lignes: lignes.map((ligne) => [
        jour(ligne.jour),
        ligne.modele_id,
        html`<span class="nombre">${entier(ligne.nombre_requetes)}</span>`,
        html`<span class="nombre">${usd(ligne.cout_recalcule_usd)}</span>`,
      ]),
    }),
  );
}

/** Tableau des leviers détectés sur l'application. */
function tableauLeviers(leviers) {
  if (!Array.isArray(leviers) || leviers.length === 0) {
    return carte("Levers", vide("No lever detected for this application."));
  }
  return carte(
    "Levers",
    tableau({
      entetes: ["Lever", "Status", "Est. monthly saving", "Action", "Proof required"],
      lignes: leviers.map((levier) => [
        libelle(levier.type_levier),
        etiquette(libelle(levier.statut), levier.statut === "applique" ? "ok" : "attente"),
        html`<span class="nombre">${usd(levier.economie_mensuelle_estimee_usd)}</span>`,
        texteOuTiret(levier.action_proposee),
        texteOuTiret(levier.preuve_exigee),
      ]),
    }),
  );
}

/** Recommandations de l'application, avec lien vers la page d'approbation. */
function tableauRecommandations(applicationId, recommandations) {
  if (!Array.isArray(recommandations) || recommandations.length === 0) {
    return carte("Recommendations", vide("No recommendation yet for this application."));
  }
  const lignes = recommandations.map((reco) => {
    const lien = html`<a href="#/app/${applicationId}/recommendation/${reco.id}">${reco.id}</a>`;
    return [
      lien,
      `${reco.currentModel} → ${reco.targetModel}`,
      etiquette(libelle(reco.status), reco.status === "APPROVED" ? "ok" : "attente"),
      html`<span class="nombre">${usd(reco.cost.expectedMonthlySaving)}</span>`,
      html`<span class="nombre">${ratio(reco.quality.equivalenceRate)}</span>`,
    ];
  });
  return carte(
    "Recommendations",
    tableau({
      legende: "Open a recommendation to approve, reject or request a wider campaign.",
      entetes: ["Reference", "Model change", "Status", "Expected monthly saving", "Equivalence"],
      lignes,
    }),
  );
}

/** Coût par requête et latences, lus dans la recommandation la plus récente. */
function carteCoutParRequete(recommandations) {
  const liste = Array.isArray(recommandations) ? recommandations : [];
  if (liste.length === 0) {
    return carte(
      "Cost per request",
      vide("Cost per request is reported by a recommendation block (Annex B)."),
    );
  }
  const reco = liste[0];
  return carte(
    `Cost per request — recommendation ${reco.id}`,
    html`<div class="grille">
      ${mesure("Before", usd(reco.cost.costPerRequestBefore), "USD per request")}
      ${mesure("After", usd(reco.cost.costPerRequestAfter), "USD per request")}
      ${mesure("p95 before → after", `${millisecondes(reco.latency.p95Before)} → ${millisecondes(reco.latency.p95After)}`, "latency")}
      ${mesure("Monthly requests", entier(reco.cost.monthlyRequests), "as measured in the campaign")}
    </div>`,
  );
}

/** État de l'observation fantôme (shadow) et du canari, tel que renvoyé par l'API. */
function carteOmbre(ombre) {
  if (!ombre || !ombre.phase || ombre.phase === "aucune") {
    return carte(
      "Ombre et canari",
      vide("Aucune observation fantôme lancée pour cette application."),
    );
  }
  const genrePhase = ombre.phase === "canari" ? "ok" : "attente";
  const blocs = [etiquette(libelle(ombre.phase), genrePhase)];
  const bilan = ombre.bilan;
  if (bilan) {
    blocs.push(
      tableau({
        entetes: ["Métrique", "Valeur"],
        lignes: [
          ["Modèle candidat", texteOuTiret(bilan.modele_candidat)],
          ["Volume observé", entier(bilan.volume_observe)],
          ["Taux d'équivalence", ratio(bilan.taux_equivalence)],
          ["Divergence", bilan.divergence ? "oui" : "non"],
          ["Coût du shadow", usd(bilan.cout_shadow_usd)],
        ],
      }),
    );
  }
  if (ombre.canari) {
    const canari = ombre.canari;
    blocs.push(
      html`<p>
        Canari ouvert sur ${entier(canari.palier_pourcentage)} % du trafic (règle
        ${texteOuTiret(canari.regle_id)}).
      </p>`,
    );
  }
  return carte("Ombre et canari", html`${blocs}`);
}

/** Vue complète d'une application. */
export async function rendre(ctx) {
  const appId = ctx.parametres.appId;
  const application = await ctx.api.application(appId);
  const [couts, leviers, recommandations, ombre] = await Promise.all([
    ctx.api.couts(appId).catch(() => null),
    ctx.api.leviers(appId).catch(() => []),
    ctx.api.recommandations(appId).catch(() => []),
    ctx.api.ombre(appId).catch(() => null),
  ]);
  const entete = html`${fil([
    { libelle: "Client dashboard", href: "#/client" },
    { libelle: application.nom || libelle(appId), href: `#/app/${appId}` },
  ])}
  <h1 class="titre-page">${application.nom || libelle(appId)}</h1>
  <p class="sous-titre">
    Team ${libelle(application.equipe)} · ${texteOuTiret(application.environnement)} · current model
    ${texteOuTiret(application.modele_actuel)} · quality threshold ${ratio(application.seuil_qualite)}
  </p>`;
  const seuils = carte(
    "Quality thresholds",
    html`<p>
      Required equivalence rate ${ratio(application.seuil_qualite)} · lower confidence bound
      ${ratio(application.seuil_intervalle_bas)} · gateway
      ${application.passe_par_passerelle ? "enabled" : "not used (change by ticket)"} ·
      human review ${application.relecture_humaine_autorisee ? "allowed" : "disabled"}
    </p>`,
  );
  return html`${entete}${cartesPeriode(couts)}${carteCoutParRequete(recommandations)}
  ${seuils}${carteOmbre(ombre)}${tableauModeles(couts)}${tableauLeviers(leviers)}
  ${tableauRecommandations(appId, recommandations)}${tableauHistorique(couts)}`;
}
