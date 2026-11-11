"""Tests des recommandations et décisions (§15, EF-REC-01..02, ENF-IA-02)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import APP_B, RECO_A, RECO_A_APPROUVEE, RECO_B, entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur


def test_liste_recommandations(client: TestClient) -> None:
    """`GET .../recommendations` rend les recommandations de l'application."""
    reponse = client.get(
        "/v1/applications/ticket-classifier/recommendations", headers=entetes("t-demo-a")
    )
    assert reponse.status_code == 200
    assert {r["id"] for r in reponse.json()} == {RECO_A, RECO_A_APPROUVEE}


def test_recommandation_respecte_l_annexe_b(client: TestClient) -> None:
    """Le format normalisé (Annexe B) est bien exposé : qualité, coût, latence."""
    recommandations = client.get(
        "/v1/applications/ticket-classifier/recommendations", headers=entetes("t-demo-a")
    ).json()
    reco = next(r for r in recommandations if r["id"] == RECO_A)
    assert reco["quality"]["equivalenceRate"] == 0.97
    assert reco["cost"]["expectedMonthlySaving"] == 6480.0
    assert reco["latency"]["p95After"] == 900
    assert reco["rolloutPlan"] == [10, 50, 100]
    assert reco["status"] == "PENDING_APPROVAL"


def test_approbation_recommandation(client: TestClient) -> None:
    """Le responsable d'application approuve : statut et décideur enregistrés."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_A}/decision",
        json={"decision": "approuver", "motif": "Qualité prouvée sur 200 requêtes"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION, "chef-produit"),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["status"] == "APPROVED"
    assert corps["approvedBy"] == "chef-produit@demo.jauge.local"
    assert corps["approvalNotes"] == "Qualité prouvée sur 200 requêtes"


def test_refus_recommandation(client: TestClient) -> None:
    """Un refus laisse la recommandation en statut REJECTED."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_A}/decision",
        json={"decision": "refuser", "motif": "Risque de latence"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )
    assert reponse.status_code == 200
    assert reponse.json()["status"] == "REJECTED"


def test_decision_deja_prise_refusee(client: TestClient) -> None:
    """Une recommandation déjà tranchée ne peut pas l'être deux fois (409)."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/decision",
        json={"decision": "refuser"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )
    assert reponse.status_code == 409
    assert reponse.json()["error"]["code"] == "invalid_state_transition"


def test_decision_interdite_au_directeur(client: TestClient) -> None:
    """Le directeur financier lit mais ne décide pas (§6)."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_A}/decision",
        json={"decision": "approuver"},
        headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR),
    )
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_decision_valeur_invalide(client: TestClient) -> None:
    """Une décision hors vocabulaire est refusée (422)."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_A}/decision",
        json={"decision": "peut-etre"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )
    assert reponse.status_code == 422


def test_decision_recommandation_inconnue(client: TestClient) -> None:
    """Une recommandation inconnue renvoie 404."""
    reponse = client.post(
        "/v1/recommendations/r-fantome/decision",
        json={"decision": "approuver"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )
    assert reponse.status_code == 404


def test_decision_journalisee(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : chaque décision est journalisée avec son auteur et son rôle."""
    client.post(
        f"/v1/recommendations/{RECO_A}/decision",
        json={"decision": "approuver", "motif": "ok"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION, "chef-produit"),
    )
    evenements = audit.lister(action="decision_recommandation")
    assert len(evenements) == 1
    evenement = evenements[0]
    assert evenement.tenant_id == "t-demo-a"
    assert evenement.role == "responsable_application"
    assert evenement.utilisateur_id == "chef-produit"
    assert evenement.details["decision"] == "approuver"
    assert evenement.details["statut"] == "APPROVED"


def test_recommandations_du_client_b_refusees(client: TestClient) -> None:
    """R11 : les recommandations du client B sont refusées au client A."""
    reponse = client.get(f"/v1/applications/{APP_B}/recommendations", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403


def test_decision_sur_recommandation_du_client_b_refusee(client: TestClient) -> None:
    """R11 : décider sur la recommandation du client B est refusé au client A."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_B}/decision",
        json={"decision": "refuser"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"
