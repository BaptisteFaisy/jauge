"""Détection des leviers d'économie d'une application (Annexe A, EF-LEV-01..06).

Point d'entrée du module : `detecter_leviers(contexte)` renvoie les leviers
donnés par les huit règles par défaut (les sept de l'Annexe A, plus la diète
de jetons), classés par économie mensuelle
décroissante.

Les règles ne lisent que les agrégats (`AgregatQuotidien`) et un profil
agrégé (`ProfilRequetes`) : jamais le texte des requêtes (ENF-CONF-01). Tous
les chiffres sont calculés par le code (ENF-IA-01).
"""

from __future__ import annotations

from jauge.levers.base import (
    ContexteLevier,
    ParametresLevier,
    ProfilRequetes,
    Regle,
    construire_levier,
)
from jauge.levers.cache import RegleCachePromptsAbsent
from jauge.levers.deferrable import RegleTraitementDifferable
from jauge.levers.detector import REGLES_DEFAUT, DetectorLeviers, detecter_leviers
from jauge.levers.errors_retries import RegleErreursEtRelances
from jauge.levers.long_outputs import RegleSortiesTropLongues
from jauge.levers.oversized import RegleModeleSurdimensionne
from jauge.levers.priority import ReglePrioritySansBesoin
from jauge.levers.repeated import RegleRequetesRepetees
from jauge.levers.token_diet import (
    RegleDieteJetons,
    dispersion_entree,
    estimer_diet_jetons,
    jetons_evites_par_requete,
    part_prefixe_dans_entree,
    prix_applicable_prefixe,
)

__all__ = [
    "REGLES_DEFAUT",
    "ContexteLevier",
    "DetectorLeviers",
    "ParametresLevier",
    "ProfilRequetes",
    "Regle",
    "RegleCachePromptsAbsent",
    "RegleDieteJetons",
    "RegleErreursEtRelances",
    "RegleModeleSurdimensionne",
    "ReglePrioritySansBesoin",
    "RegleRequetesRepetees",
    "RegleSortiesTropLongues",
    "RegleTraitementDifferable",
    "construire_levier",
    "detecter_leviers",
    "dispersion_entree",
    "estimer_diet_jetons",
    "jetons_evites_par_requete",
    "part_prefixe_dans_entree",
    "prix_applicable_prefixe",
]
