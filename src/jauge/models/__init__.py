"""Paquet `models` : le vocabulaire partagé de tout Jauge.

Ces modèles pydantic sont le **contrat** entre modules. Aucun module ne définit
ses propres entités métier : on importe ici. Toute évolution passe par une PR
qui met à jour ce paquet et les tests associés.
"""

from __future__ import annotations

from jauge.models.campaign import (
    Campagne,
    EchantillonRequete,
    PaireRejoueeDataPlane,
    ParametresRejeu,
    ResultatCandidat,
    StatutCampagne,
)
from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout
from jauge.models.lever import Levier, StatutLevier, TypeLevier
from jauge.models.pricing import (
    CoutRequete,
    MetriquesRequete,
    ServiceTier,
    TarifModele,
)
from jauge.models.recommendation import (
    BasculeProgressive,
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
    VerificationEconomie,
)
from jauge.models.tenant import Application, FormuleOffre, SchemaAttribution, Tenant

__all__ = [
    "AgregatQuotidien",
    "Application",
    "BasculeProgressive",
    "Campagne",
    "CoutRequete",
    "EchantillonRequete",
    "FormuleOffre",
    "Levier",
    "MetriquesCout",
    "MetriquesLatence",
    "MetriquesQualite",
    "MetriquesRequete",
    "PaireRejoueeDataPlane",
    "ParametresRejeu",
    "RapprochementJour",
    "Recommandation",
    "ResultatCandidat",
    "SchemaAttribution",
    "ServiceTier",
    "StatutCampagne",
    "StatutLevier",
    "StatutRecommandation",
    "SyntheseCout",
    "TarifModele",
    "Tenant",
    "TypeLevier",
    "VerificationEconomie",
]
