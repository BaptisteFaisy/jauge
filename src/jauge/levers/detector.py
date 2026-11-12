"""Détection des leviers d'économie — exécution des règles et classement.

Exigences visées : EF-LEV-01 à EF-LEV-06 (§9.2), Annexe A.

Le détecteur applique toutes les règles à un `ContexteLevier` et classe les
leviers trouvés par **économie décroissante** (EF-LEV-06). Une règle qui ne
trouve pas son signal ne produit rien : l'absence de signal n'est pas un
levier.
"""

from __future__ import annotations

from collections.abc import Sequence

from jauge.levers.base import ContexteLevier, Regle
from jauge.levers.cache import RegleCachePromptsAbsent
from jauge.levers.deferrable import RegleTraitementDifferable
from jauge.levers.errors_retries import RegleErreursEtRelances
from jauge.levers.long_outputs import RegleSortiesTropLongues
from jauge.levers.oversized import RegleModeleSurdimensionne
from jauge.levers.priority import ReglePrioritySansBesoin
from jauge.levers.repeated import RegleRequetesRepetees
from jauge.levers.token_diet import RegleDieteJetons
from jauge.models.lever import Levier

# Huit règles par défaut : les sept de l'Annexe A (quatre « M » indispensables,
# trois « S »), plus la diète de jetons (top 10, piste 8) qui comprime le prompt
# au lieu de changer de modèle.
REGLES_DEFAUT: tuple[Regle, ...] = (
    RegleModeleSurdimensionne(),
    RegleCachePromptsAbsent(),
    RegleTraitementDifferable(),
    RegleSortiesTropLongues(),
    RegleRequetesRepetees(),
    RegleErreursEtRelances(),
    ReglePrioritySansBesoin(),
    RegleDieteJetons(),
)


def classer_par_economie(leviers: Sequence[Levier]) -> list[Levier]:
    """Classe les leviers par économie mensuelle décroissante (EF-LEV-06).

    Le tri est stable et déterministe : à économie égale, les types de leviers
    sont rangés par ordre alphabétique.
    """
    return sorted(
        leviers,
        key=lambda levier: (-levier.economie_mensuelle_estimee_usd, levier.type_levier.value),
    )


class DetectorLeviers:
    """Exécute un ensemble de règles sur un contexte et classe le résultat."""

    def __init__(self, regles: Sequence[Regle] | None = None) -> None:
        self.regles: list[Regle] = list(regles) if regles is not None else list(REGLES_DEFAUT)

    def detecter(self, contexte: ContexteLevier) -> list[Levier]:
        """Applique chaque règle et renvoie les leviers classés par économie décroissante."""
        trouves: list[Levier] = []
        for regle in self.regles:
            levier = regle.detecter(contexte)
            if levier is not None:
                trouves.append(levier)
        return classer_par_economie(trouves)


def detecter_leviers(contexte: ContexteLevier) -> list[Levier]:
    """Détection complète d'une application avec les huit règles par défaut."""
    return DetectorLeviers().detecter(contexte)
