/**
 * Suivi de bascule — §8.6, EF-BAS-01..04.
 *
 * Paliers 10 %, 50 %, 100 %, qualité mesurée en ligne, état de la règle de
 * passerelle et retour arrière. Le portail lit l'état et déclenche l'action :
 * la décision de palier et le rejugement restent dans le plan de données.
 */

import { html, carte, mesure, etiquette, vide, fil, choisir, sur } from "../dom.js";
import { ratio, libelle, texteOuTiret } from "../format.js";
import { ROLES_BASCULE, PALIERS_PROPOSES } from "../config.js";

/** État de la bascule en une étiquette. */
function etatBascule(bascule) {
  if (bascule.annule_automatiquement) {
    return etiquette("rolled back automatically", "alerte");
  }
  if (!bascule.actif) {
    return etiquette("stopped", "neutre");
  }
  return etiquette("in progress", "attente");
}

/** Cartes de synthèse : palier courant, qualité en ligne, seuil. */
function cartesBascule(bascule) {
  return html`<div class="grille">
    ${mesure("Current step", `${bascule.palier_actuel_pourcentage} %`, "of traffic on the target model")}
    ${mesure("Online quality", ratio(bascule.qualite_en_ligne_mesuree), "re-judged live traffic sample")}
    ${mesure("Alert threshold", ratio(bascule.seuil_alerte), "automatic rollback below this value")}
    ${mesure("State", etatBascule(bascule), bascule.motif_annulation || "gateway rule")}
  </div>`;
}

/** Frise des paliers, du plus petit au plus grand. */
function frisePaliers(bascule) {
  const elements = PALIERS_PROPOSES.map((palier) => {
    const atteint = bascule.palier_actuel_pourcentage >= palier;
    return html`<li>
      ${etiquette(`${palier} %`, atteint ? "ok" : "neutre")}
      ${atteint ? html`<span> reached</span>` : html`<span> pending</span>`}
    </li>`;
  });
  return carte(
    "Rollout steps",
    html`<ul class="liste-simple">${elements}</ul>`,
  );
}

/** Détails de la règle de passerelle et de l'application visée. */
function carteRegle(bascule) {
  return carte(
    "Gateway rule",
    html`<p>
      Rule ${texteOuTiret(bascule.regle_passerelle_id)} sends traffic of application
      ${libelle(bascule.application_id)} to ${texteOuTiret(bascule.modele_cible)}. Recommendation
      ${bascule.recommandation_id} is the source of this change.
    </p>
    <p>
      At every step a sample of real traffic is re-judged; below the alert threshold the rule is
      removed automatically and the recommendation is marked as rolled back.
    </p>`,
  );
}

/** Actions : rafraîchir, retour arrière. */
function carteActions(bascule, autorise) {
  const desactive = bascule.actif && autorise ? "" : "disabled";
  const explication = autorise
    ? "A manual rollback restores the previous model immediately."
    : "Your role cannot roll back: switch to Application owner or Platform administrator.";
  return carte(
    "Actions",
    html`<p>${explication}</p>
    <div class="actions">
      <button class="bouton bouton-secondaire" type="button" id="rafraichir-bascule">
        Refresh state
      </button>
      <button class="bouton bouton-danger" type="button" id="annuler-bascule" ${desactive}>
        Roll back now
      </button>
    </div>`,
  );
}

/** Vue complète du suivi de bascule. */
export async function rendre(ctx) {
  const bascule = await ctx.api.bascule(ctx.parametres.basculeId);
  const autorise = ROLES_BASCULE.includes(ctx.session.role);
  const entete = html`${fil([
    { libelle: "Client dashboard", href: "#/client" },
    {
      libelle: `Recommendation ${bascule.recommandation_id}`,
      href: `#/app/${bascule.application_id}/recommendation/${bascule.recommandation_id}`,
    },
    { libelle: "Rollout", href: `#/rollout/${bascule.id}` },
  ])}
  <h1 class="titre-page">Rollout ${bascule.id}</h1>
  <p class="sous-titre">
    Started from recommendation ${bascule.recommandation_id} on
    ${libelle(bascule.application_id)}${bascule.actif ? sur(" · active") : sur(" · stopped")}
  </p>`;
  const detail = bascule.actif
    ? vide("No rollback recorded for this rollout.")
    : carte(
        "Rollback",
        html`<p>
          ${bascule.annule_automatiquement ? "Automatic rollback" : "Manual rollback"} — reason
          ${texteOuTiret(bascule.motif_annulation)}.
        </p>`,
      );
  return html`${entete}${cartesBascule(bascule)}${frisePaliers(bascule)}${carteRegle(bascule)}
  ${carteActions(bascule, autorise)}${detail}`;
}

/** Branche les actions après montage. */
export function monter(ctx, racine) {
  const rafraichir = choisir(racine, "#rafraichir-bascule");
  const annuler = choisir(racine, "#annuler-bascule");
  if (rafraichir) {
    rafraichir.addEventListener("click", () => ctx.actions.recharger());
  }
  if (annuler) {
    annuler.addEventListener("click", async () => {
      annuler.disabled = true;
      try {
        await ctx.api.annulerBascule(ctx.parametres.basculeId);
        ctx.actions.bandeau({
          genre: "attention",
          titre: "Rollback recorded",
          message: "The gateway rule was removed and the recommendation is marked as rolled back.",
        });
        ctx.actions.recharger();
      } catch (erreur) {
        ctx.actions.bandeau({
          genre: "erreur",
          titre: "Rollback refused by the API",
          message: erreur.message,
        });
        annuler.disabled = false;
      }
    });
  }
}
