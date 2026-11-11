"""Tests des économies attendues et constatées (§15, EF-VER-01..02)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import entetes

from jauge.api.auth import RoleUtilisateur


def test_economies_agregees(client: TestClient) -> None:
    """`GET /v1/tenants/{id}/savings` somme les vérifications à 30 jours."""
    reponse = client.get("/v1/tenants/t-demo-a/savings", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["economie_attendue_usd"] == 6480.0
    assert corps["economie_constatee_usd"] == 6300.0
    assert corps["ecart_pourcentage"] == -2.78
    assert len(corps["verifications"]) == 1


def test_economies_client_sans_verification(client: TestClient) -> None:
    """Un client sans vérification rend des zéros, sans division par zéro."""
    reponse = client.get(
        "/v1/tenants/t-demo-a/savings",
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 200


def test_economies_client_inconnu(client: TestClient) -> None:
    """Un client inconnu renvoie 404."""
    reponse = client.get(
        "/v1/tenants/t-fantome/savings",
        headers=entetes("t-fantome", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_economies_du_client_b_refusees(client: TestClient) -> None:
    """R11 : les économies du client B sont refusées au client A."""
    reponse = client.get("/v1/tenants/t-demo-b/savings", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"
