"""Adaptateur du chat du plan de contrôle (`jauge.api.ports.ChatPort`).

Le port est branché sur les **agents Strands** (M7) :

- hors démonstration, la question est confiée au chef d'orchestre et à ses quatre
  spécialistes (`jauge.agents.orchestrator.creer_agents`) ;
- en démonstration, aucun modèle n'est joignable : la réponse est produite par
  les **outils déterministes** de l'agent analyste (agrégats, leviers,
  rapprochement), c'est-à-dire par les mêmes ports et les mêmes modules que
  la version agent.

Dans les deux cas, les chiffres viennent du code (ENF-IA-01) et aucun texte de
requête ne sort (ENF-CONF-01). Si l'agent échoue, la réponse déterministe est
rendue **avec le motif de l'échec** : jamais d'erreur silencieuse.

Exigences visées : EF-RAP-04, §12.1, ENF-IA-01, ENF-CONF-01.
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils
from jauge.agents.orchestrator import creer_agents
from jauge.agents.ports import Services
from jauge.agents.tools import creer_outils
from jauge.agents.traces import Traceur
from jauge.integration.socle import charger_tenant
from jauge.store.protocol import Store

#: Longueur maximale de la question recopiée dans la réponse.
LONGUEUR_QUESTION = 200


class AdaptateurChat:
    """Port de questions en langage naturel, branché sur les agents Strands."""

    def __init__(
        self,
        services: Services,
        store: Store,
        *,
        modele: Any | None = None,
        mode_demo: bool = True,
        traceur: Traceur | None = None,
    ) -> None:
        self._services = services
        self._store = store
        self._modele = modele
        self._mode_demo = mode_demo
        self._traceur = traceur or Traceur()

    def repondre(self, tenant_id: str, question: str) -> dict[str, object]:
        """Répond à une question sur les coûts du client."""
        charger_tenant(self._store, tenant_id)
        chiffres = self._chiffres(tenant_id)
        if self._mode_demo or self._modele is None:
            return {
                "reponse": self._texte(tenant_id, question, chiffres),
                "chiffres": chiffres,
                "mode": "demonstration",
            }
        return self._reponse_agent(tenant_id, question, chiffres)

    def _reponse_agent(
        self, tenant_id: str, question: str, chiffres: dict[str, object]
    ) -> dict[str, object]:
        """Interroge l'agent chef d'orchestre, avec repli déterministe explicite."""
        try:
            agents = creer_agents(
                services=self._services,
                modele=self._modele,
                tenant_id=tenant_id,
                traceur=self._traceur,
            )
            texte = agents.repondre(question, tenant_id)
        except Exception as erreur:  # le modèle est indisponible : on le dit
            return {
                "reponse": self._texte(tenant_id, question, chiffres),
                "chiffres": chiffres,
                "mode": "deterministe",
                "motif": f"Agent indisponible ({type(erreur).__name__}) : {erreur}"[:300],
            }
        return {"reponse": texte, "chiffres": chiffres, "mode": "agent"}

    def _contexte(self, tenant_id: str) -> ContexteOutils:
        """Contexte d'appel des outils : le client vient du plan de contrôle."""
        return ContexteOutils(services=self._services, tenant_id=tenant_id, traceur=self._traceur)

    def _chiffres(self, tenant_id: str) -> dict[str, object]:
        """Chiffres du client, calculés par les outils déterministes de l'agent."""
        ctx = self._contexte(tenant_id)
        outils = creer_outils(ctx)
        applications = self._store.lister_applications(tenant_id)
        couts = [_cout_application(outils, application.id) for application in applications]
        leviers = [_leviers_application(outils, application.id) for application in applications]
        rapprochement = outils["reconcile_with_bill"](jours=30)
        total = round(sum(cout["cout_total_usd"] for cout in couts), 4)
        nombre_leviers = sum(len(levier["leviers"]) for levier in leviers)
        economie = round(sum(levier["economie_totale_estimee_usd"] for levier in leviers), 2)
        principal = max(couts, key=lambda cout: cout["cout_total_usd"], default=None)
        return {
            "cout_total_usd": total,
            "nombre_requetes": sum(cout["nombre_requetes"] for cout in couts),
            "application_principale": principal["application_id"] if principal else "",
            "cout_application_principale_usd": principal["cout_total_usd"] if principal else 0.0,
            "leviers_detectes": nombre_leviers,
            "economie_mensuelle_estimee_usd": economie,
            "jours_rapproches": rapprochement["jours_rapproches"],
            "ecart_facture_pourcentage": rapprochement["ecart_pourcentage"],
            "rapprochement_conforme": rapprochement["conforme"],
        }

    def _texte(self, tenant_id: str, question: str, chiffres: dict[str, object]) -> str:
        """Réponse déterministe, rédigée à partir des seuls chiffres calculés."""
        return (
            f"Sur les 30 derniers jours, la dépense recalculée du client {tenant_id} atteint "
            f"{chiffres['cout_total_usd']} USD pour {chiffres['nombre_requetes']} requêtes. "
            f"L'application la plus coûteuse est « {chiffres['application_principale']} » "
            f"({chiffres['cout_application_principale_usd']} USD). "
            f"{chiffres['leviers_detectes']} leviers sont détectés, pour une économie mensuelle "
            f"estimée de {chiffres['economie_mensuelle_estimee_usd']} USD. "
            f"L'écart avec la facture est de {chiffres['ecart_facture_pourcentage']} % sur "
            f"{chiffres['jours_rapproches']} jours rapprochés. "
            f"Question reçue : « {question[:LONGUEUR_QUESTION]} »."
        )


def _cout_application(outils: dict[str, Any], application_id: str) -> dict[str, Any]:
    """Charge de l'application, calculée par l'outil d'agrégats de l'agent."""
    return outils["query_cost_aggregates"](application_id=application_id)


def _leviers_application(outils: dict[str, Any], application_id: str) -> dict[str, Any]:
    """Leviers de l'application, calculés par l'outil de leviers de l'agent."""
    return outils["list_levers"](application_id=application_id)
