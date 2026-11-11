"""Tests du ticket de changement pour une application hors passerelle (EF-BAS-04).

Une application qui appelle Bedrock directement ne peut pas être basculée par une
règle (§11.5) : Jauge décrit le changement dans un ticket et n'exécute rien.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.rollout.conftest import PETIT_MODELE

from jauge.common.errors import ApprobationRequiseError, CloisonnementError, ValidationError
from jauge.models.recommendation import Recommandation
from jauge.models.tenant import Application, Tenant
from jauge.rollout.ticket import (
    FakeEmetteurTicket,
    corps_du_ticket,
    generer_ticket,
    ouvrir_ticket,
)

INSTANT = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)


def test_generation_du_ticket(
    tenant: Tenant, app_hors_passerelle: Application, recommandation: Recommandation
) -> None:
    """Le ticket nomme les deux modèles, l'économie attendue et les étapes."""
    ticket = generer_ticket(tenant.id, app_hors_passerelle, recommandation, moment=INSTANT)

    assert ticket.id.startswith("ro-")
    assert ticket.tenant_id == tenant.id
    assert ticket.application_id == app_hors_passerelle.id
    assert ticket.modele_actuel == app_hors_passerelle.modele_actuel
    assert ticket.modele_cible == PETIT_MODELE
    assert ticket.economie_attendue_usd == pytest.approx(recommandation.cost.expectedMonthlySaving)
    assert datetime.fromisoformat(ticket.cree_le) == INSTANT
    assert "Résumé nocturne" in ticket.titre
    assert "Identifiant de modèle cible" in ticket.corps
    assert "Jauge ne modifie pas son code" in ticket.corps


def test_ticket_decrit_les_etapes_sans_executer(
    tenant: Tenant, app_hors_passerelle: Application, recommandation: Recommandation
) -> None:
    """Le corps décrit le changement ; rien n'est appliqué par Jauge."""
    corps = corps_du_ticket(app_hors_passerelle, recommandation)

    assert "Remplacer l'identifiant de modèle" in corps
    assert "rejouer la suite de tests" in corps
    assert recommandation.campaignId in corps
    assert "200 requêtes" in corps


def test_volume_mensuel_peut_etre_precise(
    tenant: Tenant, app_hors_passerelle: Application, recommandation: Recommandation
) -> None:
    """Un volume réel observé remplace le volume de la recommandation."""
    corps = corps_du_ticket(app_hors_passerelle, recommandation, volume_mensuel=42_000)
    assert "Volume mensuel de référence : 42000" in corps


def test_ouverture_confiee_au_systeme_de_tickets(
    tenant: Tenant, app_hors_passerelle: Application, recommandation: Recommandation
) -> None:
    """L'émetteur reçoit le ticket et renvoie sa référence externe."""
    emetteur = FakeEmetteurTicket()

    ticket, reference = ouvrir_ticket(
        emetteur, tenant.id, app_hors_passerelle, recommandation, moment=INSTANT
    )

    assert reference == "DEMO-1"
    assert emetteur.tickets == [ticket]


def test_refus_si_application_sur_la_passerelle(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Application déjà derrière la passerelle : la règle remplace le ticket."""
    with pytest.raises(ValidationError):
        generer_ticket(tenant.id, app_classement, recommandation)


def test_refus_sans_approbation(
    tenant: Tenant,
    app_hors_passerelle: Application,
    recommandation_en_attente: Recommandation,
) -> None:
    """Pas de ticket sur une recommandation non approuvée (ENF-IA-02)."""
    with pytest.raises(ApprobationRequiseError):
        generer_ticket(tenant.id, app_hors_passerelle, recommandation_en_attente)


def test_refus_autre_client(
    tenant: Tenant,
    tenant_b: Tenant,
    app_hors_passerelle: Application,
    recommandation: Recommandation,
) -> None:
    """Client B sur l'application du client A → refus (ENF-SEC-02)."""
    with pytest.raises(CloisonnementError):
        generer_ticket(tenant_b.id, app_hors_passerelle, recommandation)
