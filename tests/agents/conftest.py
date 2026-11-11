"""Fixtures des tests des agents (§12) — aucun appel réseau, aucun AWS.

Les fixtures du socle (`tenant`, `tenant_b`, `app_classement`, `app_assistant`)
sont réutilisées telles quelles ; ce fichier ne fait que brancher le jeu d'essai
partagé (`jauge.agents.jeu_essai`) sur les fixtures du socle, avec des
identifiants de modèles distincts pour que référence et candidats ne se
confondent pas.
"""

from __future__ import annotations

import pytest

from jauge.agents.contexte import ContexteOutils
from jauge.agents.jeu_essai import (
    CAMPAGNE,
    MODELE_GRAND,
    MODELE_MOYEN,
    MODELE_PETIT,
    jeu_demo,
    tarifs_demo,
)
from jauge.agents.session import SessionCampagne
from jauge.agents.traces import Traceur
from jauge.models.tenant import Application, Tenant

__all__ = [
    "CAMPAGNE",
    "MODELE_GRAND",
    "MODELE_MOYEN",
    "MODELE_PETIT",
    "application",
    "client",
    "contexte",
    "services",
    "session",
    "table_tarifs",
    "traceur",
]


@pytest.fixture
def client(tenant: Tenant) -> Tenant:
    """Client de démonstration, avec les trois modèles de la démo autorisés."""
    return tenant.model_copy(
        update={"modeles_autorises": [MODELE_GRAND, MODELE_MOYEN, MODELE_PETIT]}
    )


@pytest.fixture
def application(app_classement: Application, client: Tenant) -> Application:
    """Application de classement de tickets, servie par le grand modèle."""
    return app_classement.model_copy(update={"tenant_id": client.id, "modele_actuel": MODELE_GRAND})


@pytest.fixture
def table_tarifs() -> list:
    """Table datée des trois modèles de la démo (Standard et Flex)."""
    return tarifs_demo()


@pytest.fixture
def jeu(client: Tenant, application: Application, app_assistant: Application):
    """Jeu d'essai complet (services fictifs), branché sur les fixtures du socle."""
    assistante = app_assistant.model_copy(
        update={"tenant_id": client.id, "modele_actuel": MODELE_GRAND}
    )
    return jeu_demo(client, [application, assistante])


@pytest.fixture
def services(jeu):
    """Les douze ports assemblés en `Services`, tels que les agents les reçoivent."""
    return jeu.vers_services()


@pytest.fixture
def traceur() -> Traceur:
    """Collecteur de traces d'un test."""
    return Traceur()


@pytest.fixture
def session(client: Tenant, application: Application) -> SessionCampagne:
    """Session persistante de la campagne de rejeu (§12.4)."""
    return SessionCampagne.pour_la_campagne(CAMPAGNE, client.id, application.id)


@pytest.fixture
def contexte(services, client: Tenant, traceur: Traceur, session: SessionCampagne):
    """Contexte d'appel des outils : client A, campagne c-0001, traces, session."""
    return ContexteOutils(
        services=services,
        tenant_id=client.id,
        campagne_id=CAMPAGNE,
        traceur=traceur,
        session=session,
    )
