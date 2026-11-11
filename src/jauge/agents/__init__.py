"""Agents Strands de Jauge (§12) : orchestrateur et quatre spécialistes.

Point d'entrée du paquet :

- `creer_agents(services, modele, tenant_id, ...)` — factory unique (motif
  « agents comme outils » du §12.1) ;
- `Services` et les protocoles de `jauge.agents.ports` — ce dont les agents ont
  besoin, sans dépendre des modules M1/M3/M4/M5/M6 ;
- `ServicesFictifs` — jeu d'essai sans réseau pour les tests et la démo ;
- `Traceur` et `SessionCampagne` — traces (ENF-OBS-01) et sessions par campagne
  (§12.4).

Les garde-fous du §12.3 (plafond, candidats autorisés, validation humaine) et la
validation de schéma sont appliqués par le code, jamais délégués au modèle.
"""

from __future__ import annotations

from jauge.agents.contexte import ContexteOutils
from jauge.agents.fakes import ServicesFictifs
from jauge.agents.orchestrator import AgentsJauge, creer_agents
from jauge.agents.ports import (
    EntreesRecommandation,
    EstimationCampagne,
    ModeleCandidat,
    ProfilTache,
    Services,
)
from jauge.agents.schemas import (
    AnalyseCouts,
    LectureResultats,
    PlanRejeu,
    RedactionRecommandation,
    composer_recommandation,
    valider_recommandation,
    valider_sortie,
)
from jauge.agents.session import SessionCampagne
from jauge.agents.tools import TOUS_LES_OUTILS, creer_outils, outils_pour
from jauge.agents.traces import TraceExecution, Traceur

__all__ = [
    "TOUS_LES_OUTILS",
    "AgentsJauge",
    "AnalyseCouts",
    "ContexteOutils",
    "EntreesRecommandation",
    "EstimationCampagne",
    "LectureResultats",
    "ModeleCandidat",
    "PlanRejeu",
    "ProfilTache",
    "RedactionRecommandation",
    "Services",
    "ServicesFictifs",
    "SessionCampagne",
    "TraceExecution",
    "Traceur",
    "composer_recommandation",
    "creer_agents",
    "creer_outils",
    "outils_pour",
    "valider_recommandation",
    "valider_sortie",
]
