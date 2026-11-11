"""Câblage du plan de contrôle Jauge : les ports des agents sur les modules réels.

Ce paquet ne réécrit **aucun** calcul : il branche les protocoles de
`jauge.agents.ports` sur les modules livrés par les autres lots du chantier
(attribution M1, tarifs M2, leviers M3, rejeu M4, évaluation M5, bascule M6,
agents M7, API M8, rapports M9).

Point d'entrée :

- `jauge.integration.factory.creer_services(store, ...)` assemble les douze
  ports sur un `Store` ;
- `jauge.integration.factory.creer_app(mode_demo=True)` renvoie l'application
  FastAPI du plan de contrôle, branchée sur ces services.

Exigences visées : §12, §15, ENF-SEC-02 (cloisonnement), ENF-CONF-01 (aucun
texte de requête hors du plan de données), ENF-IA-01 (les chiffres viennent du
code), ENF-COUT-01 (plafond des campagnes).
"""

from __future__ import annotations

from jauge.integration.api_ports import AdaptateurCampagnes, AdaptateurRapports
from jauge.integration.attribution import ServiceAttribution
from jauge.integration.candidats import AdaptateurCandidats
from jauge.integration.chat import AdaptateurChat
from jauge.integration.client_demo import ClientModeleDemo, juge_demonstration
from jauge.integration.couts import AdaptateurAgregats, AdaptateurRapprochement
from jauge.integration.estimation import AdaptateurEstimation
from jauge.integration.factory import Clients, creer_app, creer_services
from jauge.integration.lecture_journaux import (
    construire_plan_de_donnees,
    remplir_plan_de_donnees,
)
from jauge.integration.leviers import AdaptateurLeviers, ServiceDetectionLeviers
from jauge.integration.plan_de_donnees import PlanDeDonnees, RequeteRejouable
from jauge.integration.profil import AdaptateurProfilTache, construire_contexte_levier
from jauge.integration.recommandation import (
    AdaptateurEnregistrement,
    AdaptateurEntrees,
    AdaptateurValidation,
)
from jauge.integration.referentiel import AdaptateurReferentiel
from jauge.integration.rejeu import AdaptateurRejeu
from jauge.integration.resultats import AdaptateurResultats

__all__ = [
    "AdaptateurAgregats",
    "AdaptateurCandidats",
    "AdaptateurCampagnes",
    "AdaptateurChat",
    "AdaptateurEntrees",
    "AdaptateurEnregistrement",
    "AdaptateurEstimation",
    "AdaptateurLeviers",
    "AdaptateurProfilTache",
    "AdaptateurRapprochement",
    "AdaptateurReferentiel",
    "AdaptateurRejeu",
    "AdaptateurRapports",
    "AdaptateurResultats",
    "AdaptateurValidation",
    "ClientModeleDemo",
    "Clients",
    "PlanDeDonnees",
    "RequeteRejouable",
    "ServiceAttribution",
    "ServiceDetectionLeviers",
    "construire_contexte_levier",
    "construire_plan_de_donnees",
    "creer_app",
    "creer_services",
    "juge_demonstration",
    "remplir_plan_de_donnees",
]
