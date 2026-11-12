"""Règle de décision d'un candidat (§13.4, EF-EVA-03).

Un candidat est **proposé** si les trois conditions du §13.4 sont réunies :

1. son taux d'équivalence atteint le seuil de l'application (95 % par défaut) ;
2. la borne basse de l'intervalle de confiance à 95 % dépasse 90 % ;
3. aucun contrôle déterministe critique n'échoue.

Sinon il est **rejeté avec ses motifs**, un par condition non remplie : un refus
muet n'est pas exploitable par le responsable d'application.

Lecture relative obligatoire (§13.4) : le taux du candidat est toujours présenté
à côté de l'auto-accord du modèle actuel. Si le modèle actuel n'est équivalent à
lui-même qu'à 96 %, un candidat à 95 % est excellent — il fait aussi bien que la
variabilité naturelle du modèle en place. `qualification` porte ce jugement, et
`lecture_relative` la phrase qui l'accompagne.

Pré-enregistrement (piste n° 1 du top 10) : quand un `ProtocoleEvaluation`
scellé est présenté, deux choses s'ajoutent, sans rien retirer :

- le protocole est **vérifié** avant toute conclusion (`verifier_scelle`) : si
  l'empreinte recalculée ne correspond plus à l'empreinte engagée avant le
  rejeu, la décision est refusée (`EtatIncompatibleError`) plutôt que rendue sur
  une méthode retouchée ;
- la **marge de non-infériorité** pré-engagée devient une quatrième condition :
  le candidat ne doit pas s'effondrer sous l'auto-accord du modèle actuel de plus
  que la marge annoncée. Le verdict le dit explicitement
  (`lecture_non_inferiorite`) : « non-inférieur dans la marge de X points » ou
  « inférieur ». Sans protocole, les trois conditions du §13.4 s'appliquent
  exactement comme avant — la règle existante n'est ni remplacée ni durcie.

Aucun texte de requête ni de réponse ne traverse ce module (ENF-CONF-01) : la
décision ne porte que sur des nombres déjà calculés et des noms de contrôles.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from jauge.common.errors import EtatIncompatibleError, ValidationError
from jauge.evaluation.protocole import ProtocoleEvaluation, verifier_scelle
from jauge.models.campaign import ResultatCandidat
from jauge.models.tenant import Application

# Écart toléré entre le taux du candidat et l'auto-accord du modèle actuel :
# en deçà, le candidat fait aussi bien que le modèle en place comparé à lui-même.
MARGE_AUTO_ACCORD = 0.02

# Tolérance de comparaison flottante : les taux sont arrondis à 1e-6, une marge
# pré-engagée de 0.05 ne doit pas faire basculer un verdict sur un ulp.
TOLERANCE_FLOTTANTE = 1e-9


class Qualification(StrEnum):
    """Lecture du taux du candidat face à l'auto-accord du modèle actuel."""

    EXCELLENT = "excellent"
    ACCEPTABLE = "acceptable"
    INSUFFISANT = "insuffisant"


@dataclass(frozen=True)
class SeuilsDecision:
    """Seuils de décision d'une application (§13.4, `Application.seuil_*`)."""

    seuil_equivalence: float = 0.95
    seuil_intervalle_bas: float = 0.90

    def __post_init__(self) -> None:
        for nom, valeur in (
            ("seuil_equivalence", self.seuil_equivalence),
            ("seuil_intervalle_bas", self.seuil_intervalle_bas),
        ):
            if not 0.0 < valeur <= 1.0:
                raise ValidationError("seuil hors de ]0, 1]", seuil=nom, valeur=valeur)

    @classmethod
    def depuis_application(cls, application: Application) -> SeuilsDecision:
        """Seuils portés par l'application (§13.4 : « le seuil de l'application »)."""
        return cls(
            seuil_equivalence=application.seuil_qualite,
            seuil_intervalle_bas=application.seuil_intervalle_bas,
        )


@dataclass(frozen=True)
class Condition:
    """Une des trois conditions du §13.4, avec son verdict et son motif."""

    nom: str
    remplie: bool
    detail: str


@dataclass(frozen=True)
class DecisionCandidat:
    """Décision motivée sur un candidat. Ne contient aucun texte de requête."""

    modele_id: str
    propose: bool
    qualification: Qualification
    conditions: tuple[Condition, ...] = field(default_factory=tuple)
    motifs: tuple[str, ...] = field(default_factory=tuple)
    resultat: ResultatCandidat | None = None
    lecture_relative: str = ""
    #: Marge de non-infériorité pré-engagée ; `None` sans protocole scellé.
    marge_non_inferiorite: float | None = None
    #: Verdict de la condition de non-infériorité ; `None` sans protocole scellé.
    non_inferiorite: bool | None = None
    #: Phrase explicite : « non-inférieur dans la marge de X points » ou « inférieur ».
    lecture_non_inferiorite: str = ""

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable remise au plan de contrôle (ENF-CONF-01).

        Champs scalaires et textes de motifs uniquement : ni requête, ni
        réponse, ni explication du juge. Les trois clés de non-infériorité
        n'apparaissent **que** si un protocole scellé a été présenté : sans
        protocole, la vue reste exactement celle de la règle du §13.4.
        """
        vue: dict[str, object] = {
            "modele_id": self.modele_id,
            "propose": self.propose,
            "qualification": str(self.qualification),
            "conditions": [
                {"nom": c.nom, "remplie": c.remplie, "detail": c.detail} for c in self.conditions
            ],
            "motifs": list(self.motifs),
            "lecture_relative": self.lecture_relative,
        }
        if self.marge_non_inferiorite is not None:
            vue.update(
                {
                    "marge_non_inferiorite": self.marge_non_inferiorite,
                    "non_inferiorite": self.non_inferiorite,
                    "lecture_non_inferiorite": self.lecture_non_inferiorite,
                }
            )
        return vue


def _pourcentage(valeur: float) -> str:
    """Formate un taux en pourcentage à un dixième (lecture humaine)."""
    return f"{valeur * 100:.1f} %"


def condition_taux(resultat: ResultatCandidat, seuils: SeuilsDecision) -> Condition:
    """Condition 1 : le taux atteint le seuil de l'application."""
    remplie = resultat.taux_equivalence >= seuils.seuil_equivalence
    detail = (
        f"taux {_pourcentage(resultat.taux_equivalence)} "
        f"(seuil {_pourcentage(seuils.seuil_equivalence)})"
    )
    return Condition(nom="taux_equivalence", remplie=remplie, detail=detail)


def condition_intervalle(resultat: ResultatCandidat, seuils: SeuilsDecision) -> Condition:
    """Condition 2 : la borne basse de l'intervalle dépasse le seuil."""
    remplie = resultat.intervalle_confiance_bas > seuils.seuil_intervalle_bas
    detail = (
        f"borne basse {_pourcentage(resultat.intervalle_confiance_bas)} "
        f"(exige > {_pourcentage(seuils.seuil_intervalle_bas)})"
    )
    return Condition(nom="intervalle_confiance_bas", remplie=remplie, detail=detail)


def condition_controles(resultat: ResultatCandidat) -> Condition:
    """Condition 3 : aucun contrôle déterministe critique en échec."""
    remplie = resultat.echecs_deterministes == 0
    detail = f"{resultat.echecs_deterministes} échec(s) déterministe(s) critique(s)"
    return Condition(nom="controles_deterministes", remplie=remplie, detail=detail)


def plancher_non_inferiorite(resultat: ResultatCandidat, marge: float) -> float:
    """Plancher imposé par la marge pré-engagée : auto-accord du modèle actuel moins la marge."""
    return resultat.auto_accord_modele_actuel - marge


def condition_non_inferiorite(resultat: ResultatCandidat, marge: float) -> Condition:
    """Condition ajoutée : le candidat reste dans la marge de non-infériorité.

    La marge est **pré-engagée** dans le protocole scellé : elle ne peut pas être
    élargie après coup pour faire passer un candidat. Elle se lit par rapport à
    l'auto-accord du modèle actuel (§13.4), pas par rapport à un absolu.
    """
    plancher = plancher_non_inferiorite(resultat, marge)
    remplie = resultat.taux_equivalence >= plancher - TOLERANCE_FLOTTANTE
    detail = (
        f"taux {_pourcentage(resultat.taux_equivalence)} pour un plancher de "
        f"{_pourcentage(plancher)} (auto-accord {_pourcentage(resultat.auto_accord_modele_actuel)} "
        f"moins la marge de {marge * 100:.1f} points)"
    )
    return Condition(nom="non_inferiorite", remplie=remplie, detail=detail)


def lecture_non_inferiorite(resultat: ResultatCandidat, marge: float) -> str:
    """Phrase explicite du verdict de non-infériorité (jamais implicite)."""
    ecart = (resultat.auto_accord_modele_actuel - resultat.taux_equivalence) * 100
    if resultat.taux_equivalence >= plancher_non_inferiorite(resultat, marge) - TOLERANCE_FLOTTANTE:
        return (
            f"non-inférieur dans la marge de {marge * 100:.1f} points "
            f"(écart de {max(ecart, 0.0):.1f} point(s) avec l'auto-accord du modèle actuel)"
        )
    return (
        f"inférieur : {ecart:.1f} point(s) sous l'auto-accord du modèle actuel, "
        f"au-delà de la marge pré-engagée de {marge * 100:.1f} points"
    )


def _conditions(
    resultat: ResultatCandidat, seuils: SeuilsDecision, marge: float | None
) -> tuple[Condition, ...]:
    """Les trois conditions du §13.4, plus la marge pré-engagée si un protocole."""
    conditions = [
        condition_taux(resultat, seuils),
        condition_intervalle(resultat, seuils),
        condition_controles(resultat),
    ]
    if marge is not None:
        conditions.append(condition_non_inferiorite(resultat, marge))
    return tuple(conditions)


def _seuils_du_protocole(
    seuils: SeuilsDecision | None, protocole: ProtocoleEvaluation
) -> SeuilsDecision:
    """Seuils applicables : ceux du protocole scellé, contradiction refusée.

    Un appelant qui présente des seuils différents de ceux engagés avant le
    rejeu se voit opposer une erreur explicite : c'est une dérive de méthode,
    pas une variante d'affichage.
    """
    engages = SeuilsDecision(
        seuil_equivalence=protocole.seuils.seuil_equivalence,
        seuil_intervalle_bas=protocole.seuils.borne_basse_intervalle,
    )
    if seuils is None:
        return engages
    ecarts = [
        abs(present - engage) > TOLERANCE_FLOTTANTE
        for present, engage in (
            (seuils.seuil_equivalence, engages.seuil_equivalence),
            (seuils.seuil_intervalle_bas, engages.seuil_intervalle_bas),
        )
    ]
    if any(ecarts):
        raise EtatIncompatibleError(
            "Les seuils présentés ne sont pas ceux du protocole scellé",
            seuils_presentes={
                "seuil_equivalence": seuils.seuil_equivalence,
                "seuil_intervalle_bas": seuils.seuil_intervalle_bas,
            },
            seuils_engages={
                "seuil_equivalence": engages.seuil_equivalence,
                "seuil_intervalle_bas": engages.seuil_intervalle_bas,
            },
            empreinte_protocole=protocole.scelle.empreinte if protocole.scelle else None,
        )
    return engages


def qualifier(resultat: ResultatCandidat, marge: float = MARGE_AUTO_ACCORD) -> Qualification:
    """Qualifie le taux du candidat **par rapport à l'auto-accord** (§13.4).

    Un candidat à 95 % face à un modèle actuel qui ne s'accorde qu'à 96 % avec
    lui-même est excellent : l'écart est dans la variabilité naturelle.
    """
    if resultat.taux_equivalence >= resultat.auto_accord_modele_actuel - marge:
        return Qualification.EXCELLENT
    if resultat.taux_equivalence >= resultat.auto_accord_modele_actuel - 0.10:
        return Qualification.ACCEPTABLE
    return Qualification.INSUFFISANT


def lecture_relative(resultat: ResultatCandidat) -> str:
    """Phrase de lecture du taux à côté de l'auto-accord du modèle actuel."""
    ecart = (resultat.taux_equivalence - resultat.auto_accord_modele_actuel) * 100
    sens = "au-dessus" if ecart >= 0 else "en dessous"
    return (
        f"candidat {_pourcentage(resultat.taux_equivalence)} contre "
        f"{_pourcentage(resultat.auto_accord_modele_actuel)} d'auto-accord du modèle actuel : "
        f"{abs(ecart):.1f} point(s) {sens}"
    )


def decider(
    resultat: ResultatCandidat,
    seuils: SeuilsDecision | None = None,
    *,
    protocole: ProtocoleEvaluation | None = None,
) -> DecisionCandidat:
    """Applique la règle du §13.4 à un candidat et motive le verdict.

    `protocole` est optionnel. Sans lui, le comportement est inchangé (trois
    conditions). Avec lui, le protocole est d'abord **vérifié** contre son sceau,
    ses seuils pré-engagés s'imposent, et la marge de non-infériorité devient une
    quatrième condition assortie d'une phrase de verdict explicite.
    """
    marge = protocole.marge_non_inferiorite if protocole is not None else None
    if protocole is not None:
        verifier_scelle(protocole)
        seuils = _seuils_du_protocole(seuils, protocole)
    else:
        seuils = seuils or SeuilsDecision()
    conditions = _conditions(resultat, seuils, marge)
    motifs = tuple(
        f"{condition.nom} : {condition.detail}" for condition in conditions if not condition.remplie
    )
    if resultat.est_reference:
        motifs = ("modele_actuel : la référence n'est pas un candidat de bascule", *motifs)
    return DecisionCandidat(
        modele_id=resultat.modele_id,
        propose=not motifs,
        qualification=qualifier(resultat),
        conditions=conditions,
        motifs=motifs,
        resultat=resultat,
        lecture_relative=lecture_relative(resultat),
        marge_non_inferiorite=marge,
        non_inferiorite=None if marge is None else _non_inferiorite(conditions),
        lecture_non_inferiorite=("" if marge is None else lecture_non_inferiorite(resultat, marge)),
    )


def _non_inferiorite(conditions: tuple[Condition, ...]) -> bool | None:
    """Verdict booléen de la condition de non-infériorité, si elle figure."""
    for condition in conditions:
        if condition.nom == "non_inferiorite":
            return condition.remplie
    return None


def decider_lot(
    resultats: Sequence[ResultatCandidat],
    seuils: SeuilsDecision | None = None,
    *,
    protocole: ProtocoleEvaluation | None = None,
) -> list[DecisionCandidat]:
    """Décide sur un lot de candidats, dans l'ordre reçu (le meilleur reste à voir).

    Le tri est laissé à l'appelant : `resultats` peut contenir la référence du
    modèle actuel, qui n'est jamais proposée. Le protocole scellé, s'il est
    présenté, s'applique à **tout** le lot : on ne change pas de méthode entre
    deux candidats.
    """
    if not resultats:
        raise ValidationError("aucun résultat de candidat à décider")
    return [decider(resultat, seuils, protocole=protocole) for resultat in resultats]


def candidats_proposes(decisions: Sequence[DecisionCandidat]) -> list[DecisionCandidat]:
    """Candidats proposables, du meilleur au moins bon (taux décroissant)."""
    retenus = [decision for decision in decisions if decision.propose and decision.resultat]
    return sorted(
        retenus,
        key=lambda decision: (
            -decision.resultat.taux_equivalence,  # type: ignore[union-attr]
            decision.modele_id,
        ),
    )
