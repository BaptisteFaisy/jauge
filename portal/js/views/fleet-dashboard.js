/**
 * Tableau de bord du parc — EF-RAP-02.
 *
 * Tous les clients visibles par l'appelant, l'économie attendue et réalisée,
 * les alertes de rapprochement. Le portail n'additionne rien : chaque valeur
 * affichée est celle que rend l'API (§15).
 */

import { html, carte, tableau, etiquette, vide, fil, sur } from "../dom.js";
import { usd, pourcent, jour, libelle, texteOuTiret } from "../format.js";
import { ROLES_MSP } from "../config.js";

/** Dernier jour de rapprochement d'un client (le plus récent). */
function dernierJour(rapprochement) {
  if (!Array.isArray(rapprochement) || rapprochement.length === 0) {
    return null;
  }
  return rapprochement
    .slice()
    .sort((a, b) => String(b.jour).localeCompare(String(a.jour)))
    .at(0);
}

/** Charge économies et rapprochement de chaque client du parc. */
async function chargerParc(api, clients) {
  const resultats = await Promise.allSettled(
    clients.map(async (client) => {
      const [economies, rapprochement] = await Promise.allSettled([
        api.economies(client.id),
        api.rapprochement(client.id),
      ]);
      return {
        client,
        economies: economies.status === "fulfilled" ? economies.value : null,
        rapprochement: rapprochement.status === "fulfilled" ? rapprochement.value || [] : [],
      };
    }),
  );
  return resultats
    .filter((resultat) => resultat.status === "fulfilled")
    .map((resultat) => resultat.value);
}

/** Ligne de tableau pour un client. */
function ligneClient(entree) {
  const economie = entree.economies;
  const veille = dernierJour(entree.rapprochement);
  const lien = html`<button type="button" class="bouton bouton-secondaire" data-client="${entree.client.id}">
    ${entree.client.nom || libelle(entree.client.id)}
  </button>`;
  const statut = veille
    ? etiquette(
        veille.conforme ? "within tolerance" : "outside tolerance",
        veille.conforme ? "ok" : "alerte",
      )
    : etiquette("no data", "neutre");
  return [
    lien,
    texteOuTiret(entree.client.formule),
    html`<span class="nombre">${economie ? usd(economie.economie_attendue_usd) : "—"}</span>`,
    html`<span class="nombre">${economie ? usd(economie.economie_constatee_usd) : "—"}</span>`,
    html`<span class="nombre">${economie ? pourcent(economie.ecart_pourcentage) : "—"}</span>`,
    veille ? jour(veille.jour) : "—",
    html`<span class="nombre">${veille ? usd(veille.cout_facture_usd) : "—"}</span>`,
    statut,
  ];
}

/** Cartes d'alerte : clients hors tolérance de rapprochement. */
function cartesAlertes(parc) {
  const enAlerte = parc.filter((entree) => {
    const veille = dernierJour(entree.rapprochement);
    return veille !== null && !veille.conforme;
  });
  if (enAlerte.length === 0) {
    return carte("Alerts", html`<p>${etiquette("no client outside tolerance", "ok")}</p>`);
  }
  const elements = enAlerte.map((entree) => {
    const veille = dernierJour(entree.rapprochement);
    const causes = (veille.causes_non_attribuees || []).join(", ");
    return html`<li>
      <strong>${entree.client.nom || libelle(entree.client.id)}</strong> — gap
      ${pourcent(veille.ecart_pourcentage)} on ${jour(veille.jour)}, unattributed
      ${pourcent(veille.part_non_attribuee_pourcentage)}${causes ? html` (causes: ${causes})` : sur("")}
    </li>`;
  });
  return carte("Alerts", html`<ul class="liste-simple">${elements}</ul>`);
}

/** Note d'honnêteté : aucun total de parc n'est calculé côté navigateur. */
function notePerimetre() {
  return carte(
    "Scope of this view",
    html`<p>
      Each line is a figure returned by the control-plane API. The park total is not summed in
      the browser: a park-level aggregate is expected from the control plane (see the pull
      request notes).
    </p>`,
  );
}

/** Vue complète du parc MSP. */
export async function rendre(ctx) {
  const clients = await ctx.api.clients();
  const parc = await chargerParc(ctx.api, Array.isArray(clients) ? clients : []);
  const estMsp = ROLES_MSP.includes(ctx.session.role);
  const entete = html`${fil([{ libelle: "Fleet dashboard", href: "#/fleet" }])}
    <h1 class="titre-page">Managed fleet</h1>
    <p class="sous-titre">
      ${parc.length} client(s) visible with role ${libelle(ctx.session.role)}${estMsp
        ? sur(" · MSP-wide view")
        : sur(" · scoped to your attached clients")}
    </p>`;
  if (parc.length === 0) {
    return html`${entete}${carte("Clients", vide("No client is visible for this role."))}`;
  }
  const corps = tableau({
    legende: "One line per client; latest reconciled day shown for spend.",
    entetes: [
      "Client",
      "Formula",
      "Savings expected",
      "Savings realised",
      "Gap",
      "Latest day",
      "Billed (latest day)",
      "Reconciliation",
    ],
    lignes: parc.map(ligneClient),
  });
  return html`${entete}${carte("Clients", corps)}${cartesAlertes(parc)}${notePerimetre()}`;
}
