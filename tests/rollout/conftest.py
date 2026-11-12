"""Fixtures du module M6 (bascule & vérification).

Elles s'appuient sur les fixtures partagées de `tests/conftest.py` (client,
applications, tarifs) et ajoutent ce qui est propre au module : une
recommandation approuvée, une recommandation en attente, une application hors
passerelle et des agrégats avant/après.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE

from jauge.models.cost import AgregatQuotidien
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
)
from jauge.models.tenant import Application, Tenant

# Identifiant de modèle cible distinct de celui du socle : les constantes
# partagées `GRAND_MODELE`/`PETIT_MODELE` sont aujourd'hui identiques (même
# chaîne), ce qui rendrait toute assertion de routage vide de sens. Besoin de
# socle signalé en PR : trois identifiants distincts dans `tests/conftest.py`.
PETIT_MODELE = "anthropic.petit-demo"

# Valeurs de référence du scénario nominal (10 $ / 1 000 requêtes → 2 $ / 1 000).
COUT_AVANT = 0.01
COUT_APRES = 0.002
VOLUME_MENSUEL = 100_000


def construire_recommandation(
    tenant_id: str,
    application_id: str,
    statut: StatutRecommandation = StatutRecommandation.APPROUVEE,
) -> Recommandation:
    """Recommandation de démonstration, approuvée par défaut."""
    return Recommandation(
        id="r-demo-001",
        tenantId=tenant_id,
        applicationId=application_id,
        currentModel=GRAND_MODELE,
        targetModel=PETIT_MODELE,
        campaignId="c-demo-001",
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=0.97,
            confidenceIntervalLow=0.94,
            selfAgreementOfCurrentModel=0.98,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=COUT_AVANT,
            costPerRequestAfter=COUT_APRES,
            monthlyRequests=VOLUME_MENSUEL,
            expectedMonthlySaving=round((COUT_AVANT - COUT_APRES) * VOLUME_MENSUEL, 2),
        ),
        latency=MetriquesLatence(p50Before=400, p50After=300, p95Before=900, p95After=700),
        approvedBy="responsable-applicatif",
        status=statut,
    )


@pytest.fixture
def recommandation(tenant: Tenant, app_classement: Application) -> Recommandation:
    """Recommandation approuvée pour l'application de classement."""
    return construire_recommandation(tenant.id, app_classement.id)


@pytest.fixture
def recommandation_en_attente(tenant: Tenant, app_classement: Application) -> Recommandation:
    """Même recommandation, pas encore approuvée (R6 : bascule impossible)."""
    return construire_recommandation(
        tenant.id, app_classement.id, StatutRecommandation.EN_ATTENTE_APPROBATION
    )


@pytest.fixture
def app_hors_passerelle(tenant: Tenant) -> Application:
    """Application qui appelle Bedrock directement : elle reçoit un ticket."""
    return Application(
        id="resume-nuit",
        tenant_id=tenant.id,
        nom="Résumé nocturne",
        equipe="operations",
        environnement="production",
        modele_actuel=GRAND_MODELE,
        seuil_qualite=0.95,
        seuil_intervalle_bas=0.90,
        passe_par_passerelle=False,
        description_tache="Résumé de documents longs, traitement différé",
    )


def agregats(
    tenant_id: str,
    application_id: str,
    cout_par_requete: float,
    volume: int,
    jour: date = date(2026, 10, 1),
) -> list[AgregatQuotidien]:
    """Agrégats d'une journée à coût par requête constant (avant ou après)."""
    return [
        AgregatQuotidien(
            tenant_id=tenant_id,
            application_id=application_id,
            equipe="support",
            environnement="production",
            modele_id=GRAND_MODELE,
            jour=jour,
            nombre_requetes=volume,
            jetons_entree_total=volume * 100,
            jetons_sortie_total=volume * 50,
            cout_recalcule_usd=round(cout_par_requete * volume, 6),
        )
    ]


@pytest.fixture
def application(app_classement: Application) -> Application:
    """Application de la démonstration (alias du nom partagé du socle).

    Partagée par les tests du module : la bascule, le shadow et le canari
    travaillent tous sur la même application de démonstration.
    """
    return app_classement
