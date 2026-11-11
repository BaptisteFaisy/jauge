"""Recette R11 — un utilisateur du client A sur les données du client B (§22).

Scénario : « Utilisateur du client A sur les données du client B » → **refus
sur chaque point d'API qui expose des données** (ENF-SEC-02, EF-POR-01).

La recette parcourt l'API FastAPI en **mode démo** (en-têtes `X-Jauge-*`), sur
un store amorcé par le générateur de la démonstration (`demo.seed.peupler_store`)
complété par les ressources du client B qui manqueraient (campagne,
recommandation, bascule). Pour chaque point d'API qui expose des données, la
réponse doit être **403 `tenant_isolation_violation`** et le refus doit laisser
une trace dans le journal d'audit (EF-ADM-02).

Aucun réseau, aucun AWS.
"""

from __future__ import annotations

import pytest
from demo.seed import peupler_store
from fastapi.testclient import TestClient
from tests.recette.conftest import entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.models.campaign import Campagne, ResultatCandidat, StatutCampagne
from jauge.models.recommendation import (
    BasculeProgressive,
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
)
from jauge.store.memory import MemoryStore

CLIENT_A = "t-demo-a"
CLIENT_B = "t-demo-b"
APP_B = "kb-search"
CAMPAGNE_B = "c-recette-b"
RECO_B = "r-recette-b"
BASCULE_B = "ro-recette-b"
ROLE = RoleUtilisateur.DIRECTEUR

#: (méthode, chemin, corps, rôle minimal) — tous les points d'API qui exposent
#: des données. Le rôle est celui qu'exige la route : le refus visé est alors
#: bien le refus de cloisonnement, pas un refus de rôle.
CAS_R11 = [
    ("GET", f"/v1/tenants/{CLIENT_B}", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/tenants/{CLIENT_B}/applications", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}/costs", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/tenants/{CLIENT_B}/reconciliation", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}/levers", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}/recommendations", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/tenants/{CLIENT_B}/savings", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/campaigns/{CAMPAGNE_B}", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/campaigns/{CAMPAGNE_B}/results", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/rollouts/{BASCULE_B}", None, RoleUtilisateur.DIRECTEUR),
    (
        "POST",
        f"/v1/applications/{APP_B}/campaigns",
        {"taille_echantillon": 200},
        RoleUtilisateur.ANALYSTE_FINOPS,
    ),
    (
        "POST",
        f"/v1/recommendations/{RECO_B}/decision",
        {"decision": "refuser"},
        RoleUtilisateur.RESPONSABLE_APPLICATION,
    ),
    (
        "POST",
        f"/v1/recommendations/{RECO_B}/rollout",
        {},
        RoleUtilisateur.RESPONSABLE_APPLICATION,
    ),
    (
        "POST",
        f"/v1/rollouts/{BASCULE_B}/rollback",
        None,
        RoleUtilisateur.RESPONSABLE_APPLICATION,
    ),
    (
        "POST",
        f"/v1/tenants/{CLIENT_B}/reports",
        {},
        RoleUtilisateur.RESPONSABLE_COMPTE,
    ),
    (
        "POST",
        "/v1/chat",
        {"question": "Coût ?", "tenant_id": CLIENT_B},
        RoleUtilisateur.DIRECTEUR,
    ),
]


def _semer_campagne_b(store: MemoryStore) -> None:
    """Campagne du client B (la démo n'en crée pas pour lui)."""
    store.enregistrer_campagne(
        Campagne(
            id=CAMPAGNE_B,
            tenant_id=CLIENT_B,
            application_id=APP_B,
            modeles_candidats=["anthropic.claude-3-5-haiku-20241022-v1:0"],
            taille_echantillon=200,
            statut=StatutCampagne.TERMINEE,
            cout_estime_usd=0.4,
            resultats=[
                ResultatCandidat(
                    modele_id="anthropic.claude-3-5-haiku-20241022-v1:0",
                    nombre_requetes_rejouees=200,
                    taux_equivalence=0.96,
                    intervalle_confiance_bas=0.92,
                    intervalle_confiance_haut=0.98,
                    cout_par_requete_usd=0.0009,
                )
            ],
        )
    )


def _semer_recommandation_b(store: MemoryStore) -> None:
    """Recommandation approuvée du client B."""
    store.enregistrer_recommandation(
        Recommandation(
            id=RECO_B,
            tenantId=CLIENT_B,
            applicationId=APP_B,
            currentModel="anthropic.claude-3-5-sonnet-20241022-v2:0",
            targetModel="anthropic.claude-3-5-haiku-20241022-v1:0",
            campaignId=CAMPAGNE_B,
            sampleSize=200,
            quality=MetriquesQualite(
                equivalenceRate=0.96,
                confidenceIntervalLow=0.92,
                selfAgreementOfCurrentModel=0.99,
            ),
            cost=MetriquesCout(
                costPerRequestBefore=0.004,
                costPerRequestAfter=0.0009,
                monthlyRequests=5_000,
                expectedMonthlySaving=15.5,
            ),
            latency=MetriquesLatence(p50Before=800, p50After=300, p95Before=1500, p95After=700),
            status=StatutRecommandation.APPROUVEE,
            approvedBy="resp-b",
        )
    )


def _semer_bascule_b(store: MemoryStore) -> None:
    """Bascule en cours du client B."""
    store.enregistrer_bascule(
        BasculeProgressive(
            id=BASCULE_B,
            tenant_id=CLIENT_B,
            recommandation_id=RECO_B,
            application_id=APP_B,
            modele_cible="anthropic.claude-3-5-haiku-20241022-v1:0",
            regle_passerelle_id="gw-recette-b",
            palier_actuel_pourcentage=10,
        )
    )


@pytest.fixture
def store_demo(store_recette: MemoryStore) -> MemoryStore:
    """Store amorcé par la démonstration, avec les ressources du client B."""
    peupler_store(store_recette, jours=1)
    _semer_campagne_b(store_recette)
    _semer_recommandation_b(store_recette)
    _semer_bascule_b(store_recette)
    return store_recette


@pytest.fixture
def client_demo(store_demo: MemoryStore, audit_recette: MemoryAuditStore) -> TestClient:
    """API en mode démo sur le store de démonstration."""
    from jauge.api.app import creer_app

    return TestClient(creer_app(store=store_demo, mode_demo=True, audit=audit_recette))


@pytest.mark.parametrize(("methode", "chemin", "corps", "role"), CAS_R11)
def test_r11_client_a_refuse_sur_les_donnees_du_client_b(
    client_demo: TestClient,
    methode: str,
    chemin: str,
    corps: dict[str, object] | None,
    role: RoleUtilisateur,
) -> None:
    """R11 : refus 403 sur chaque point d'API qui expose des données du client B."""
    reponse = client_demo.request(methode, chemin, json=corps, headers=entetes(CLIENT_A, role))
    assert reponse.status_code == 403, f"{methode} {chemin} devrait être refusé : {reponse.text}"
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_r11_refus_journalise_dans_audit(
    client_demo: TestClient, audit_recette: MemoryAuditStore
) -> None:
    """R11 : chaque refus laisse une trace dans le journal d'audit (EF-ADM-02)."""
    client_demo.get(f"/v1/applications/{APP_B}", headers=entetes(CLIENT_A, ROLE))
    refus = audit_recette.lister(action="lecture")
    assert refus, "le refus doit être journalisé"
    assert refus[-1].succes is False
    assert refus[-1].statut_http == 403


def test_r11_client_a_accede_a_ses_propres_donnees(client_demo: TestClient) -> None:
    """R11 (contrôle) : le client A continue d'accéder à ses propres données."""
    reponse = client_demo.get("/v1/applications/ticket-classifier", headers=entetes(CLIENT_A, ROLE))
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["tenant_id"] == CLIENT_A


def test_r11_le_store_ne_fuit_pas_les_donnees_du_client_b(store_demo: MemoryStore) -> None:
    """R11 (contrôle) : le store lui-même est cloisonné par client (ENF-SEC-02)."""
    assert store_demo.obtenir_application(CLIENT_A, APP_B) is None
    assert store_demo.lister_agregats(CLIENT_A, app_id=APP_B) == []
    assert store_demo.lister_campagnes(CLIENT_A, app_id=APP_B) == []
    assert store_demo.lister_recommandations(CLIENT_A, app_id=APP_B) == []
    verifications = store_demo.lister_verifications(CLIENT_A)
    assert all(v.application_id != APP_B for v in verifications)
