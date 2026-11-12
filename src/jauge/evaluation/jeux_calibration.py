"""Jeu de calibration du modèle juge — construit hors ligne (Top 10, piste n° 2).

Pourquoi ce module existe : le risque n° 1 du cahier des charges (§21) est
« le modèle juge se trompe ». Aujourd'hui on le **mitige** (30 paires relues
par un humain, accord affiché, §13.5) ; on ne le **mesure** pas. Ce module
construit le jeu qui permet de le mesurer :

- des paires de qualité connue (équivalentes / non équivalentes) ;
- des **items-pièges dorés** : une réponse correcte et une réponse fausse
  construites exprès ;
- des paires de **qualité identique** présentées dans les deux ordres
  (biais de position) ;
- des paires où la réponse la **plus longue** est la moins bonne (biais de
  verbosité) ;
- des paires au **formatage** différent pour un contenu identique (biais de
  format).

Les textes viennent de `jeux_calibration_corpus` (synthétiques, hors ligne).
`verifier_jeu` garantit avant toute notation que le jeu est cohérent : familles
complètes, paires croisées, aucune trace de modèle, vérité recoupée par les
contrôles déterministes quand ils peuvent trancher (EF-EVA-02).

Références : §13.3, §13.5, §21, EF-EVA-01..05, ENF-IA-01.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from jauge.common.errors import ValidationError
from jauge.evaluation.checks import echecs_critiques, executer_controles
from jauge.evaluation.jeux_calibration_corpus import (
    EQUIVALENTS,
    NON_EQUIVALENTS,
    PAIRES_FORMAT,
    PAIRES_POSITION,
    PAIRES_VERBOSITE,
    PIEGES_DORES,
)

#: Modèles fictifs du jeu : servent de trace à effacer, jamais de modèle réel.
MODELE_REFERENCE = "modele-reference-fictif"
MODELE_CANDIDAT = "modele-candidat-fictif"
MODELE_AUTRE = "modele-autre-fictif"

#: Catalogue des identifiants de modèles connus du jeu (contrôle de fuite).
CATALOGUE_MODELES: tuple[str, ...] = (MODELE_REFERENCE, MODELE_CANDIDAT, MODELE_AUTRE)

#: Tâche dont le contenu est vérifiable par un contrôle déterministe :
#: `classification` porte une étiquette, que `etiquette_identique` compare.
TACHE_RECOUPABLE = "classification"


class FamilleCalibration(StrEnum):
    """Familles d'items : chacune mesure un biais précis du juge."""

    EQUIVALENT = "equivalent"
    NON_EQUIVALENT = "non_equivalent"
    PIEGE_DORE = "piege_dore"
    POSITION = "position"
    VERBOSITE = "verbosite"
    FORMAT = "format"


@dataclass(frozen=True)
class ItemCalibration:
    """Un item du jeu : une paire, sa vérité, et la raison de cette vérité.

    `gold_equivalent` est la vérité terrain : les deux réponses rendent-elles le
    même service ? C'est cette vérité que le juge doit retrouver, pas une
    opinion.
    """

    identifiant: str
    famille: FamilleCalibration
    requete: str
    reponse_reference: str
    reponse_candidate: str
    gold_equivalent: bool
    justification: str
    type_tache: str = "redaction"
    cle_paire: str = ""
    modele_reference: str = MODELE_REFERENCE
    modele_candidate: str = MODELE_CANDIDAT
    est_piege: bool = False


def _item(
    identifiant: str,
    famille: FamilleCalibration,
    requete: str,
    reference: str,
    candidate: str,
    gold: bool,
    justification: str,
    type_tache: str = "redaction",
    cle_paire: str = "",
    est_piege: bool = False,
) -> ItemCalibration:
    """Construit un item du jeu (raccourci interne)."""
    return ItemCalibration(
        identifiant=identifiant,
        famille=famille,
        requete=requete,
        reponse_reference=reference,
        reponse_candidate=candidate,
        gold_equivalent=gold,
        justification=justification,
        type_tache=type_tache,
        cle_paire=cle_paire,
        est_piege=est_piege,
    )


def _deux_ordres(
    identifiant: str,
    famille: FamilleCalibration,
    requete: str,
    gauche: str,
    droite: str,
    gold: bool,
    justification: str,
    type_tache: str = "redaction",
) -> tuple[ItemCalibration, ItemCalibration]:
    """La même paire présentée dans les deux ordres (mesure d'invariance).

    Les deux items partagent `cle_paire` : c'est le regroupement qui permet de
    comparer les verdicts d'un ordre à l'autre.
    """
    commun = {
        "famille": famille,
        "requete": requete,
        "gold": gold,
        "justification": justification,
        "type_tache": type_tache,
        "cle_paire": identifiant,
    }
    return (
        _item(f"{identifiant}-a", reference=gauche, candidate=droite, **commun),
        _item(f"{identifiant}-b", reference=droite, candidate=gauche, **commun),
    )


def _items_equivalents() -> list[ItemCalibration]:
    """Paires équivalentes : même service rendu, formulation différente."""
    return [
        _item(
            f"cal-{identifiant}",
            FamilleCalibration.EQUIVALENT,
            requete,
            reference,
            candidate,
            True,
            justification,
        )
        for identifiant, requete, reference, candidate, justification in EQUIVALENTS
    ]


def _items_non_equivalents() -> list[ItemCalibration]:
    """Paires non équivalentes : service rendu différent de la référence."""
    return [
        _item(
            f"cal-{identifiant}",
            FamilleCalibration.NON_EQUIVALENT,
            requete,
            reference,
            candidate,
            False,
            justification,
            type_tache=type_tache,
        )
        for identifiant, requete, reference, candidate, justification, type_tache in NON_EQUIVALENTS
    ]


def _items_pieges_dores() -> list[ItemCalibration]:
    """Items-pièges dorés : chaque piège est noté dans les deux orientations.

    Le candidat correct (le juge doit accepter) et le candidat faux (le juge
    doit refuser) sont deux items distincts : un piège n'est utile que s'il
    teste les deux sens de l'erreur.
    """
    items: list[ItemCalibration] = []
    for identifiant, requete, bonne, mauvaise, type_tache, justification in PIEGES_DORES:
        items.append(
            _item(
                f"cal-{identifiant}-ok",
                FamilleCalibration.PIEGE_DORE,
                requete,
                bonne,
                bonne,
                True,
                "Le candidat est identique à la référence : le juge doit l'accepter.",
                type_tache=type_tache,
                est_piege=True,
            )
        )
        items.append(
            _item(
                f"cal-{identifiant}-ko",
                FamilleCalibration.PIEGE_DORE,
                requete,
                bonne,
                mauvaise,
                False,
                justification,
                type_tache=type_tache,
                est_piege=True,
            )
        )
    return items


def _items_position() -> list[ItemCalibration]:
    """Paires de qualité identique, présentées dans les deux ordres.

    Chaque paire relie deux formulations du même service : la réponse du juge
    **ne doit pas** changer quand l'ordre change. Un juge ancré sur la position
    produit des verdicts divergents d'un ordre à l'autre.
    """
    items: list[ItemCalibration] = []
    for identifiant, requete, gauche, droite in PAIRES_POSITION:
        items.extend(
            _deux_ordres(
                identifiant,
                FamilleCalibration.POSITION,
                requete,
                gauche,
                droite,
                True,
                "Les deux réponses rendent le même service : le verdict ne doit pas "
                "dépendre de l'ordre de présentation.",
            )
        )
    return items


def _items_verbosite() -> list[ItemCalibration]:
    """Paires où la réponse la plus longue est la moins bonne."""
    return [
        _item(
            f"cal-{identifiant}",
            FamilleCalibration.VERBOSITE,
            requete,
            reference,
            candidate,
            False,
            justification,
            type_tache=type_tache,
        )
        for identifiant, requete, reference, candidate, justification, type_tache in PAIRES_VERBOSITE
    ]


def _items_format() -> list[ItemCalibration]:
    """Paires au formatage différent, pour un contenu identique.

    Chaque paire est présentée dans les deux ordres : le contenu est le même,
    seule la présentation change. Un juge qui rejette une présentation
    enrichie rejette un service pourtant rendu à l'identique.
    """
    items: list[ItemCalibration] = []
    for identifiant, requete, gauche, droite, type_tache, justification in PAIRES_FORMAT:
        items.extend(
            _deux_ordres(
                identifiant,
                FamilleCalibration.FORMAT,
                requete,
                gauche,
                droite,
                True,
                justification,
                type_tache=type_tache,
            )
        )
    return items


def jeu_calibration_defaut() -> list[ItemCalibration]:
    """Le jeu de calibration complet, hors ligne et reproductible.

    Ordre stable : la suite d'items ne change pas d'une exécution à l'autre,
    ce qui rend les mesures comparables dans le temps.
    """
    jeu = [
        *_items_equivalents(),
        *_items_non_equivalents(),
        *_items_pieges_dores(),
        *_items_position(),
        *_items_verbosite(),
        *_items_format(),
    ]
    verifier_jeu(jeu)
    return jeu


def trace_modele(texte: str, item: ItemCalibration | None = None) -> str:
    """Identifiant de modèle trouvé dans un texte, ou chaîne vide.

    Cherche les identifiants du catalogue du jeu et ceux de l'item : c'est la
    vérification d'aveuglement (le juge ne doit pas deviner quel modèle a
    produit quelle réponse).
    """
    candidats = list(CATALOGUE_MODELES)
    if item is not None:
        candidats.extend((item.modele_reference, item.modele_candidate))
    for identifiant in candidats:
        if identifiant and identifiant in texte:
            return identifiant
    return ""


def _controle_recoupe(item: ItemCalibration) -> bool:
    """Vrai si les contrôles déterministes confirment la vérité de l'item.

    Recoupement possible uniquement pour `classification` : c'est la seule
    tâche dont un contrôle déterministe compare le contenu (l'étiquette).
    Ailleurs, aucun contrôle ne se prononce sur le contenu, et l'absence
    d'échec ne prouve rien.
    """
    if item.type_tache != TACHE_RECOUPABLE:
        return True
    echecs = echecs_critiques(
        executer_controles(item.type_tache, item.reponse_reference, item.reponse_candidate)
    )
    etiquette_fausse = any(echec.nom == "etiquette_identique" for echec in echecs)
    return etiquette_fausse != item.gold_equivalent


def _json_valide(item: ItemCalibration) -> bool:
    """Vrai si les réponses JSON de l'item sont lisibles.

    Un item dont une réponse n'est pas du JSON ne teste plus le juge : il
    testerait le parseur. Les items JSON du jeu sont donc valides par
    construction, et cette vérification le garantit.
    """
    resultats = executer_controles(item.type_tache, item.reponse_reference, item.reponse_candidate)
    return all(resultat.ok for resultat in resultats if resultat.nom == "json_valide")


def _verifier_paires_ordonnees(jeu: Sequence[ItemCalibration]) -> None:
    """Vérifie que chaque paire à deux ordres est bien croisée."""
    groupes: dict[str, list[ItemCalibration]] = {}
    for item in jeu:
        if item.cle_paire:
            groupes.setdefault(item.cle_paire, []).append(item)
    for cle, items in sorted(groupes.items()):
        if len(items) != 2:
            raise ValidationError(
                "paire de calibration incomplète : deux ordres attendus",
                cle_paire=cle,
                nombre=len(items),
            )
        premier, second = items
        if (
            premier.reponse_reference != second.reponse_candidate
            or premier.reponse_candidate != second.reponse_reference
        ):
            raise ValidationError(
                "paire de calibration non croisée : les deux ordres ne sont pas inversés",
                cle_paire=cle,
            )


def verifier_jeu(jeu: Sequence[ItemCalibration]) -> None:
    """Vérifie la cohérence du jeu avant de le faire noter par le juge.

    Cinq garanties, chacune vérifiable sans réseau :
    1. les identifiants sont uniques et toutes les familles sont représentées ;
    2. les paires à deux ordres sont bien croisées ;
    3. aucun texte ne contient un identifiant de modèle (fuite au juge) ;
    4. pour `classification`, la vérité est confirmée par le contrôle
       déterministe d'étiquette (EF-EVA-02), et les réponses JSON sont lisibles ;
    5. dans la famille « verbosité », la réponse candidate est bien la plus
       longue (définition de la famille).
    """
    if not jeu:
        raise ValidationError("jeu de calibration vide : aucune mesure possible")
    identifiants = [item.identifiant for item in jeu]
    if len(set(identifiants)) != len(identifiants):
        raise ValidationError("identifiants de calibration dupliqués")
    familles = {item.famille for item in jeu}
    manquantes = [famille.value for famille in FamilleCalibration if famille not in familles]
    if manquantes:
        raise ValidationError("familles absentes du jeu de calibration", familles=manquantes)
    _verifier_paires_ordonnees(jeu)
    for item in jeu:
        _verifier_item(item)


def _verifier_item(item: ItemCalibration) -> None:
    """Vérifications propres à un item (fuite, vérité, longueur)."""
    for texte in (item.requete, item.reponse_reference, item.reponse_candidate):
        trace = trace_modele(texte, item)
        if trace:
            raise ValidationError(
                "identifiant de modèle dans un texte de calibration",
                identifiant=item.identifiant,
                trace=trace,
            )
    if not _controle_recoupe(item):
        raise ValidationError(
            "vérité de l'item contredite par les contrôles déterministes",
            identifiant=item.identifiant,
            gold_equivalent=item.gold_equivalent,
        )
    if not _json_valide(item):
        raise ValidationError(
            "item de calibration au JSON invalide : il testerait le parseur, pas le juge",
            identifiant=item.identifiant,
        )
    if item.famille is FamilleCalibration.VERBOSITE and len(item.reponse_candidate) <= len(
        item.reponse_reference
    ):
        raise ValidationError(
            "item de verbosité invalide : le candidat n'est pas le plus long",
            identifiant=item.identifiant,
        )
