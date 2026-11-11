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

Aucun texte de requête ni de réponse ne traverse ce module (ENF-CONF-01) : la
décision ne porte que sur des nombres déjà calculés et des noms de contrôles.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from jauge.common.errors import ValidationError
from jauge.models.campaign import ResultatCandidat
from jauge.models.tenant import Application

# Écart toléré entre le taux du candidat et l'auto-accord du modèle actuel :
# en deçà, le candidat fait aussi bien que le modèle en place comparé à lui-même.
MARGE_AUTO_ACCORD = 0.02


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

    def vers_dict(self) -> dict[str, object]:
        """Vue sérialisable remise au plan de contrôle (ENF-CONF-01).

        Champs scalaires et textes de motifs uniquement : ni requête, ni
        réponse, ni explication du juge.
        """
        return {
            "modele_id": self.modele_id,
            "propose": self.propose,
            "qualification": str(self.qualification),
            "conditions": [
                {"nom": c.nom, "remplie": c.remplie, "detail": c.detail} for c in self.conditions
            ],
            "motifs": list(self.motifs),
            "lecture_relative": self.lecture_relative,
        }


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


def decider(resultat: ResultatCandidat, seuils: SeuilsDecision | None = None) -> DecisionCandidat:
    """Applique la règle du §13.4 à un candidat et motive le verdict."""
    seuils = seuils or SeuilsDecision()
    conditions = (
        condition_taux(resultat, seuils),
        condition_intervalle(resultat, seuils),
        condition_controles(resultat),
    )
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
    )


def decider_lot(
    resultats: Sequence[ResultatCandidat], seuils: SeuilsDecision | None = None
) -> list[DecisionCandidat]:
    """Décide sur un lot de candidats, dans l'ordre reçu (le meilleur reste à voir).

    Le tri est laissé à l'appelant : `resultats` peut contenir la référence du
    modèle actuel, qui n'est jamais proposée.
    """
    if not resultats:
        raise ValidationError("aucun résultat de candidat à décider")
    return [decider(resultat, seuils) for resultat in resultats]


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
