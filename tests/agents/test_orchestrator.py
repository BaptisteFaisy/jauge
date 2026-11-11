"""Tests du chef d'orchestre et des quatre spécialistes (§12.1, §12.2).

Le modèle est un `ModeleFactice` : il rejoue un scénario scripté, ce qui permet
de vérifier, sans réseau, que le chef d'orchestre choisit le bon agent pour une
intention donnée et que chaque spécialiste appelle ses propres outils.
"""

from __future__ import annotations

from typing import Any

import pytest

from jauge.agents.jeu_essai import CAMPAGNE, MODELE_MOYEN
from jauge.agents.modele_factice import ModeleFactice
from jauge.agents.orchestrator import NOM as NOM_ORCHESTRATEUR
from jauge.agents.orchestrator import creer_agents
from jauge.agents.tools import (
    OUTILS_ANALYSTE,
    OUTILS_LECTEUR,
    OUTILS_PLANIFICATEUR,
    OUTILS_REDACTEUR,
)
from jauge.models.tenant import Application, Tenant

SPECIALISTES = ("analyste_couts", "planificateur_rejeu", "lecteur_resultats", "redacteur")


def _modele(agent: str, outil: str, arguments: dict[str, Any], texte: str = "Fait."):
    """Modèle factice d'un agent : un appel d'outil, puis une réponse texte."""
    return ModeleFactice([[{"outil": (outil, arguments)}], [{"texte": texte}]])


@pytest.fixture
def equipe(services, client: Tenant, application: Application):
    """Équipe d'agents dont tous les modèles sont scriptés par le test."""

    def _creer(modeles: dict[str, ModeleFactice]):
        return creer_agents(
            services=services,
            modele=modeles,
            tenant_id=client.id,
            campagne_id=CAMPAGNE,
        )

    return _creer


def test_orchestrateur_n_a_que_les_quatre_agents(equipe) -> None:
    """§12.1 : les outils du chef d'orchestre sont les agents, pas des outils de données."""
    agents = equipe({})
    assert agents.orchestrateur.tool_names == list(SPECIALISTES)
    assert set(agents.specialises()) == set(SPECIALISTES)


def test_chaque_specialiste_a_ses_outils(equipe) -> None:
    """Chaque spécialiste ne détient que les outils de son rôle (§12.1)."""
    agents = equipe({})
    assert agents.analyste_couts.tool_names == list(OUTILS_ANALYSTE)
    assert agents.planificateur_rejeu.tool_names == list(OUTILS_PLANIFICATEUR)
    assert agents.lecteur_resultats.tool_names == list(OUTILS_LECTEUR)
    assert agents.redacteur.tool_names == list(OUTILS_REDACTEUR)


@pytest.mark.parametrize(
    ("intention", "specialiste", "outil", "arguments"),
    [
        (
            "Combien a coûté l'application ticket-classifier ce mois-ci ?",
            "analyste_couts",
            "query_cost_aggregates",
            {"application_id": "ticket-classifier"},
        ),
        (
            "Quels leviers d'économie reste-t-il sur ticket-classifier ?",
            "analyste_couts",
            "list_levers",
            {"application_id": "ticket-classifier"},
        ),
        (
            "Peut-on tester un modèle moins cher sur ticket-classifier ?",
            "planificateur_rejeu",
            "list_candidate_models",
            {"application_id": "ticket-classifier"},
        ),
        (
            "Quels sont les résultats du rejeu c-0001 ?",
            "lecteur_resultats",
            "get_replay_results",
            {"campagne_id": CAMPAGNE},
        ),
        (
            "Rédige la recommandation de la campagne c-0001.",
            "redacteur",
            "get_recommendation_inputs",
            {"campagne_id": CAMPAGNE},
        ),
    ],
)
def test_le_chef_d_orchestre_choisit_le_bon_agent(
    equipe, intention: str, specialiste: str, outil: str, arguments: dict[str, Any]
) -> None:
    """Pour chaque intention, l'orchestrateur délègue au spécialiste attendu."""
    modele_specialiste = _modele(specialiste, outil, arguments)
    modele_orchestrateur = ModeleFactice(
        [[{"outil": (specialiste, {"input": intention})}], [{"texte": "Voici la réponse."}]]
    )
    agents = equipe({NOM_ORCHESTRATEUR: modele_orchestrateur, specialiste: modele_specialiste})
    agents.invoquer(intention, tenant_id="t-demo-a")
    assert modele_orchestrateur.outils_demandes == [specialiste]
    assert modele_specialiste.outils_demandes == [outil]


def test_analyste_repond_avec_les_agregats(equipe, services) -> None:
    """L'analyste interroge les agrégats : le service est réellement sollicité."""
    modele = _modele(
        "analyste_couts",
        "query_cost_aggregates",
        {"application_id": "ticket-classifier", "jours": 10},
    )
    agents = equipe({"analyste_couts": modele})
    agents.analyste_couts("Coût de ticket-classifier sur 10 jours ?")
    assert modele.outils_demandes == ["query_cost_aggregates"]
    assert modele.appels == 2
    # Le résultat de l'outil est bien revenu au modèle (agrégats, pas de texte).
    assert "cout_total_usd" in str(agents.analyste_couts.messages)


def test_planificateur_enchaine_profil_puis_candidats(equipe) -> None:
    """Le planificateur lit le profil, puis les candidats autorisés."""
    modele = ModeleFactice(
        [
            [{"outil": ("get_task_profile", {"application_id": "ticket-classifier"})}],
            [{"outil": ("list_candidate_models", {"application_id": "ticket-classifier"})}],
            [{"texte": "Deux candidats autorisés."}],
        ]
    )
    agents = equipe({"planificateur_rejeu": modele})
    agents.planificateur_rejeu("Que peut-on tester sur ticket-classifier ?")
    assert modele.outils_demandes == ["get_task_profile", "list_candidate_models"]


def test_lecteur_compare_au_modele_actuel(equipe) -> None:
    """Le lecteur lit les résultats et les familles en échec, dans cet ordre."""
    modele = ModeleFactice(
        [
            [{"outil": ("get_replay_results", {"campagne_id": CAMPAGNE})}],
            [{"outil": ("get_failure_clusters", {"campagne_id": CAMPAGNE})}],
            [{"texte": "Le candidat moyen respecte les seuils."}],
        ]
    )
    agents = equipe({"lecteur_resultats": modele})
    agents.lecteur_resultats("Résultats de c-0001 ?")
    assert modele.outils_demandes == ["get_replay_results", "get_failure_clusters"]


def test_redacteur_soumet_et_s_arrete_sur_la_validation(equipe, jeu) -> None:
    """Le rédacteur soumet la recommandation : elle attend l'accord d'un humain."""
    modele = ModeleFactice(
        [
            [{"outil": ("get_recommendation_inputs", {"campagne_id": CAMPAGNE})}],
            [
                {
                    "outil": (
                        "save_recommendation",
                        {
                            "campagne_id": CAMPAGNE,
                            "risques": ["Tickets en langue rare : 4 échecs sur 5"],
                        },
                    )
                }
            ],
            [{"texte": "Recommandation soumise pour validation."}],
        ]
    )
    agents = equipe({"redacteur": modele})
    agents.redacteur("Rédige la recommandation de c-0001.")
    assert modele.outils_demandes == ["get_recommendation_inputs", "save_recommendation"]
    assert not jeu.recommandations
    assert len(jeu.demandes_validation) == 1


def _planificateur_complet() -> ModeleFactice:
    """Planificateur scripté : estimation du coût, puis lancement du rejeu."""
    return ModeleFactice(
        [
            [
                {
                    "outil": (
                        "estimate_campaign_cost",
                        {
                            "application_id": "ticket-classifier",
                            "modeles_candidats": [MODELE_MOYEN],
                        },
                    )
                }
            ],
            [
                {
                    "outil": (
                        "start_replay",
                        {
                            "application_id": "ticket-classifier",
                            "modeles_candidats": [MODELE_MOYEN],
                            "taille_echantillon": 200,
                        },
                    )
                }
            ],
            [{"texte": "Rejeu lancé."}],
        ]
    )


def _redacteur_complet() -> ModeleFactice:
    """Rédacteur scripté : soumission de la recommandation, puis arrêt."""
    return ModeleFactice(
        [
            [
                {
                    "outil": (
                        "save_recommendation",
                        {
                            "campagne_id": CAMPAGNE,
                            "risques": ["Tickets en langue rare : 4 échecs sur 5"],
                        },
                    )
                }
            ],
            [{"texte": "Recommandation soumise."}],
        ]
    )


def test_campagne_complete_jusqu_a_la_recommandation(equipe, jeu, client: Tenant) -> None:
    """Une campagne entière : coûts, planification, résultats, rédaction, validation."""
    orchestration = ModeleFactice(
        [
            [{"outil": ("analyste_couts", {"input": "Coût de ticket-classifier ?"})}],
            [{"outil": ("planificateur_rejeu", {"input": "Quels candidats tester ?"})}],
            [{"outil": ("lecteur_resultats", {"input": "Résultats de c-0001 ?"})}],
            [{"outil": ("redacteur", {"input": "Rédige la recommandation."})}],
            [{"texte": "Campagne terminée, recommandation soumise."}],
        ]
    )
    agents = equipe(
        {
            NOM_ORCHESTRATEUR: orchestration,
            "analyste_couts": _modele(
                "analyste_couts",
                "query_cost_aggregates",
                {"application_id": "ticket-classifier"},
            ),
            "planificateur_rejeu": _planificateur_complet(),
            "lecteur_resultats": _modele(
                "lecteur_resultats", "get_replay_results", {"campagne_id": CAMPAGNE}
            ),
            "redacteur": _redacteur_complet(),
        }
    )
    reponse = agents.repondre("Conduis la campagne sur ticket-classifier.", tenant_id=client.id)
    assert reponse
    assert orchestration.outils_demandes == list(SPECIALISTES)
    # Une campagne de rejeu a bien été créée, et une validation humaine attend.
    assert len(jeu.campagnes) == 2
    assert len(jeu.demandes_validation) == 1
    # ENF-OBS-01 : la tâche de rejeu et l'invocation sont tracées.
    operations = [trace.operation for trace in agents.traceur.traces()]
    assert "tache_rejeu" in operations
    assert "invocation" in operations


def test_prompts_interdisent_approbation_et_calcul(equipe) -> None:
    """Les consignes des agents portent les interdits du §12.2 (défense en profondeur)."""
    agents = equipe({})
    for agent in (agents.orchestrateur, *agents.specialises().values()):
        prompt = (
            " ".join(agent.system_prompt)
            if isinstance(agent.system_prompt, list)
            else (agent.system_prompt)
        )
        assert "calcul" in prompt.lower()
    assert "bascul" in agents.orchestrateur.system_prompt.lower()
