"""Tests des leviers détectés (§15, Annexe A)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import APP_B, entetes


def test_leviers_tries_par_economie_decroissante(client: TestClient) -> None:
    """Les leviers sortent du plus rentable au moins rentable (EF-LEV-06)."""
    reponse = client.get("/v1/applications/ticket-classifier/levers", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    leviers = reponse.json()
    assert [lv["economie_mensuelle_estimee_usd"] for lv in leviers] == [420.0, 95.5]
    assert leviers[0]["type_levier"] == "modele_surdimensionne"


def test_leviers_application_sans_levier(client: TestClient) -> None:
    """Une application sans levier rend une liste vide."""
    reponse = client.get("/v1/applications/support-assistant/levers", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    assert reponse.json() == []


def test_leviers_application_inconnue(client: TestClient) -> None:
    """Une application inconnue renvoie 404."""
    reponse = client.get("/v1/applications/app-fantome/levers", headers=entetes("t-demo-a"))
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_leviers_du_client_b_refuses(client: TestClient) -> None:
    """R11 : les leviers du client B sont refusés au client A."""
    reponse = client.get(f"/v1/applications/{APP_B}/levers", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"
