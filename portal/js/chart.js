/**
 * Petit histogramme en CSS.
 *
 * Géométrie seulement : la hauteur de chaque barre est proportionnelle à la
 * valeur fournie par l'API (rapport au maximum de la série). Aucune valeur
 * affichée n'est calculée ici — les nombres viennent du plan de contrôle.
 */

import { html, sur } from "./dom.js";
import { usd, jour } from "./format.js";

/** Ratio de hauteur d'une barre (0..1), utilisé uniquement pour le style. */
function hauteur(valeur, maximum) {
  const brut = Number(valeur);
  if (!Number.isFinite(brut) || maximum <= 0) {
    return 0;
  }
  return Math.max(0.02, Math.min(1, brut / maximum));
}

/**
 * Histogramme de coûts quotidiens.
 * @param {Array<{jour: string, cout: number}>} points
 */
export function histogramme(points) {
  if (!Array.isArray(points) || points.length === 0) {
    return sur("");
  }
  const maximum = Math.max(...points.map((point) => Number(point.cout) || 0));
  const barres = points.map((point) => {
    const pourcentage = Math.round(hauteur(point.cout, maximum) * 100);
    const titre = `${jour(point.jour)} — ${usd(point.cout)}`;
    return html`<div
      class="barre"
      style="height: ${sur(`${pourcentage}%`)}"
      title="${titre}"
      role="img"
      aria-label="${titre}"
    ></div>`;
  });
  const premier = jour(points[0].jour);
  const dernier = jour(points[points.length - 1].jour);
  return html`<div class="barres" aria-hidden="false">${barres}</div>
    <div class="legende"><span>${premier}</span><span>${dernier}</span></div>`;
}
