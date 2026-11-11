"""Relecture humaine d'un sous-échantillon et accord juge/humain (EF-EVA-05, §13.5).

Trois responsabilités, et rien d'autre :

1. **Tirer** un sous-échantillon reproductible de 30 paires parmi celles que le
   juge a notées (§13.5). Même graine → même sous-échantillon.
2. **Enregistrer** les verdicts du relecteur humain, un par paire.
3. **Calculer** l'accord entre le juge et l'humain, qui figure dans la
   recommandation (Annexe B, `judgeHumanAgreement`).

ENF-CONF-01 : la relecture se fait **dans le compte du client**. Ce module ne
déplace aucun texte — il ne manipule que des `requete_id` et des verdicts
booléens. Le relecteur ouvre les paires dans le portail du client ; le plan de
contrôle ne reçoit que l'identifiant de la paire et son verdict.

La relecture n'est proposée que si l'application l'autorise
(`Application.relecture_humaine_autorisee`, §13.5).
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from jauge.common.errors import ValidationError
from jauge.common.ids import graine_de
from jauge.evaluation.judge import VerdictJuge
from jauge.evaluation.scoring import PaireEvaluee, accord_juge_humain

# Taille du sous-échantillon relu par un humain (§13.5).
TAILLE_ECHANTILLON = 30


@dataclass(frozen=True)
class VerdictHumain:
    """Verdict d'un relecteur sur une paire (§13.5). Aucun texte de réponse."""

    requete_id: str
    equivalent: bool
    relecteur: str = "relecteur"
    commentaire: str = ""


@dataclass(frozen=True)
class SousEchantillon:
    """Paires proposées à la relecture : identifiants seuls, aucun texte."""

    requete_ids: tuple[str, ...]
    graine: int = 42
    taille_demandee: int = TAILLE_ECHANTILLON

    @property
    def complet(self) -> bool:
        """Vrai si l'échantillon atteint la taille visée (30 paires, §13.5)."""
        return len(self.requete_ids) >= self.taille_demandee

    def a_relire(self, verdicts: Iterable[VerdictHumain]) -> list[str]:
        """Identifiants restant à relire, dans l'ordre du tirage."""
        faits = {verdict.requete_id for verdict in verdicts}
        return [identifiant for identifiant in self.requete_ids if identifiant not in faits]


def tirer_sous_echantillon(
    paires: Sequence[PaireEvaluee],
    taille: int = TAILLE_ECHANTILLON,
    graine: int = 42,
) -> SousEchantillon:
    """Tire `taille` paires au hasard, de façon reproductible (§13.1, §13.5).

    Si l'échantillon évalué compte moins de `taille` paires, toutes sont
    proposées : mieux vaut une relecture partielle qu'aucune relecture.
    """
    if not paires:
        raise ValidationError("aucune paire évaluée : rien à relire")
    if taille <= 0:
        raise ValidationError("taille de relecture invalide", taille=taille)
    identifiants = sorted({paire.requete_id for paire in paires})
    generateur = random.Random(graine_de(graine, "relecture"))
    retenus = generateur.sample(identifiants, min(taille, len(identifiants)))
    return SousEchantillon(
        requete_ids=tuple(sorted(retenus)), graine=graine, taille_demandee=taille
    )


@dataclass
class RegistreRelecture:
    """Verdicts humains d'un sous-échantillon, et accord avec le juge (§13.5).

    `verdicts_juge` associe l'identifiant de paire au verdict du juge : c'est la
    seule information dont l'accord a besoin, et elle ne porte aucun texte.
    """

    sous_echantillon: SousEchantillon
    verdicts_juge: dict[str, VerdictJuge]
    verdicts: dict[str, VerdictHumain] = field(default_factory=dict)

    def enregistrer(self, verdict: VerdictHumain) -> None:
        """Enregistre le verdict d'un relecteur sur une paire de l'échantillon."""
        if verdict.requete_id not in self.sous_echantillon.requete_ids:
            raise ValidationError(
                "paire hors du sous-échantillon de relecture", requete_id=verdict.requete_id
            )
        if verdict.requete_id not in self.verdicts_juge:
            raise ValidationError(
                "aucun verdict du juge pour cette paire", requete_id=verdict.requete_id
            )
        self.verdicts[verdict.requete_id] = verdict

    def restant(self) -> list[str]:
        """Paires encore à relire, dans l'ordre du tirage."""
        return self.sous_echantillon.a_relire(self.verdicts.values())

    @property
    def complet(self) -> bool:
        """Vrai si chaque paire du sous-échantillon a son verdict humain."""
        return not self.restant()

    def accord(self) -> float:
        """Accord juge/humain sur les paires déjà relues (EF-EVA-05).

        Refuse de rendre un accord sur un échantillon vide : un accord sans
        donnée ne veut rien dire et ne doit pas figurer dans la recommandation.
        """
        if not self.verdicts:
            raise ValidationError("aucun verdict humain enregistré : accord non calculable")
        ordre = [
            identifiant
            for identifiant in self.sous_echantillon.requete_ids
            if identifiant in self.verdicts
        ]
        verdicts_juge = [self.verdicts_juge[identifiant] for identifiant in ordre]
        verdicts_humain = [self.verdicts[identifiant].equivalent for identifiant in ordre]
        return accord_juge_humain(verdicts_juge, verdicts_humain)


def preparer_relecture(
    paires: Sequence[PaireEvaluee],
    relecture_autorisee: bool = True,
    taille: int = TAILLE_ECHANTILLON,
    graine: int = 42,
) -> RegistreRelecture:
    """Prépare la relecture : tirage du sous-échantillon + verdicts du juge.

    Si l'application n'autorise pas la relecture (§13.5), la préparation est
    refusée explicitement plutôt que rendue vide en silence.
    """
    if not relecture_autorisee:
        raise ValidationError("la relecture humaine n'est pas autorisée pour cette application")
    sous_echantillon = tirer_sous_echantillon(paires, taille=taille, graine=graine)
    par_id = {paire.requete_id: paire for paire in paires}
    verdicts_juge = {
        identifiant: par_id[identifiant].verdict for identifiant in sous_echantillon.requete_ids
    }
    return RegistreRelecture(sous_echantillon=sous_echantillon, verdicts_juge=verdicts_juge)
