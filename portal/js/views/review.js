/**
 * Relecture humaine — EF-EVA-05, §13.5.
 *
 * Sous-échantillon de paires rejouées (30 par défaut) à relire par un relecteur
 * du client, vote équivalent / non équivalent, puis accord juge / humain tel que
 * calculé par le plan de données.
 *
 * Les textes affichés proviennent d'un point d'API servi dans le compte du
 * client (§16) : ils ne transitent jamais par le compte du MSP. Ce point
 * (`GET/POST /v1/campaigns/{id}/reviews`) est une extension du §15, signalée
 * dans le corps de la PR.
 */

import { html, carte, mesure, tableau, etiquette, vide, fil, choisir, choisirTous } from "../dom.js";
import { ratio, libelle, texteOuTiret } from "../format.js";
import { ROLES_RELECTURE } from "../config.js";

/** État de chargement de l'échantillon, y compris l'absence du point d'API. */
async function chargerEchantillon(api, campagneId) {
  try {
    const charge = await api.relectures(campagneId);
    return { disponible: true, donnees: charge || null };
  } catch (erreur) {
    return { disponible: false, erreur, donnees: null };
  }
}

/** Bandeau explicatif quand la relecture n'est pas exposée par l'API. */
function bandeauIndisponible(erreur) {
  return html`<div class="bandeau bandeau-attention" role="status">
    <h3>Human review is not available on this deployment</h3>
    <p>
      The portal calls <code>GET /v1/campaigns/{id}/reviews</code> (extension of the control-plane
      API, requirement EF-EVA-05). The server answered: ${texteOuTiret(erreur.message)}.
    </p>
    <p>
      Until that endpoint is deployed, review votes cannot be recorded; the recommendation keeps
      showing the judge/human agreement measured during the campaign.
    </p>
  </div>`;
}

/** Cartes de synthèse de l'échantillon. */
function cartesEchantillon(donnees) {
  return html`<div class="grille">
    ${mesure("Judge / human agreement", ratio(donnees.accord_juge_humain), "API-reported")}
    ${mesure("Pairs in the sample", texteOuTiret(donnees.taille_echantillon), "requests")}
    ${mesure("Votes recorded", texteOuTiret(donnees.votes_enregistres), "human verdicts")}
  </div>`;
}

/** Bloc d'une paire : requête, réponse de référence, réponse candidate. */
function blocPaire(paire) {
  return html`<div class="carte">
    <h2>Request ${texteOuTiret(paire.requete_id)} · candidate ${texteOuTiret(paire.modele_candidat_id)}</h2>
    <div class="paire">
      <div class="paire-bloc">
        <h4>Request</h4>
        <p class="paire-texte">${texteOuTiret(paire.texte_requete)}</p>
      </div>
      <div class="paire-bloc">
        <h4>Reference answer (current model)</h4>
        <p class="paire-texte">${texteOuTiret(paire.reponse_reference)}</p>
      </div>
      <div class="paire-bloc">
        <h4>Candidate answer</h4>
        <p class="paire-texte">${texteOuTiret(paire.reponse_candidate)}</p>
      </div>
      <div class="paire-bloc">
        <h4>Judge verdict</h4>
        <p>${etiquette(ratio(paire.score_equivalence), "neutre")}</p>
        <p class="paire-texte">${texteOuTiret(paire.explication_juge)}</p>
      </div>
    </div>
    <div class="actions">
      <button class="bouton" type="button" data-vote="equivalent"
        data-requete="${paire.requete_id}" data-modele="${paire.modele_candidat_id}">
        Equivalent
      </button>
      <button class="bouton bouton-danger" type="button" data-vote="non_equivalent"
        data-requete="${paire.requete_id}" data-modele="${paire.modele_candidat_id}">
        Not equivalent
      </button>
      <span class="demo-note">
        ${paire.vote_humain ? `Recorded: ${libelle(paire.vote_humain)}` : "Not reviewed yet"}
      </span>
    </div>
  </div>`;
}

/** Tableau récapitulatif des votes déjà enregistrés. */
function tableauVotes(donnees) {
  const paires = Array.isArray(donnees.paires) ? donnees.paires : [];
  const votees = paires.filter((paire) => paire.vote_humain);
  if (votees.length === 0) {
    return carte("Recorded votes", vide("No pair reviewed yet."));
  }
  return carte(
    "Recorded votes",
    tableau({
      entetes: ["Request", "Candidate", "Judge score", "Human verdict"],
      lignes: votees.map((paire) => [
        texteOuTiret(paire.requete_id),
        texteOuTiret(paire.modele_candidat_id),
        ratio(paire.score_equivalence),
        libelle(paire.vote_humain),
      ]),
    }),
  );
}

/** Vue complète de la relecture humaine. */
export async function rendre(ctx) {
  const campagneId = ctx.parametres.campagneId;
  const echantillon = await chargerEchantillon(ctx.api, campagneId);
  const entete = html`${fil([
    { libelle: "Client dashboard", href: "#/client" },
    { libelle: `Campaign ${campagneId}`, href: `#/campaign/${campagneId}` },
    { libelle: "Human review", href: `#/campaign/${campagneId}/review` },
  ])}
  <h1 class="titre-page">Human review sample</h1>
  <p class="sous-titre">
    Campaign ${campagneId} — a reviewer of the client organisation re-reads a sample of replayed
    pairs and checks the judge.
  </p>`;
  if (!echantillon.disponible) {
    return html`${entete}${bandeauIndisponible(echantillon.erreur || {})}`;
  }
  const donnees = echantillon.donnees || {};
  const paires = Array.isArray(donnees.paires) ? donnees.paires : [];
  const autorise = ROLES_RELECTURE.includes(ctx.session.role);
  const note = autorise
    ? vide("Votes are recorded in the client's own account and feed the judge/human agreement.")
    : carte(
        "Read-only",
        html`<p>
          Your role can read the sample but not vote: switch to Reviewer in the demo selector to
          record a verdict.
        </p>`,
      );
  return html`${entete}${cartesEchantillon(donnees)}${note}${tableauVotes(donnees)}
  ${paires.map(blocPaire)}`;
}

/** Branche les votes après montage. */
export function monter(ctx, racine) {
  const autorise = ROLES_RELECTURE.includes(ctx.session.role);
  if (!autorise) {
    return;
  }
  choisirTous(racine, "[data-vote]").forEach((bouton) => {
    bouton.addEventListener("click", async () => {
      bouton.disabled = true;
      try {
        await ctx.api.voter(ctx.parametres.campagneId, {
          requete_id: bouton.dataset.requete,
          modele_candidat_id: bouton.dataset.modele,
          verdict: bouton.dataset.vote,
        });
        ctx.actions.bandeau({
          genre: "succes",
          titre: "Vote recorded",
          message: `Request ${bouton.dataset.requete} marked as ${libelle(bouton.dataset.vote)}.`,
        });
        ctx.actions.recharger();
      } catch (erreur) {
        ctx.actions.bandeau({
          genre: "erreur",
          titre: "Vote refused by the API",
          message: erreur.message,
        });
        bouton.disabled = false;
      }
    });
  });
}
