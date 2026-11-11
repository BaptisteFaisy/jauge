/**
 * Helpers DOM sûrs du portail.
 *
 * Règle de robustesse : toute donnée venue de l'API passe par `html` qui
 * échappe systématiquement `& < > " '`. Les seuls contenus insérés sans
 * échappement sont des `Fragment` produits par ce module (`sur`), donc déjà
 * construits à partir de valeurs échappées. `innerHTML` n'apparaît que dans
 * `monter`, ce qui rend l'injection impossible depuis une vue.
 */

const ECHAPPEMENTS = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Fragment déjà échappé, à insérer tel quel dans un gabarit `html`. */
export class Fragment {
  constructor(valeur) {
    this.valeur = valeur;
  }

  toString() {
    return this.valeur;
  }
}

/** Déclare un contenu comme déjà sûr (jamais une valeur d'API brute). */
export function sur(valeur) {
  return new Fragment(String(valeur ?? ""));
}

/** Échappe une valeur quelconque pour insertion dans du HTML. */
export function echapper(valeur) {
  return String(valeur ?? "").replace(/[&<>"']/g, (caractere) => ECHAPPEMENTS[caractere]);
}

function rendre(valeur) {
  if (valeur instanceof Fragment) {
    return valeur.valeur;
  }
  if (Array.isArray(valeur)) {
    return valeur.map(rendre).join("");
  }
  return echapper(valeur);
}

/** Gabarit balisé : échappe toute interpolation non déclarée sûre. */
export function html(morceaux, ...valeurs) {
  let sortie = morceaux[0];
  valeurs.forEach((valeur, index) => {
    sortie += rendre(valeur) + morceaux[index + 1];
  });
  return new Fragment(sortie);
}

/** Remplace le contenu d'un élément par un fragment ou du HTML déjà sûr. */
export function monter(cible, contenu) {
  if (!cible) {
    return;
  }
  cible.innerHTML = contenu instanceof Fragment ? contenu.valeur : String(contenu ?? "");
}

/** Écrit du texte brut dans un élément (jamais interprété comme du HTML). */
export function ecrireTexte(cible, valeur) {
  if (cible) {
    cible.textContent = String(valeur ?? "");
  }
}

/** Sélecteur unique, tolérant à l'absence. */
export function choisir(racine, selecteur) {
  return racine ? racine.querySelector(selecteur) : null;
}

/** Sélecteur multiple, rendu en tableau. */
export function choisirTous(racine, selecteur) {
  return racine ? Array.from(racine.querySelectorAll(selecteur)) : [];
}

/** Carte de contenu avec titre. */
export function carte(titre, corps) {
  return html`<section class="carte"><h2>${titre}</h2>${corps}</section>`;
}

/** Mesure mise en avant : libellé, valeur (déjà formatée), unité. */
export function mesure(libelle, valeur, unite) {
  return html`<div class="carte">
    <h2>${libelle}</h2>
    <span class="mesure">${valeur}</span>
    <span class="mesure-unite">${unite || ""}</span>
  </div>`;
}

/** Étiquette d'état colorée. `genre` : ok, alerte, attente, neutre. */
export function etiquette(libelle, genre) {
  const classe = ["ok", "alerte", "attente", "neutre"].includes(genre) ? genre : "neutre";
  return html`<span class="etat etat-${sur(classe)}">${libelle}</span>`;
}

/** Tableau accessible : légende, en-têtes, lignes (chaque cellule = Fragment). */
export function tableau({ legende, entetes, lignes }) {
  const cap = legende ? html`<caption>${legende}</caption>` : sur("");
  const tetes = entetes.map((entete) => html`<th scope="col">${entete}</th>`);
  const corps = lignes.map(
    (ligne) => html`<tr>
      ${ligne.map((cellule) => html`<td>${cellule}</td>`)}
    </tr>`,
  );
  return html`<div class="table-enveloppe">
    <table>
      ${cap}
      <thead>
        <tr>
          ${tetes}
        </tr>
      </thead>
      <tbody>
        ${corps}
      </tbody>
    </table>
  </div>`;
}

/** Message d'absence de données. */
export function vide(message) {
  return html`<p class="vide">${message}</p>`;
}

/** Fil d'Ariane : liste de { libelle, href }. */
export function fil(liens) {
  const elements = liens.map(
    (lien, index) =>
      html`${index > 0 ? sur(" / ") : sur("")}<a href="${lien.href}">${lien.libelle}</a>`,
  );
  return html`<p class="fil">${elements}</p>`;
}

/** Bloc de texte long (explication du juge), conservé tel que fourni. */
export function blocTexte(titre, texte) {
  return html`<div class="paire-bloc">
    <h4>${titre}</h4>
    <p class="paire-texte">${texte}</p>
  </div>`;
}
