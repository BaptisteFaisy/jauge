/**
 * Recommandation et décision — EF-REC-02, Annexe B.
 *
 * Affiche les blocs qualité / coût / latence, les risques et le plan de
 * bascule, puis propose trois décisions humaines : approuver, refuser ou
 * demander une campagne plus large. Le bandeau « human approval required »
 * rappelle que la décision n'est jamais prise par un agent (ENF-IA-02).
 */

import { html, carte, mesure, tableau, etiquette, vide, fil, choisir } from "../dom.js";
import { usd, entier, ratio, millisecondes, libelle, listeTextes, texteOuTiret } from "../format.js";
import { ROLES_DECISION, TAILLE_CAMPAGNE_ELARGIE, PALIERS_PROPOSES } from "../config.js";

/** Récupère une recommandation dans la liste de son application (§15). */
async function chargerRecommandation(api, appId, recoId) {
  const liste = await api.recommandations(appId);
  const trouvee = (Array.isArray(liste) ? liste : []).find((reco) => reco.id === recoId);
  if (!trouvee) {
    throw new Error(`Recommendation ${recoId} not found for application ${appId}.`);
  }
  return trouvee;
}

/** Bandeau d'état de la décision. */
function bandeauStatut(reco) {
  const enAttente = reco.status === "PENDING_APPROVAL";
  if (enAttente) {
    return html`<div class="bandeau bandeau-attention" role="status">
      <h3>Human approval required</h3>
      <p>
        This recommendation was drafted by an agent and cannot be applied without the explicit
        approval of the application owner.
      </p>
    </div>`;
  }
  return html`<div class="bandeau bandeau-succes" role="status">
    <h3>Decision recorded</h3>
    <p>
      Status ${libelle(reco.status)}${reco.approvedBy ? ` by ${reco.approvedBy}` : ""}.
      ${reco.approvalNotes ? `Notes: ${reco.approvalNotes}` : ""}
    </p>
  </div>`;
}

/** Blocs Annexe B : qualité, coût, latence. */
function cartesMetriques(reco) {
  const qualite = reco.quality || {};
  const cout = reco.cost || {};
  const latence = reco.latency || {};
  return html`<div class="grille">
    ${mesure("Equivalence rate", ratio(qualite.equivalenceRate), "judge verdicts")}
    ${mesure(
      "95 % interval",
      `${ratio(qualite.confidenceIntervalLow)} – ${ratio(qualite.confidenceIntervalHigh)}`,
      "Wilson interval",
    )}
    ${mesure("Self-agreement", ratio(qualite.selfAgreementOfCurrentModel), "current model")}
    ${mesure("Judge / human", qualite.judgeHumanAgreement === null ? "—" : ratio(qualite.judgeHumanAgreement), "human review sample")}
    ${mesure("Cost per request before", usd(cout.costPerRequestBefore), "USD")}
    ${mesure("Cost per request after", usd(cout.costPerRequestAfter), "USD")}
    ${mesure("Expected monthly saving", usd(cout.expectedMonthlySaving), cout.currency || "USD")}
    ${mesure("p95 before → after", `${millisecondes(latence.p95Before)} → ${millisecondes(latence.p95After)}`, "latency")}
  </div>`;
}

/** Tableau de synthèse du changement proposé. */
function tableauChangement(reco) {
  return carte(
    "Proposed change",
    tableau({
      entetes: ["Field", "Value"],
      lignes: [
        ["Application", texteOuTiret(reco.applicationId)],
        ["Current model", texteOuTiret(reco.currentModel)],
        ["Target model", texteOuTiret(reco.targetModel)],
        ["Campaign", html`<a href="#/campaign/${reco.campaignId}">${reco.campaignId}</a>`],
        ["Sample size", entier(reco.sampleSize)],
        ["Monthly requests", entier((reco.cost || {}).monthlyRequests)],
        ["Deterministic failures", entier((reco.quality || {}).deterministicFailures)],
        ["Rollout steps", listeTextes((reco.rolloutPlan || []).map((palier) => `${palier} %`))],
      ],
    }),
  );
}

/** Risques identifiés par la campagne et le juge. */
function carteRisques(reco) {
  const risques = reco.risks || [];
  if (risques.length === 0) {
    return carte("Risks", vide("No risk reported by the campaign."));
  }
  return carte(
    "Risks",
    html`<ul class="liste-simple">
      ${risques.map((risque) => html`<li>${risque}</li>`)}
    </ul>`,
  );
}

/** Formulaire de décision : trois issues, un motif facultatif. */
function formulaireDecision(reco, autorise) {
  const enAttente = reco.status === "PENDING_APPROVAL";
  const desactive = enAttente && autorise ? "" : "disabled";
  const explication = autorise
    ? "Approving authorises the staged gateway rollout described above."
    : "Your role cannot decide on a recommendation: switch to Application owner or Platform administrator in the demo selector.";
  return carte(
    "Decision",
    html`<p>${explication}</p>
    <form id="decision-form" novalidate>
      <label for="decision-notes">Notes (optional, recorded in the audit log)</label>
      <textarea id="decision-notes" name="notes" rows="3"></textarea>
      <div class="actions">
        <button class="bouton" type="submit" name="decision" value="approuver" ${desactive}>
          Approve
        </button>
        <button class="bouton bouton-danger" type="submit" name="decision" value="refuser" ${desactive}>
          Reject
        </button>
        <button class="bouton bouton-secondaire" type="submit" name="decision" value="campagne_elargie" ${desactive}>
          Request a wider campaign
        </button>
      </div>
    </form>`,
  );
}

/** Bouton de bascule, visible seulement après approbation. */
function carteBascule(reco, autorise) {
  if (reco.status !== "APPROVED") {
    return carte(
      "Rollout",
      vide("The staged rollout becomes available once the recommendation is approved."),
    );
  }
  const desactive = autorise ? "" : "disabled";
  return carte(
    "Rollout",
    html`<p>
      Steps ${listeTextes(PALIERS_PROPOSES.map((palier) => `${palier} %`))} of traffic to
      ${texteOuTiret(reco.targetModel)}, with online re-judging at every step.
    </p>
    <div class="actions">
      <button class="bouton" type="button" id="demarrer-bascule" ${desactive}>
        Start staged rollout
      </button>
    </div>`,
  );
}

/** Vue complète de la recommandation. */
export async function rendre(ctx) {
  const reco = await chargerRecommandation(ctx.api, ctx.parametres.appId, ctx.parametres.recoId);
  ctx.recommandation = reco;
  const autorise = ROLES_DECISION.includes(ctx.session.role);
  const entete = html`${fil([
    { libelle: "Client dashboard", href: "#/client" },
    { libelle: `Application ${libelle(reco.applicationId)}`, href: `#/app/${reco.applicationId}` },
    { libelle: `Recommendation ${reco.id}`, href: `#/app/${reco.applicationId}/recommendation/${reco.id}` },
  ])}
  <h1 class="titre-page">Recommendation ${reco.id}</h1>
  <p class="sous-titre">
    ${texteOuTiret(reco.currentModel)} → ${texteOuTiret(reco.targetModel)} ·
    ${etiquette(libelle(reco.status), reco.status === "APPROVED" ? "ok" : "attente")}
  </p>`;
  return html`${entete}${bandeauStatut(reco)}${cartesMetriques(reco)}${tableauChangement(reco)}
  ${carteRisques(reco)}${formulaireDecision(reco, autorise)}${carteBascule(reco, autorise)}`;
}

/** Envoie une décision ; en cas de refus d'API, propose la campagne élargie. */
async function envoyerDecision(ctx, decision, notes) {
  try {
    await ctx.api.decider(ctx.parametres.recoId, decision, notes);
    ctx.actions.bandeau({
      genre: "succes",
      titre: "Decision recorded",
      message: `Decision "${decision}" recorded in the audit log.`,
    });
    ctx.actions.recharger();
  } catch (erreur) {
    ctx.actions.bandeau({
      genre: "erreur",
      titre: "Decision refused by the API",
      message: `${erreur.message} — the control-plane API does not yet accept the decision "${decision}".`,
    });
  }
}

/** Demande une campagne plus large : décision d'abord, création ensuite si besoin. */
async function demanderCampagne(ctx, notes) {
  try {
    await ctx.api.decider(ctx.parametres.recoId, "campagne_elargie", notes);
    ctx.actions.bandeau({
      genre: "succes",
      titre: "Wider campaign requested",
      message: "The recommendation is held until the wider campaign is proven.",
    });
    ctx.actions.recharger();
  } catch (erreur) {
    await lancerCampagneElargie(ctx, notes);
  }
}

/** Repli : crée une nouvelle campagne avec un échantillon plus grand (§15). */
async function lancerCampagneElargie(ctx, notes) {
  const reco = ctx.recommandation || {};
  const candidats = reco.targetModel ? [reco.targetModel] : [];
  try {
    const campagne = await ctx.api.lancerCampagne(ctx.parametres.appId, {
      modeles_candidats: candidats,
      taille_echantillon: TAILLE_CAMPAGNE_ELARGIE,
      graine_aleatoire: 42,
    });
    ctx.actions.bandeau({
      genre: "succes",
      titre: "Wider campaign launched",
      message: `Campaign ${campagne.id} created with a sample of ${TAILLE_CAMPAGNE_ELARGIE} requests.`,
    });
    ctx.actions.recharger();
  } catch (erreur) {
    ctx.actions.bandeau({
      genre: "erreur",
      titre: "Wider campaign refused by the API",
      message: `${erreur.message}${notes ? " (notes kept for the analyst)" : ""}`,
    });
  }
}

/** Branche les actions de la page après montage. */
export function monter(ctx, racine) {
  const formulaire = choisir(racine, "#decision-form");
  const boutonBascule = choisir(racine, "#demarrer-bascule");
  if (formulaire) {
    formulaire.addEventListener("submit", async (evenement) => {
      evenement.preventDefault();
      const decision = evenement.submitter ? evenement.submitter.value : "";
      const notes = choisir(formulaire, "#decision-notes").value;
      if (decision === "campagne_elargie") {
        await demanderCampagne(ctx, notes);
        return;
      }
      await envoyerDecision(ctx, decision, notes);
    });
  }
  if (boutonBascule) {
    boutonBascule.addEventListener("click", () => demarrerBascule(ctx, boutonBascule));
  }
}

/** Lance la bascule par paliers puis ouvre le suivi. */
async function demarrerBascule(ctx, bouton) {
  bouton.disabled = true;
  try {
    const resultat = await ctx.api.basculer(ctx.parametres.recoId, PALIERS_PROPOSES, null);
    if (resultat.bascule && resultat.bascule.id) {
      ctx.actions.naviguer(`#/rollout/${resultat.bascule.id}`);
      return;
    }
    ctx.actions.bandeau({
      genre: "attention",
      titre: "Change by ticket",
      message: resultat.message || "This application does not use the gateway: a ticket is opened.",
    });
  } catch (erreur) {
    ctx.actions.bandeau({
      genre: "erreur",
      titre: "Rollout refused by the API",
      message: erreur.message,
    });
    bouton.disabled = false;
  }
}
