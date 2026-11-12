"""Variantes de prompt déterministes, diffables et auditées (piste n° 8).

Le prompt du client est une **donnée** : il vit dans le plan de données, il
n'en sort pas (ENF-CONF-01) et il n'est jamais exécuté comme une instruction
(ENF-IA-03). Ce module :

1. produit des **variantes candidates déterministes** — une liste blanche de
   transformations, aucune réécriture libre par un modèle ;
2. conserve pour chaque variante son **diff** contre l'original ;
3. refuse **par construction** toute variante qui modifierait une contrainte
   métier (format de sortie, énumérations autorisées, consignes de sécurité).

La preuve par rejeu de ces variantes vit dans `jauge.replay.variantes_preuve`.
Aucun chiffre n'est inventé (ENF-IA-01) : les jetons viennent de la convention
du socle (`jauge.replay.client.JETONS_PAR_CARACTERE`).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from jauge.common.errors import ValidationError
from jauge.common.ids import deterministic
from jauge.replay.client import JETONS_PAR_CARACTERE

# --- Liste blanche des transformations : rien d'autre n'est appliqué -------
TRANSFORMATIONS: tuple[str, ...] = (
    "suppression_exemples_redondants",
    "condensation_consignes_repetees",
    "retrait_politesses",
    "troncature_contexte",
    "deplacement_prefixe_cache",
)

# --- Contraintes métier : lignes intouchables ----------------------------
MOTIFS_CONTRAINTE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("format de sortie", re.compile(r"\b(json|xml|yaml|csv|schéma|format de sortie)\b", re.I)),
    ("énumération autorisée", re.compile(r"\b(parmi|valeurs autorisées|uniquement)\b", re.I)),
    ("sécurité", re.compile(r"\b(ne jamais|interdit|confidentiel|sécurité|rgpd)\b", re.I)),
    ("rôle", re.compile(r"^\s*(tu es|vous êtes|you are)\b", re.I | re.M)),
    ("longueur de sortie", re.compile(r"\b(concis|maximum de|en moins de)\b", re.I)),
)

# --- Formules de politesse retirables ------------------------------------
POLITESSES: tuple[str, ...] = (
    "merci de bien vouloir",
    "s'il vous plaît",
    "veuillez",
    "bonjour",
    "bonsoir",
    "cordialement",
)

# --- Bornes par défaut des transformations --------------------------------
MAX_EXEMPLES = 2
SEUIL_CONTEXTE = 2000
OUVERTURE_CONTEXTE = "<contexte>"
FERMETURE_CONTEXTE = "</contexte>"
MOTIF_EXEMPLE = re.compile(r"^\s*(?:[-*•]\s*)?(exemple|example)\b", re.I)
MOTIF_PREFIXE = re.compile(r"^\s*(?:[-*•]\s*)?(exemple|example|contexte)\b", re.I)


def normaliser(ligne: str) -> str:
    """Ligne normalisée (espaces réduits, casse abaissée) pour comparaison."""
    return " ".join(ligne.strip().lower().split())


def est_contrainte(ligne: str) -> bool:
    """Vrai si la ligne porte une contrainte métier : elle est intouchable."""
    return any(motif.search(ligne) for _, motif in MOTIFS_CONTRAINTE)


def contraintes_presentes(texte: str) -> tuple[tuple[str, str], ...]:
    """Contraintes du texte : (libellé, ligne normalisée), sans doublon."""
    trouvees: list[tuple[str, str]] = []
    for ligne in texte.splitlines():
        for libelle, motif in MOTIFS_CONTRAINTE:
            if motif.search(ligne) and (libelle, normaliser(ligne)) not in trouvees:
                trouvees.append((libelle, normaliser(ligne)))
    return tuple(trouvees)


def verifier_integrite(origine: str, variante: str) -> str:
    """Motif de refus si la variante a modifié une contrainte métier."""
    apres = set(contraintes_presentes(variante))
    manquantes = [
        libelle
        for libelle, ligne in contraintes_presentes(origine)
        if (libelle, ligne) not in apres
    ]
    if not manquantes:
        return ""
    return "contrainte métier modifiée : " + ", ".join(sorted(set(manquantes)))


def _indice(lignes: Sequence[str], marqueur: str) -> int | None:
    """Premier index d'une ligne portant exactement le marqueur."""
    for index, ligne in enumerate(lignes):
        if ligne.strip() == marqueur:
            return index
    return None


def _est_borne(ligne: str) -> bool:
    """Une borne ferme un bloc d'exemple : jamais une contrainte n'est dedans."""
    if not ligne.strip():
        return True
    if ligne.strip() in (OUVERTURE_CONTEXTE, FERMETURE_CONTEXTE):
        return True
    return bool(MOTIF_EXEMPLE.match(ligne)) or est_contrainte(ligne)


def _blocs_exemples(lignes: Sequence[str]) -> list[tuple[int, int]]:
    """Blocs d'exemples peu-shot : (début, fin exclusive), bornes comprises.

    Un bloc s'arrête à la première borne : autre exemple, ligne vide, contrainte
    métier ou marqueur de contexte. Une contrainte ne peut donc jamais se
    retrouver dans un bloc supprimé — c'est le garde-fou « par construction ».
    """
    blocs: list[tuple[int, int]] = []
    for index, ligne in enumerate(lignes):
        if not MOTIF_EXEMPLE.match(ligne):
            continue
        fin = index + 1
        while fin < len(lignes) and not _est_borne(lignes[fin]):
            fin += 1
        blocs.append((index, fin))
    return blocs


def _supprimer_exemples(lignes: Sequence[str], max_exemples: int) -> list[str]:
    """Retire les exemples au-delà de `max_exemples` (les autres restent)."""
    blocs = _blocs_exemples(lignes)
    if len(blocs) <= max_exemples:
        return list(lignes)
    a_retirer: set[int] = set()
    for debut, fin in blocs[max_exemples:]:
        a_retirer.update(range(debut, fin))
    return [ligne for index, ligne in enumerate(lignes) if index not in a_retirer]


def _condenser_consignes(lignes: Sequence[str]) -> list[str]:
    """Supprime les lignes non contraintes déjà présentes plus haut (texte exact)."""
    vues: set[str] = set()
    gardees: list[str] = []
    for ligne in lignes:
        norm = normaliser(ligne)
        if not norm or est_contrainte(ligne):
            gardees.append(ligne)
            continue
        if norm in vues:
            continue
        vues.add(norm)
        gardees.append(ligne)
    return gardees


def _retirer_politesses(lignes: Sequence[str]) -> list[str]:
    """Retire les formules de politesse des lignes non contraintes."""
    resultat: list[str] = []
    for ligne in lignes:
        if est_contrainte(ligne):
            resultat.append(ligne)
            continue
        nettoye = ligne
        for formule in POLITESSES:
            nettoye = re.sub(rf"\b{re.escape(formule)}\b[ ,!]*", "", nettoye, flags=re.IGNORECASE)
        resultat.append(" ".join(nettoye.split()))
    return resultat


def _tronquer_contexte(lignes: Sequence[str], seuil: int) -> list[str]:
    """Tronque le bloc `<contexte> … </contexte>` au-delà de `seuil` caractères."""
    debut = _indice(lignes, OUVERTURE_CONTEXTE)
    fin = _indice(lignes, FERMETURE_CONTEXTE)
    if debut is None or fin is None or fin <= debut:
        return list(lignes)
    contenu = "\n".join(lignes[debut + 1 : fin])
    if len(contenu) <= seuil:
        return list(lignes)
    retir = len(contenu) - seuil
    return (
        list(lignes[: debut + 1])
        + [contenu[:seuil], f"[contexte tronqué : {retir} caractères retirés]"]
        + list(lignes[fin:])
    )


def _isoler_prefixe_cache(lignes: Sequence[str]) -> list[str]:
    """Encadre le préfixe stable pour y poser un point de cache de prompts.

    Cette transformation ne retire aucun jeton : son gain est celui du levier
    « cache de prompts » (EF-LEV-02). Elle est conservée pour tracer la piste
    et sera écartée par le critère de gain net si elle n'apporte aucun euro.
    """
    fin = len(lignes)
    for index, ligne in enumerate(lignes):
        if MOTIF_PREFIXE.match(ligne) or ligne.strip() == OUVERTURE_CONTEXTE:
            fin = index
            break
    if fin == 0:
        return list(lignes)
    return ["<prefixe_cache>", *lignes[:fin], "</prefixe_cache>", *lignes[fin:]]


def appliquer_transformation(
    nom: str,
    consigne: str,
    *,
    max_exemples: int = MAX_EXEMPLES,
    seuil_contexte: int = SEUIL_CONTEXTE,
) -> tuple[str, str]:
    """Applique une transformation de la liste blanche.

    Rend (texte résultant, motif de refus). Une transformation hors liste
    blanche est refusée : rien d'autre n'est appliqué au prompt du client.
    """
    if nom not in TRANSFORMATIONS:
        raise ValidationError("Transformation hors liste blanche.", transformation=nom)
    lignes = consigne.splitlines()
    if nom == "suppression_exemples_redondants":
        resultat = _supprimer_exemples(lignes, max_exemples)
    elif nom == "condensation_consignes_repetees":
        resultat = _condenser_consignes(lignes)
    elif nom == "retrait_politesses":
        resultat = _retirer_politesses(lignes)
    elif nom == "troncature_contexte":
        resultat = _tronquer_contexte(lignes, seuil_contexte)
    else:
        resultat = _isoler_prefixe_cache(lignes)
    if resultat == list(lignes):
        return consigne, ""
    texte = "\n".join(resultat)
    motif = verifier_integrite(consigne, texte)
    return (consigne, motif) if motif else (texte, "")


def estimer_jetons(texte: str) -> int:
    """Estimation de jetons d'un texte (convention du socle : 0,25 jeton/caractère)."""
    return max(1, int(len(texte) * JETONS_PAR_CARACTERE))


def diff_texte(origine: str, variante: str) -> str:
    """Diff unifié entre l'original et la variante (conservé pour l'audit)."""
    lignes = difflib.unified_diff(
        origine.splitlines(),
        variante.splitlines(),
        fromfile="consigne-originale",
        tofile="consigne-variante",
        lineterm="",
    )
    return "\n".join(lignes)


@dataclass(frozen=True)
class VariantePrompt:
    """Variante de prompt : texte, diff et métriques (plan de données)."""

    variante_id: str
    transformation: str
    consigne_originale: str
    consigne_variante: str
    diff: str
    jetons_origine: int
    jetons_variante: int
    refus: str = ""

    @property
    def acceptee(self) -> bool:
        """Vrai si la variante n'a pas été refusée par le garde-fou sémantique."""
        return not self.refus

    @property
    def jetons_economises(self) -> int:
        """Jetons d'entrée retirés par la variante (0 si elle est refusée)."""
        if not self.acceptee:
            return 0
        return max(0, self.jetons_origine - self.jetons_variante)


def generer_variantes(
    consigne: str,
    *,
    application_id: str,
    max_exemples: int = MAX_EXEMPLES,
    seuil_contexte: int = SEUIL_CONTEXTE,
) -> tuple[VariantePrompt, ...]:
    """Produit les variantes candidates : déterministes, diffables, auditées."""
    variantes: list[VariantePrompt] = []
    for nom in TRANSFORMATIONS:
        texte, motif = appliquer_transformation(
            nom, consigne, max_exemples=max_exemples, seuil_contexte=seuil_contexte
        )
        if not motif and texte == consigne:
            continue  # la transformation n'apporte rien : pas de candidate
        variantes.append(
            VariantePrompt(
                variante_id=f"var-{deterministic(application_id, nom, consigne)}",
                transformation=nom,
                consigne_originale=consigne,
                consigne_variante=texte,
                diff=diff_texte(consigne, texte) if not motif else "",
                jetons_origine=estimer_jetons(consigne),
                jetons_variante=estimer_jetons(texte),
                refus=motif,
            )
        )
    return tuple(variantes)
