"""Fixtures des tests d'API : store amorcé, journal d'audit et clients de test.

Le store est amorcé avec deux clients (A et B), chacun avec une application, des
agrégats, un levier, une campagne, des recommandations et une vérification
d'économie. C'est ce qui permet d'écrire les tests négatifs R11 : l'utilisateur
du client A doit être refusé sur **chaque** donnée du client B.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE

from jauge.api.app import Ports, creer_app, ports_par_defaut
from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.models.campaign import Campagne, ResultatCandidat, StatutCampagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour
from jauge.models.lever import Levier, TypeLevier
from jauge.models.recommendation import (
    BasculeProgressive,
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
    VerificationEconomie,
)
from jauge.models.tenant import Application, Tenant
from jauge.store.memory import MemoryStore

JOUR = date(2026, 11, 1)
APP_B = "b-resume-nocturne"
CAMPAGNE_A = "c-demo-a"
CAMPAGNE_B = "c-demo-b"
RECO_A = "r-demo-a"
RECO_A_APPROUVEE = "r-demo-a-approuvee"
RECO_B = "r-demo-b"
BASCULE_B = "ro-demo-b"
LEVIER_A = "lv-demo-a"
LEVIER_B = "lv-demo-b"
MODELES_A = [GRAND_MODELE, PETIT_MODELE]


def entetes(
    tenant_id: str,
    role: RoleUtilisateur | str = RoleUtilisateur.DIRECTEUR,
    utilisateur: str = "u-test",
) -> dict[str, str]:
    """En-têtes de démonstration attendus par `deps.obtenir_utilisateur`."""
    return {
        "X-Jauge-Tenant": tenant_id,
        "X-Jauge-Role": str(role),
        "X-Jauge-User": utilisateur,
    }


@pytest.fixture
def app_b(tenant_b: Tenant) -> Application:
    """Application du client B (cible des tests de cloisonnement)."""
    return Application(
        id=APP_B,
        tenant_id=tenant_b.id,
        nom="Résumé nocturne",
        equipe="veille",
        modele_actuel=MOYEN_MODELE,
    )


def _semer_couts(store: MemoryStore, tenant: Tenant, application: Application) -> None:
    """Agrégats et rapprochement du client A."""
    store.enregistrer_agregats(
        [
            AgregatQuotidien(
                tenant_id=tenant.id,
                application_id=application.id,
                equipe=application.equipe,
                environnement="production",
                modele_id=application.modele_actuel,
                jour=JOUR,
                nombre_requetes=1000,
                jetons_entree_total=400_000,
                jetons_sortie_total=100_000,
                cout_recalcule_usd=13.5,
            ),
            AgregatQuotidien(
                tenant_id=tenant.id,
                application_id=application.id,
                equipe=application.equipe,
                environnement="production",
                modele_id=PETIT_MODELE,
                jour=JOUR,
                nombre_requetes=1000,
                jetons_entree_total=400_000,
                jetons_sortie_total=100_000,
                cout_recalcule_usd=2.7,
            ),
        ]
    )
    store.enregistrer_rapprochement(
        RapprochementJour(
            tenant_id=tenant.id,
            jour=JOUR,
            cout_recalcule_usd=16.2,
            cout_facture_usd=16.5,
            ecart_usd=-0.3,
            ecart_pourcentage=1.82,
            part_non_attribuee_pourcentage=0.0,
        )
    )


def _semer_leviers(store: MemoryStore, tenant: Tenant, application: Application) -> None:
    """Deux leviers de niveaux d'économie différents pour vérifier le tri."""
    store.enregistrer_levier(
        Levier(
            id=LEVIER_A,
            tenant_id=tenant.id,
            application_id=application.id,
            type_levier=TypeLevier.MODELE_SURDIMENSIONNE,
            action_proposee="Rejouer sur un modèle plus petit",
            preuve_exigee="Campagne de 200 requêtes",
            economie_mensuelle_estimee_usd=420.0,
        )
    )
    store.enregistrer_levier(
        Levier(
            id="lv-demo-a-2",
            tenant_id=tenant.id,
            application_id=application.id,
            type_levier=TypeLevier.CACHE_PROMPTS_ABSENT,
            action_proposee="Activer le cache de prompts",
            preuve_exigee="Mesure du taux de répétition",
            economie_mensuelle_estimee_usd=95.5,
        )
    )


def _semer_campagnes(store: MemoryStore, tenant: Tenant, application: Application) -> None:
    """Une campagne terminée avec deux résultats candidats."""
    store.enregistrer_campagne(
        Campagne(
            id=CAMPAGNE_A,
            tenant_id=tenant.id,
            application_id=application.id,
            modeles_candidats=MODELES_A,
            statut=StatutCampagne.TERMINEE,
            cout_estime_usd=1.25,
            cout_reel_usd=1.31,
            resultats=[
                ResultatCandidat(
                    modele_id=application.modele_actuel,
                    est_reference=True,
                    nombre_requetes_rejouees=200,
                    taux_equivalence=1.0,
                    intervalle_confiance_bas=1.0,
                    intervalle_confiance_haut=1.0,
                    cout_par_requete_usd=0.0135,
                ),
                ResultatCandidat(
                    modele_id=PETIT_MODELE,
                    nombre_requetes_rejouees=200,
                    taux_equivalence=0.97,
                    intervalle_confiance_bas=0.93,
                    intervalle_confiance_haut=0.99,
                    cout_par_requete_usd=0.0027,
                    conforme_seuils=True,
                ),
            ],
        )
    )


def _recommandation(
    identifiant: str,
    tenant_id: str,
    app_id: str,
    modele_cible: str,
    statut: StatutRecommandation,
) -> Recommandation:
    """Recommandation type au format Annexe B."""
    return Recommandation(
        id=identifiant,
        tenantId=tenant_id,
        applicationId=app_id,
        currentModel=GRAND_MODELE,
        targetModel=modele_cible,
        campaignId=CAMPAGNE_A,
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=0.97,
            confidenceIntervalLow=0.93,
            selfAgreementOfCurrentModel=0.99,
            deterministicFailures=0,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=0.0135,
            costPerRequestAfter=0.0027,
            monthlyRequests=600_000,
            expectedMonthlySaving=6480.0,
        ),
        latency=MetriquesLatence(p50Before=900, p50After=420, p95Before=1800, p95After=900),
        status=statut,
    )


def _semer_recommandations(store: MemoryStore, tenant: Tenant, application: Application) -> None:
    """Une recommandation en attente, une approuvée, une vérification d'économie."""
    store.enregistrer_recommandation(
        _recommandation(
            RECO_A,
            tenant.id,
            application.id,
            PETIT_MODELE,
            StatutRecommandation.EN_ATTENTE_APPROBATION,
        )
    )
    store.enregistrer_recommandation(
        _recommandation(
            RECO_A_APPROUVEE,
            tenant.id,
            application.id,
            PETIT_MODELE,
            StatutRecommandation.APPROUVEE,
        )
    )
    store.enregistrer_verification(
        VerificationEconomie(
            application_id=application.id,
            recommandation_id=RECO_A_APPROUVEE,
            cout_par_requete_avant=0.0135,
            cout_par_requete_apres=0.0027,
            volume_periode=600_000,
            economie_constatee_usd=6300.0,
            economie_attendue_usd=6480.0,
            ecart_pourcentage=-2.78,
        )
    )


def _semer_couts_b(store: MemoryStore, tenant_b: Tenant, application: Application) -> None:
    """Agrégat et rapprochement du client B."""
    store.enregistrer_agregats(
        [
            AgregatQuotidien(
                tenant_id=tenant_b.id,
                application_id=application.id,
                equipe=application.equipe,
                environnement="production",
                modele_id=application.modele_actuel,
                jour=JOUR,
                nombre_requetes=50,
                jetons_entree_total=10_000,
                jetons_sortie_total=5_000,
                cout_recalcule_usd=0.6,
            )
        ]
    )
    store.enregistrer_rapprochement(
        RapprochementJour(
            tenant_id=tenant_b.id,
            jour=JOUR,
            cout_recalcule_usd=0.6,
            cout_facture_usd=0.62,
            ecart_usd=-0.02,
            ecart_pourcentage=3.2,
            part_non_attribuee_pourcentage=0.0,
        )
    )


def _semer_levier_b(store: MemoryStore, tenant_b: Tenant, application: Application) -> None:
    """Levier du client B."""
    store.enregistrer_levier(
        Levier(
            id=LEVIER_B,
            tenant_id=tenant_b.id,
            application_id=application.id,
            type_levier=TypeLevier.TRAITEMENT_DIFFERABLE,
            action_proposee="Basculer le traitement en Flex",
            preuve_exigee="Mesure de latence acceptable",
            economie_mensuelle_estimee_usd=12.0,
        )
    )


def _semer_campagne_b(store: MemoryStore, tenant_b: Tenant, application: Application) -> None:
    """Campagne et recommandation approuvée du client B."""
    store.enregistrer_campagne(
        Campagne(
            id=CAMPAGNE_B,
            tenant_id=tenant_b.id,
            application_id=application.id,
            modeles_candidats=[PETIT_MODELE],
            statut=StatutCampagne.TERMINEE,
            cout_estime_usd=0.2,
        )
    )
    store.enregistrer_recommandation(
        _recommandation(
            RECO_B,
            tenant_b.id,
            application.id,
            PETIT_MODELE,
            StatutRecommandation.APPROUVEE,
        )
    )


def _semer_bascule_b(store: MemoryStore, tenant_b: Tenant, application: Application) -> None:
    """Bascule en cours et vérification d'économie du client B."""
    store.enregistrer_bascule(
        BasculeProgressive(
            id=BASCULE_B,
            tenant_id=tenant_b.id,
            recommandation_id=RECO_B,
            application_id=application.id,
            modele_cible=PETIT_MODELE,
            regle_passerelle_id="gw-demo-b",
        )
    )
    store.enregistrer_verification(
        VerificationEconomie(
            application_id=application.id,
            recommandation_id=RECO_B,
            cout_par_requete_avant=0.012,
            cout_par_requete_apres=0.008,
            volume_periode=15_000,
            economie_constatee_usd=60.0,
            economie_attendue_usd=62.0,
            ecart_pourcentage=-3.2,
        )
    )


def _semer_client_b(store: MemoryStore, tenant_b: Tenant, application: Application) -> None:
    """Données du client B : tout ce qui ne doit jamais fuiter vers le client A."""
    _semer_couts_b(store, tenant_b, application)
    _semer_levier_b(store, tenant_b, application)
    _semer_campagne_b(store, tenant_b, application)
    _semer_bascule_b(store, tenant_b, application)


@pytest.fixture
def store(
    tenant: Tenant,
    tenant_b: Tenant,
    app_classement: Application,
    app_assistant: Application,
    app_b: Application,
) -> MemoryStore:
    """Store en mémoire amorcé pour les deux clients."""
    memoire = MemoryStore()
    for client in (tenant, tenant_b):
        memoire.enregistrer_tenant(client)
    for application in (app_classement, app_assistant, app_b):
        memoire.enregistrer_application(application)
    _semer_couts(memoire, tenant, app_classement)
    _semer_leviers(memoire, tenant, app_classement)
    _semer_campagnes(memoire, tenant, app_classement)
    _semer_recommandations(memoire, tenant, app_classement)
    _semer_client_b(memoire, tenant_b, app_b)
    return memoire


@pytest.fixture
def audit() -> MemoryAuditStore:
    """Journal d'audit mémoire partagé avec l'application de test."""
    return MemoryAuditStore()


@pytest.fixture
def client(store: MemoryStore, audit: MemoryAuditStore) -> TestClient:
    """Client de test en mode démo, modules M4/M7/M9 remplacés par la démo."""
    return TestClient(creer_app(store=store, mode_demo=True, audit=audit))


@pytest.fixture
def client_sans_modules(store: MemoryStore, audit: MemoryAuditStore) -> TestClient:
    """Client de test où les ports M4/M7/M9 refusent explicitement (502)."""
    ports: Ports = ports_par_defaut(store, mode_demo=False)
    return TestClient(creer_app(store=store, mode_demo=True, audit=audit, ports=ports))


@pytest.fixture
def client_jeton(store: MemoryStore, audit: MemoryAuditStore) -> TestClient:
    """Client de test hors mode démo : exige un jeton Bearer Cognito."""
    return TestClient(creer_app(store=store, mode_demo=False, audit=audit))
