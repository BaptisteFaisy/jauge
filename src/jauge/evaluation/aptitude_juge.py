"""Aptitude et dérive du modèle juge (Top 10, piste n° 2 — §13.5, §21).

Deux questions, deux réponses chiffrées :

1. **Aptitude** : ce juge peut-il servir de preuve ? Le verdict est `apte`,
   `à recalibrer` ou `rejeté`, calculé à partir des mesures de
   `jauge.evaluation.calibration` et de seuils explicites (`SeuilsCalibration`).
   Chaque condition est rendue avec son motif : un refus muet n'est pas
   exploitable par le responsable d'application.

2. **Dérive** : ce juge est-il encore le même qu'hier ? Les mesures sont
   suivies par version de prompt et par date (`HistoriqueCalibration`) ;
   `comparer_derive` signale toute dégradation au-delà d'un seuil. Un juge qui
   dérive **invalide les preuves passées** : les observations concernées sont
   listées pour être rejouées.

ENF-IA-01 : aucun nombre de ce module n'est produit par un modèle ; les seuils
sont des constantes de configuration, écrites ici, jamais déduites.
ENF-CONF-01 : aucun texte de requête ni de réponse ne traverse ce module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from jauge.common.errors import ValidationError
from jauge.evaluation.decision import Condition

if TYPE_CHECKING:  # import de type seulement : évite un cycle avec `calibration`
    from jauge.evaluation.calibration import MesuresCalibration


def _pourcentage(valeur: float) -> str:
    """Formate un taux en pourcentage à un dixième (lecture humaine)."""
    return f"{valeur * 100:.1f} %"


def _verifier_seuils(seuils: object) -> None:
    """Refuse un seuil hors de [0, 1] : erreur de configuration, pas de mesure."""
    for nom, valeur in vars(seuils).items():
        if not 0.0 <= valeur <= 1.0:
            raise ValidationError("seuil de calibration hors de [0, 1]", seuil=nom, valeur=valeur)


@dataclass(frozen=True)
class SeuilsCalibration:
    """Seuils d'aptitude d'un juge. Choisis ici, appliqués par le code.

    Lecture : `accord_min` et `accord_pieges_min` sont des planchers ; les
    `*_max` sont des plafonds. Les trois seuils de rejet (`accord_rejet`,
    `faux_accept_rejet`, `accord_pieges_rejet`) sont des **planchers durs** :
    en dessous, le juge n'est pas recalibrable, il est rejeté — ses verdicts ont
    servi à valider des candidats (§13.4).

    Pourquoi ces valeurs : un juge qui laisse passer plus de 5 % de réponses non
    équivalentes gonfle le taux d'équivalence d'un candidat au-delà de la marge
    de décision (§13.4) ; au-delà de 20 %, la preuve de qualité ne vaut plus
    rien. Le biais de position est toléré jusqu'à 10 % des paires : en dessous,
    l'ordre de présentation reste un bruit ; au-delà, il décide.
    """

    accord_min: float = 0.90
    faux_accept_max: float = 0.05
    faux_rejet_max: float = 0.10
    biais_position_max: float = 0.10
    biais_verbosite_max: float = 0.15
    biais_format_max: float = 0.15
    accord_pieges_min: float = 0.95
    accord_rejet: float = 0.70
    faux_accept_rejet: float = 0.20
    accord_pieges_rejet: float = 0.80

    def __post_init__(self) -> None:
        _verifier_seuils(self)


class Aptitude(StrEnum):
    """Verdict d'aptitude d'un juge, à la sortie de la calibration."""

    APTE = "apte"
    A_RECALIBRER = "a_recalibrer"
    REJETE = "rejete"


@dataclass(frozen=True)
class VerdictAptitude:
    """Verdict motivé : l'aptitude, ses conditions et ses motifs."""

    aptitude: Aptitude
    conditions: tuple[Condition, ...]
    motifs: tuple[str, ...]
    seuils: SeuilsCalibration


def _conditions_aptitude(
    mesures: MesuresCalibration, seuils: SeuilsCalibration
) -> tuple[Condition, ...]:
    """Conditions d'aptitude : une par mesure du jeu de calibration."""
    return (
        Condition(
            "accord_gold",
            mesures.accord_gold.taux >= seuils.accord_min,
            f"accord avec la vérité {_pourcentage(mesures.accord_gold.taux)} "
            f"(exige ≥ {_pourcentage(seuils.accord_min)})",
        ),
        Condition(
            "faux_accept",
            mesures.faux_accept.taux <= seuils.faux_accept_max,
            f"faux-accept {_pourcentage(mesures.faux_accept.taux)} "
            f"(exige ≤ {_pourcentage(seuils.faux_accept_max)})",
        ),
        Condition(
            "faux_rejet",
            mesures.faux_rejet.taux <= seuils.faux_rejet_max,
            f"faux-rejet {_pourcentage(mesures.faux_rejet.taux)} "
            f"(exige ≤ {_pourcentage(seuils.faux_rejet_max)})",
        ),
        Condition(
            "biais_position",
            mesures.biais_position <= seuils.biais_position_max,
            f"biais de position {_pourcentage(mesures.biais_position)} "
            f"(exige ≤ {_pourcentage(seuils.biais_position_max)})",
        ),
        Condition(
            "biais_verbosite",
            mesures.biais_verbosite <= seuils.biais_verbosite_max,
            f"biais de verbosité {_pourcentage(mesures.biais_verbosite)} "
            f"(exige ≤ {_pourcentage(seuils.biais_verbosite_max)})",
        ),
        Condition(
            "biais_format",
            mesures.biais_format <= seuils.biais_format_max,
            f"biais de format {_pourcentage(mesures.biais_format)} "
            f"(exige ≤ {_pourcentage(seuils.biais_format_max)})",
        ),
        Condition(
            "accord_pieges_dores",
            mesures.accord_pieges_dores.taux >= seuils.accord_pieges_min,
            f"accord sur les pièges dorés {_pourcentage(mesures.accord_pieges_dores.taux)} "
            f"(exige ≥ {_pourcentage(seuils.accord_pieges_min)})",
        ),
    )


def _conditions_rejet(
    mesures: MesuresCalibration, seuils: SeuilsCalibration
) -> tuple[Condition, ...]:
    """Conditions de rejet : sous ces planchers durs, le juge est rejeté."""
    return (
        Condition(
            "accord_gold_rejet",
            mesures.accord_gold.taux >= seuils.accord_rejet,
            f"accord avec la vérité {_pourcentage(mesures.accord_gold.taux)} "
            f"(plancher de rejet {_pourcentage(seuils.accord_rejet)})",
        ),
        Condition(
            "faux_accept_rejet",
            mesures.faux_accept.taux <= seuils.faux_accept_rejet,
            f"faux-accept {_pourcentage(mesures.faux_accept.taux)} "
            f"(plafond de rejet {_pourcentage(seuils.faux_accept_rejet)})",
        ),
        Condition(
            "accord_pieges_dores_rejet",
            mesures.accord_pieges_dores.taux >= seuils.accord_pieges_rejet,
            f"accord sur les pièges dorés {_pourcentage(mesures.accord_pieges_dores.taux)} "
            f"(plancher de rejet {_pourcentage(seuils.accord_pieges_rejet)})",
        ),
    )


def juger_aptitude(
    mesures: MesuresCalibration, seuils: SeuilsCalibration | None = None
) -> VerdictAptitude:
    """Verdict d'aptitude du juge, motifs à l'appui.

    Le rejet est prioritaire : un juge qui laisse passer une réponse fausse
    au-delà du plafond de rejet a déjà servi à valider des candidats, et le
    signaler passe avant toute tentative de recalibration.
    """
    seuils = seuils or SeuilsCalibration()
    conditions = _conditions_aptitude(mesures, seuils)
    conditions_rejet = _conditions_rejet(mesures, seuils)
    toutes = (*conditions_rejet, *conditions)
    if not all(condition.remplie for condition in conditions_rejet):
        aptitude = Aptitude.REJETE
    elif not all(condition.remplie for condition in conditions):
        aptitude = Aptitude.A_RECALIBRER
    else:
        aptitude = Aptitude.APTE
    motifs = tuple(c.detail for c in toutes if not c.remplie)
    return VerdictAptitude(aptitude=aptitude, conditions=toutes, motifs=motifs, seuils=seuils)


@dataclass(frozen=True)
class ObservationCalibration:
    """Une mesure de calibration, datée et rattachée à une version de prompt."""

    version_prompt: str
    jour: str
    juge_id: str
    mesures: MesuresCalibration
    aptitude: Aptitude


class HistoriqueCalibration:
    """Historique des mesures d'un juge, par version de prompt et par date.

    C'est ce qui permet de voir une **dérive** : un juge qui change de
    comportement invalide les preuves produites par sa version précédente.
    """

    def __init__(self, juge_id: str) -> None:
        self.juge_id = juge_id
        self._observations: list[ObservationCalibration] = []

    def ajouter(self, observation: ObservationCalibration) -> None:
        """Ajoute une observation (le juge doit être le même)."""
        if observation.juge_id != self.juge_id:
            raise ValidationError(
                "observation d'un autre juge", attendu=self.juge_id, recu=observation.juge_id
            )
        self._observations.append(observation)

    def observations(self) -> tuple[ObservationCalibration, ...]:
        """Observations triées par date puis par version de prompt."""
        return tuple(sorted(self._observations, key=lambda o: (o.jour, o.version_prompt)))

    def reference(self) -> ObservationCalibration:
        """Première observation : la référence de comparaison."""
        observations = self.observations()
        if not observations:
            raise ValidationError("historique de calibration vide : aucune référence")
        return observations[0]

    def derniere(self) -> ObservationCalibration:
        """Dernière observation : l'état courant du juge."""
        observations = self.observations()
        if not observations:
            raise ValidationError("historique de calibration vide : aucune mesure courante")
        return observations[-1]

    def __len__(self) -> int:
        """Nombre d'observations enregistrées."""
        return len(self._observations)


@dataclass(frozen=True)
class SeuilsDerive:
    """Seuils de dérive : dégradation tolérée d'une version du juge à l'autre."""

    accord_max: float = 0.05
    faux_accept_max: float = 0.03
    faux_rejet_max: float = 0.05
    accord_pieges_max: float = 0.05
    biais_position_max: float = 0.05
    biais_verbosite_max: float = 0.05
    biais_format_max: float = 0.05

    def __post_init__(self) -> None:
        _verifier_seuils(self)


@dataclass(frozen=True)
class EcartDerive:
    """Écart entre deux versions d'un juge, pour une mesure."""

    nom: str
    avant: float
    apres: float
    derive: float
    seuil: float
    depasse: bool

    @property
    def motif(self) -> str:
        """Phrase expliquant l'écart (citable dans un rapport)."""
        return (
            f"{self.nom} : {_pourcentage(self.avant)} → {_pourcentage(self.apres)} "
            f"(dérive {_pourcentage(self.derive)} > seuil {_pourcentage(self.seuil)})"
        )


@dataclass(frozen=True)
class RapportDerive:
    """Rapport de dérive entre la référence et l'état courant du juge."""

    derive: bool
    prompt_modifie: bool
    version_reference: str
    version_courante: str
    jour_reference: str
    jour_courant: str
    ecarts: tuple[EcartDerive, ...]
    observations_concernees: tuple[str, ...]
    motifs: tuple[str, ...]


def _derive(sens: str, avant: float, apres: float) -> float:
    """Quantité de dégradation entre deux mesures (positive = dégradation)."""
    if sens == "baisse":
        return avant - apres
    if sens == "hausse":
        return apres - avant
    return abs(apres) - abs(avant)


def _definitions_taux(
    avant: MesuresCalibration, apres: MesuresCalibration, seuils: SeuilsDerive
) -> tuple[tuple[str, float, float, float, str], ...]:
    """Mesures suivies en taux : (nom, avant, après, seuil, sens de dérive)."""
    return (
        (
            "accord_gold",
            avant.accord_gold.taux,
            apres.accord_gold.taux,
            seuils.accord_max,
            "baisse",
        ),
        (
            "faux_accept",
            avant.faux_accept.taux,
            apres.faux_accept.taux,
            seuils.faux_accept_max,
            "hausse",
        ),
        (
            "faux_rejet",
            avant.faux_rejet.taux,
            apres.faux_rejet.taux,
            seuils.faux_rejet_max,
            "hausse",
        ),
        (
            "accord_pieges_dores",
            avant.accord_pieges_dores.taux,
            apres.accord_pieges_dores.taux,
            seuils.accord_pieges_max,
            "baisse",
        ),
    )


def _definitions_biais(
    avant: MesuresCalibration, apres: MesuresCalibration, seuils: SeuilsDerive
) -> tuple[tuple[str, float, float, float, str], ...]:
    """Mesures suivies en biais : la dérive est la croissance de la magnitude."""
    biais = (
        ("biais_position", avant.biais_position, apres.biais_position, seuils.biais_position_max),
        (
            "biais_verbosite",
            avant.biais_verbosite,
            apres.biais_verbosite,
            seuils.biais_verbosite_max,
        ),
        ("biais_format", avant.biais_format, apres.biais_format, seuils.biais_format_max),
    )
    return tuple(
        (nom, valeur_avant, valeur_apres, seuil, "magnitude")
        for nom, valeur_avant, valeur_apres, seuil in biais
    )


def _definitions_derive(
    avant: MesuresCalibration, apres: MesuresCalibration, seuils: SeuilsDerive
) -> tuple[tuple[str, float, float, float, str], ...]:
    """Table des mesures suivies : (nom, avant, après, seuil, sens)."""
    return (*_definitions_taux(avant, apres, seuils), *_definitions_biais(avant, apres, seuils))


def _ecarts_derive(
    avant: MesuresCalibration, apres: MesuresCalibration, seuils: SeuilsDerive
) -> tuple[EcartDerive, ...]:
    """Écarts de dérive, un par mesure suivie.

    La dérive est **une dégradation**, pas un simple changement : un accord qui
    progresse n'est pas une dérive, un faux-accept qui recule non plus. Pour les
    biais, c'est la croissance de la valeur absolue qui compte.
    """
    ecarts: list[EcartDerive] = []
    for nom, valeur_avant, valeur_apres, seuil, sens in _definitions_derive(avant, apres, seuils):
        derive = _derive(sens, valeur_avant, valeur_apres)
        ecarts.append(
            EcartDerive(
                nom=nom,
                avant=valeur_avant,
                apres=valeur_apres,
                derive=round(derive, 6),
                seuil=seuil,
                depasse=derive > seuil,
            )
        )
    return tuple(ecarts)


def comparer_derive(
    historique: HistoriqueCalibration, seuils: SeuilsDerive | None = None
) -> RapportDerive:
    """Compare l'état courant du juge à sa référence et signale la dérive.

    Une version de prompt différente est signalée séparément : elle n'est pas
    une dérive en soi, mais elle **invalide** les preuves produites par la
    version précédente, qui sont listées dans `observations_concernees`.
    """
    seuils = seuils or SeuilsDerive()
    reference = historique.reference()
    courante = historique.derniere()
    ecarts = _ecarts_derive(reference.mesures, courante.mesures, seuils)
    derive = any(ecart.depasse for ecart in ecarts)
    prompt_modifie = reference.version_prompt != courante.version_prompt
    concernees = tuple(
        f"{observation.version_prompt}@{observation.jour}"
        for observation in historique.observations()[:-1]
    )
    motifs = tuple(ecart.motif for ecart in ecarts if ecart.depasse)
    if prompt_modifie:
        motifs = (
            *motifs,
            f"version de prompt modifiée : {reference.version_prompt} → {courante.version_prompt}",
        )
    return RapportDerive(
        derive=derive,
        prompt_modifie=prompt_modifie,
        version_reference=reference.version_prompt,
        version_courante=courante.version_prompt,
        jour_reference=reference.jour,
        jour_courant=courante.jour,
        ecarts=ecarts,
        observations_concernees=concernees,
        motifs=motifs,
    )
