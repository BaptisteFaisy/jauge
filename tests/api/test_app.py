"""Tests de l'application : factory, OpenAPI, erreurs normalisées, CORS, portail."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.api.conftest import entetes

from jauge.api.app import DOSSIER_PORTAIL, creer_app
from jauge.store.memory import MemoryStore


def test_factory_cree_une_application_avec_store_par_defaut() -> None:
    """Sans argument, la factory monte une application et un `MemoryStore`."""
    application = creer_app()
    assert isinstance(application, FastAPI)
    assert isinstance(application.state.store, MemoryStore)


def test_openapi_et_documentation_actifs(client: TestClient) -> None:
    """La documentation OpenAPI est servie (exigence de lisibilité pour les juges)."""
    schema = client.get("/v1/openapi.json")
    assert schema.status_code == 200
    chemins = schema.json()["paths"]
    assert "/v1/tenants" in chemins
    assert "/v1/chat" in chemins
    assert schema.json()["info"]["title"].startswith("Jauge")
    assert client.get("/v1/docs").status_code == 200


def test_erreur_metier_normalisee(client: TestClient) -> None:
    """Une `JaugeError` sort au format `{"error": {code, message, details}}`."""
    reponse = client.get(
        "/v1/tenants/t-inexistant",
        headers=entetes("t-demo-a", "administrateur_plateforme"),
    )
    assert reponse.status_code == 404
    corps = reponse.json()
    assert corps["error"]["code"] == "not_found"
    assert corps["error"]["details"]["tenant_id"] == "t-inexistant"


def test_validation_pydantic_normalisee(client: TestClient) -> None:
    """Une requête invalide renvoie 422 au même format que les erreurs métier."""
    reponse = client.post(
        "/v1/tenants",
        json={
            "nom": "Client invalide",
            "compte_aws": "12",
            "role_arn": "arn:aws:iam::1:role/x",
        },
        headers=entetes("t-demo-a", "administrateur_plateforme"),
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "validation_error"


def test_cors_ouvert_pour_le_portail(client: TestClient) -> None:
    """Le portail (M13) est servi depuis une autre origine : CORS nécessaire."""
    reponse = client.options(
        "/v1/tenants",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert reponse.status_code == 200
    assert reponse.headers["access-control-allow-origin"] == "*"


def test_portail_non_monte_quand_le_dossier_est_absent() -> None:
    """Le portail est optionnel : son absence ne casse pas l'API."""
    application = creer_app(mode_demo=True)
    noms = {getattr(route, "name", None) for route in application.routes}
    assert ("portal" in noms) is DOSSIER_PORTAIL.is_dir()


def test_mode_demo_forcable_par_la_factory() -> None:
    """`mode_demo` passé à la factory prime sur la variable d'environnement."""
    application = creer_app(mode_demo=False)
    assert application.state.mode_demo is False
