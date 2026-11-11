"""Adaptateur des résultats de campagne (port `PortResultats`).

Les résultats consolidés sont ceux de la campagne enregistrée dans le store
(produits par le module d'évaluation M5) ; les familles en échec sont les
clusters calculés pendant la notation, conservés dans le **plan de données**
(Étiquettes de catégorie, jamais des extraits de requête — ENF-CONF-01).

Exigences visées : EF-EVA-03, §12.1, §13.6, ENF-SEC-02.
"""

from __future__ import annotations

from jauge.agents.ports import ClusterEchec, PortResultats
from jauge.integration.evaluation import charger_campagne
from jauge.integration.plan_de_donnees import PlanDeDonnees
from jauge.models.campaign import ResultatCandidat
from jauge.store.protocol import Store


class AdaptateurResultats(PortResultats):
    """Port des résultats de rejeu, branché sur le store et le plan de données."""

    def __init__(self, store: Store, plan: PlanDeDonnees) -> None:
        self._store = store
        self._plan = plan

    def resultats(self, tenant_id: str, campagne_id: str) -> list[ResultatCandidat]:
        """Résultats consolidés par candidat (scores agrégés uniquement)."""
        campagne = charger_campagne(self._store, tenant_id, campagne_id)
        return list(campagne.resultats)

    def familles_en_echec(self, tenant_id: str, campagne_id: str) -> list[ClusterEchec]:
        """Familles de requêtes où un candidat échoue."""
        charger_campagne(self._store, tenant_id, campagne_id)
        trace = self._plan.campagne(campagne_id)
        return list(trace.clusters) if trace else []
