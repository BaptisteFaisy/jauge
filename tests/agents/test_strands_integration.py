"""Tests de l'intégration Strands (§12.1, §12.4).

Ces tests vérifient ce qui est réellement vérifiable sans réseau : la présence
du SDK, sa version, la construction des agents, le motif « agents comme outils »
(`Agent.as_tool`) et la session persistante du SDK. Ils sont **sautés** si le
SDK n'est pas installé : la logique des agents reste alors testable via le
repli `jauge.agents.sdk`.

Ce qui n'est **pas** vérifié ici (à confirmer en intégration, M15) : l'appel
réel à un modèle Bedrock, le déploiement sur AgentCore Runtime et l'outil
d'évaluation de Strands sur des campagnes de référence.
"""

from __future__ import annotations

import pytest

from jauge.agents import sdk
from jauge.agents.jeu_essai import CAMPAGNE
from jauge.agents.modele_factice import ModeleFactice
from jauge.agents.orchestrator import creer_agents
from jauge.agents.session import SessionCampagne

pytestmark = pytest.mark.skipif(
    not sdk.STRANDS_DISPONIBLE, reason="strands-agents n'est pas installé"
)


def test_sdk_present_et_version_connue() -> None:
    """Le SDK est importable et sa version est connue (donc pinnable)."""
    assert sdk.STRANDS_DISPONIBLE is True
    assert sdk.VERSION_STRANDS
    assert sdk.VERSION_STRANDS[0].isdigit()


def test_factory_cree_les_cinq_agents(services, client) -> None:
    """`creer_agents` construit le chef d'orchestre et les quatre spécialistes."""
    agents = creer_agents(
        services=services, modele=ModeleFactice(), tenant_id=client.id, campagne_id=CAMPAGNE
    )
    assert agents.orchestrateur is not None
    assert set(agents.specialises()) == {
        "analyste_couts",
        "planificateur_rejeu",
        "lecteur_resultats",
        "redacteur",
    }


def test_agents_comme_outils_expose_le_bon_schema(services, client) -> None:
    """§12.1 : chaque spécialiste est exposé au chef d'orchestre comme un outil."""
    agents = creer_agents(
        services=services, modele=ModeleFactice(), tenant_id=client.id, campagne_id=CAMPAGNE
    )
    outil = agents.specialises()["analyste_couts"].as_tool(
        name="analyste_couts", description="Analyste des coûts"
    )
    spec = outil.tool_spec
    assert spec["name"] == "analyste_couts"
    assert spec["description"] == "Analyste des coûts"
    assert "input" in spec["inputSchema"]["json"]["properties"]


def test_session_du_sdk_par_campagne(tmp_path) -> None:
    """§12.4 : la session Strands est indexée par identifiant de campagne."""
    session = SessionCampagne.pour_la_campagne(CAMPAGNE, "t-demo-a", "ticket-classifier")
    gestionnaire = session.gestionnaire_session(repertoire=str(tmp_path))
    assert gestionnaire is not None
    assert gestionnaire.session_id == CAMPAGNE


def test_sortie_structuree_peut_etre_activee(services, client) -> None:
    """La sortie structurée du SDK est câblable sur chaque spécialiste (§12.3)."""
    agents = creer_agents(
        services=services,
        modele=ModeleFactice(),
        tenant_id=client.id,
        campagne_id=CAMPAGNE,
        sorties_structurees=True,
    )
    from jauge.agents.schemas import AnalyseCouts

    assert agents.analyste_couts._default_structured_output_model is AnalyseCouts
