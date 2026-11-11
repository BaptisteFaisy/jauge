"""Tests des coûts et du rapprochement facture (§15, EF-ATT-05..07)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import APP_B, GRAND_MODELE, PETIT_MODELE, entetes

from jauge.api.auth import RoleUtilisateur


def test_couts_par_jour_et_par_modele(client: TestClient) -> None:
    """`GET /v1/applications/{id}/costs` ventile les coûts par jour et modèle."""
    reponse = client.get(
        "/v1/applications/ticket-classifier/costs",
        params={"debut": "2026-10-01", "fin": "2026-11-30"},
        headers=entetes("t-demo-a"),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["cout_total_usd"] == 16.2
    assert corps["requetes_total"] == 2000
    assert len(corps["par_jour"]) == 2
    assert corps["par_modele"] == {GRAND_MODELE: 13.5, PETIT_MODELE: 2.7}


def test_couts_sans_ventilation_par_type_de_requete(client: TestClient) -> None:
    """Écart assumé : les agrégats du socle ne portent pas le type de requête."""
    reponse = client.get(
        "/v1/applications/ticket-classifier/costs",
        params={"debut": "2026-10-01", "fin": "2026-11-30"},
        headers=entetes("t-demo-a"),
    )
    assert "par_type_requete" not in reponse.json()


def test_couts_periode_sans_donnees(client: TestClient) -> None:
    """Une période sans agrégat rend une réponse vide, pas une erreur."""
    reponse = client.get(
        "/v1/applications/ticket-classifier/costs",
        params={"debut": "2020-01-01", "fin": "2020-01-31"},
        headers=entetes("t-demo-a"),
    )
    assert reponse.status_code == 200
    assert reponse.json()["cout_total_usd"] == 0.0
    assert reponse.json()["par_jour"] == []


def test_couts_application_inconnue(client: TestClient) -> None:
    """Une application inconnue renvoie 404."""
    reponse = client.get("/v1/applications/app-fantome/costs", headers=entetes("t-demo-a"))
    assert reponse.status_code == 404


def test_couts_application_du_client_b_refuses(client: TestClient) -> None:
    """R11 : les coûts du client B ne sont pas lisibles par le client A."""
    reponse = client.get(f"/v1/applications/{APP_B}/costs", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_rapprochement(client: TestClient) -> None:
    """`GET /v1/tenants/{id}/reconciliation` rend l'écart recalculé / facturé."""
    reponse = client.get("/v1/tenants/t-demo-a/reconciliation", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    lignes = reponse.json()
    assert len(lignes) == 1
    assert lignes[0]["ecart_pourcentage"] == 1.82
    assert lignes[0]["conforme"] is True


def test_rapprochement_client_inconnu(client: TestClient) -> None:
    """Un client inconnu renvoie 404."""
    reponse = client.get(
        "/v1/tenants/t-fantome/reconciliation",
        headers=entetes("t-fantome", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 404


def test_rapprochement_du_client_b_refuse(client: TestClient) -> None:
    """R11 : le rapprochement du client B est refusé au client A."""
    reponse = client.get("/v1/tenants/t-demo-b/reconciliation", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"
