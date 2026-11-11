/**
 * Tableau de bord client — EF-RAP-01.
 *
 * Dépense par application et par équipe, tendance de la période, leviers
 * détectés avec leur économie estimée, économies réalisées et restantes.
 * Toutes les valeurs affichées proviennent de l'API (§15) : le portail ne
 * calcule aucun agrégat.
 */

import { html, carte, mesure, tableau, etiquette, vide, fil, sur } from "../dom.js";
import { usd, entier, pourcent, jour, libelle, texteOuTiret } from "../format.js";
import { histogramme } from "../chart.js";
import { APPLICATIONS_SECOURS } from "../config.js";

/** Liste des applications d'un client, avec repli sur les identifiants de démo. */
async function applicationsDuClient(api, tenantId) {
  try {
    const liste = await api.applications(tenantId);
    if (Array.isArray(liste) && liste.length > 0) {
      return liste;
    }
  } catch (erreur) {
    // Liste indisponible : on garde les identifiants de démonstration.
  }
  return (APPLICATIONS_SECOURS[tenantId] || []).map((id) => ({
    id,
    nom: libelle(id),
    equipe: "",
  }));
}

/** Charge coûts et leviers de chaque application sans bloquer sur un échec. */
async function chargerApplications(api, applications) {
  const resultats = await Promise.allSettled(
    applications.map(async (application) => ({
      application,
      couts: await api.couts(application.id),
      leviers: await api.leviers(application.id),
    })),
  );
  return resultats.map((resultat, index) =>
    resultat.status === "fulfilled"
      ? resultat.value
      : { application: applications[index], couts: null, leviers: [] },
  );
}

/** Charge le contexte client : fiche, économies, rapprochement. */
async function chargerClient(api, tenantId) {
  const [client, economies, rapprochement] = await Promise.allSettled([
    api.client(tenantId),
    api.economies(tenantId),
    api.rapprochement(tenantId),
  ]);
  return {
    client: client.status === "fulfilled" ? client.value : null,
    economies: economies.status === "fulfilled" ? economies.value : null,
    rapprochement: rapprochement.status === "fulfilled" ? rapprochement.value || [] : [],
  };
}

/** Cartes « économies réalisées » et « reste à réaliser » (valeurs d'API). */
function cartesEconomies(economies) {
  if (!economies) {
    return carte("Savings", vide("Savings are not available for this client."));
  }
  const cartes = [
    mesure("Savings realised", usd(economies.economie_constatee_usd), "USD, measured at 30 days"),
    mesure("Savings expected", usd(economies.economie_attendue_usd), "USD, from approved changes"),
    mesure("Gap to target", pourcent(economies.ecart_pourcentage), "reported by the API"),
  ];
  return html`<div class="grille">${cartes}</div>`;
}

/** Tendance de la période, à partir du rapprochement quotidien du client. */
function carteTendance(rapprochement) {
  if (!Array.isArray(rapprochement) || rapprochement.length === 0) {
    return carte("Spend trend", vide("No reconciliation data for this period."));
  }
  const points = rapprochement
    .slice()
    .sort((a, b) => String(a.jour).localeCompare(String(b.jour)))
    .map((ligne) => ({ jour: ligne.jour, cout: ligne.cout_facture_usd }));
  return carte("Spend trend (billed cost per day)", histogramme(points));
}

/** Tableau « dépense par application » : une ligne par application. */
function tableauApplications(lignes) {
  const corps = lignes.map((ligne) => {
    const couts = ligne.couts;
    const lien = html`<a href="#/app/${sur(encodeURIComponent(ligne.application.id))}"
      >${ligne.application.nom || libelle(ligne.application.id)}</a
    >`;
    if (!couts) {
      return [lien, sur("—"), sur("—")];
    }
    return [
      lien,
      html`<span class="nombre">${usd(couts.cout_total_usd)}</span>`,
      html`<span class="nombre">${entier(couts.requetes_total)}</span>`,
    ];
  });
  return carte(
    "Spend by application",
    tableau({
      legende: "Period totals reported by the API.",
      entetes: ["Application", "Spend (USD)", "Requests"],
      lignes: corps,
    }),
  );
}

/** Dépense regroupée par équipe : aucune somme locale, une ligne par application. */
function tableauEquipes(lignes) {
  const parEquipe = new Map();
  lignes.forEach((ligne) => {
    const equipe = ligne.application.equipe || "unassigned";
    if (!parEquipe.has(equipe)) {
      parEquipe.set(equipe, []);
    }
    parEquipe.get(equipe).push(ligne);
  });
  const blocs = Array.from(parEquipe.entries()).map(([equipe, membres]) =>
    carte(
      `Team — ${libelle(equipe)}`,
      tableau({
        entetes: ["Application", "Spend (USD)"],
        lignes: membres.map((membre) => [
          membre.application.nom || libelle(membre.application.id),
          html`<span class="nombre">${membre.couts ? usd(membre.couts.cout_total_usd) : "—"}</span>`,
        ]),
      }),
    ),
  );
  return blocs.length > 0 ? html`<div class="grille">${blocs}</div>` : sur("");
}

/** Leviers détectés, avec l'économie mensuelle estimée par l'API. */
function tableauLeviers(lignes) {
  const leviers = [];
  lignes.forEach((ligne) => {
    (ligne.leviers || []).forEach((levier) =>
      leviers.push({ levier, application: ligne.application }),
    );
  });
  if (leviers.length === 0) {
    return carte("Detected levers", vide("No lever detected on this client for the period."));
  }
  const corps = leviers.map(({ levier, application }) => [
    application.nom || libelle(application.id),
    libelle(levier.type_levier),
    etiquette(libelle(levier.statut), levier.statut === "applique" ? "ok" : "attente"),
    html`<span class="nombre">${usd(levier.economie_mensuelle_estimee_usd)}</span>`,
    texteOuTiret(levier.action_proposee),
  ]);
  return carte(
    "Detected levers",
    tableau({
      legende: "Monthly saving estimated by the control plane, before replay proof.",
      entetes: ["Application", "Lever", "Status", "Est. monthly saving", "Proposed action"],
      lignes: corps,
    }),
  );
}

/** Bandeau de rapprochement : dernier jour connu et jours non conformes. */
function carteRapprochement(rapprochement) {
  if (!Array.isArray(rapprochement) || rapprochement.length === 0) {
    return carte("Invoice reconciliation", vide("No reconciliation available."));
  }
  const tries = rapprochement
    .slice()
    .sort((a, b) => String(b.jour).localeCompare(String(a.jour)));
  const dernier = tries[0];
  const entete = html`<p>
    Latest day ${jour(dernier.jour)}: billed ${usd(dernier.cout_facture_usd)}, recalculated
    ${usd(dernier.cout_recalcule_usd)}, gap ${pourcent(dernier.ecart_pourcentage)}, unattributed
    ${pourcent(dernier.part_non_attribuee_pourcentage)}.
    ${etiquette(
      dernier.conforme ? "within tolerance" : "outside tolerance",
      dernier.conforme ? "ok" : "alerte",
    )}
  </p>`;
  const ecarts = tries.filter((ligne) => !ligne.conforme).slice(0, 5);
  const detail =
    ecarts.length === 0
      ? vide("No day outside tolerance in this period.")
      : tableau({
          legende: "Most recent days outside the 3 % tolerance.",
          entetes: ["Day", "Gap", "Unattributed", "Causes"],
          lignes: ecarts.map((ligne) => [
            jour(ligne.jour),
            html`<span class="nombre">${pourcent(ligne.ecart_pourcentage)}</span>`,
            html`<span class="nombre">${pourcent(ligne.part_non_attribuee_pourcentage)}</span>`,
            (ligne.causes_non_attribuees || []).join(", ") || "—",
          ]),
        });
  return carte("Invoice reconciliation", html`${entete}${detail}`);
}

/** Vue complète du tableau de bord client. */
export async function rendre(ctx) {
  const tenantId = ctx.session.clientId;
  const [contexte, applications] = await Promise.all([
    chargerClient(ctx.api, tenantId),
    applicationsDuClient(ctx.api, tenantId),
  ]);
  const lignes = await chargerApplications(ctx.api, applications);
  const nomClient = contexte.client ? contexte.client.nom : tenantId;
  const entete = html`${fil([{ libelle: "Client dashboard", href: "#/client" }])}
    <h1 class="titre-page">${nomClient}</h1>
    <p class="sous-titre">
      Period reported by the API · formula ${texteOuTiret(contexte.client && contexte.client.formule)}
    </p>`;
  return html`${entete} ${cartesEconomies(contexte.economies)}
  ${carteTendance(contexte.rapprochement)} ${tableauApplications(lignes)}
  ${tableauEquipes(lignes)} ${tableauLeviers(lignes)}
  ${carteRapprochement(contexte.rapprochement)}`;
}
