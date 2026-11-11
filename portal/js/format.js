/**
 * Formatage des valeurs rendues par l'API.
 *
 * Aucun calcul métier ici : seulement de la mise en forme et des conversions
 * d'unité d'affichage (ratio 0..1 → pourcentage, millisecondes → secondes).
 * Tout agrégat (somme, moyenne, écart) est calculé par le plan de contrôle.
 */

const DECIMAL = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const ENTIER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

const DIXIEME = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const TIRET = "—";

function estNombre(valeur) {
  return valeur !== null && valeur !== undefined && Number.isFinite(Number(valeur));
}

/** Nombre entier formaté (`1 234`), tiret si absent. */
export function entier(valeur) {
  return estNombre(valeur) ? ENTIER.format(Number(valeur)) : TIRET;
}

/** Nombre décimal à deux chiffres, tiret si absent. */
export function decimal(valeur) {
  return estNombre(valeur) ? DECIMAL.format(Number(valeur)) : TIRET;
}

/** Montant en dollars US, tel que fourni par l'API. */
export function usd(valeur) {
  return estNombre(valeur) ? `$${DECIMAL.format(Number(valeur))}` : TIRET;
}

/** Valeur déjà exprimée en pourcentage par l'API (`12.4` → `12.4 %`). */
export function pourcent(valeur) {
  return estNombre(valeur) ? `${DIXIEME.format(Number(valeur))} %` : TIRET;
}

/** Ratio 0..1 rendu en pourcentage à une décimale (`0.9512` → `95.1 %`). */
export function ratio(valeur) {
  return estNombre(valeur) ? `${DIXIEME.format(Number(valeur) * 100)} %` : TIRET;
}

/** Latence en millisecondes. */
export function millisecondes(valeur) {
  return estNombre(valeur) ? `${ENTIER.format(Number(valeur))} ms` : TIRET;
}

/** Date ISO (`2026-11-10`) rendue au format anglais court. */
export function jour(valeur) {
  if (!valeur) {
    return TIRET;
  }
  const date = new Date(`${String(valeur).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return String(valeur);
  }
  return date.toLocaleDateString("en-US", { day: "2-digit", month: "short", year: "numeric" });
}

/** Horodatage ISO rendu en date et heure locales. */
export function horodatage(valeur) {
  if (!valeur) {
    return TIRET;
  }
  const date = new Date(valeur);
  if (Number.isNaN(date.getTime())) {
    return String(valeur);
  }
  return date.toLocaleString("en-US", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Valeur texte ou tiret. */
export function texteOuTiret(valeur) {
  return valeur === null || valeur === undefined || valeur === "" ? TIRET : String(valeur);
}

/** Liste de textes séparés par des virgules, ou tiret si vide. */
export function listeTextes(valeurs) {
  return Array.isArray(valeurs) && valeurs.length > 0 ? valeurs.join(", ") : TIRET;
}

/** Libellé lisible pour un identifiant technique (`ticket-classifier`). */
export function libelle(valeur) {
  if (!valeur) {
    return TIRET;
  }
  return String(valeur).replace(/[_-]+/g, " ");
}
