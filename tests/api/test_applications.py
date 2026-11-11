"""Tests des applications : création, lecture, liste, cloisonnement (§15)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import APP_B, GRAND_MODELE, entetes

from jauge.api.auth import RoleUtilisateur

CORPS_APP = {
    "nom": "Assistant interne",
    "equipe": "support",
    "environnement": "production",
    "modele_actuel": GRAND_MODELE,
    "seuil_qualite": 0.95,
    "passe_par_passerelle": True,
}


def test_creation_application(client: TestClient) -> None:
    """`POST /v1/tenants/{id}/applications` rattache une application au client."""
    reponse = client.post(
        "/v1/tenants/t-demo-a/applications",
        json=CORPS_APP,
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["tenant_id"] == "t-demo-a"
    assert corps["id"].startswith("app-")
    assert corps["seuil_qualite"] == 0.95


def test_creation_application_client_inexistant(client: TestClient) -> None:
    """Un client inconnu renvoie 404, pas 500."""
    reponse = client.post(
        "/v1/tenants/t-fantome/applications",
        json=CORPS_APP,
        headers=entetes("t-fantome", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_creation_application_reservee_a_l_administrateur(client: TestClient) -> None:
    """Le responsable d'application ne crée pas d'application (droits du §6)."""
    reponse = client.post(
        "/v1/tenants/t-demo-a/applications",
        json=CORPS_APP,
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )
    assert reponse.status_code == 403


def test_seuil_de_qualite_hors_bornes(client: TestClient) -> None:
    """Un seuil de qualité à 0,1 est refusé (422)."""
    corps = dict(CORPS_APP, seuil_qualite=0.1)
    reponse = client.post(
        "/v1/tenants/t-demo-a/applications",
        json=corps,
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 422


def test_lecture_application(client: TestClient) -> None:
    """`GET /v1/applications/{id}` rend l'application du client."""
    reponse = client.get("/v1/applications/ticket-classifier", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    assert reponse.json()["equipe"] == "support"


def test_lecture_application_inexistante(client: TestClient) -> None:
    """Une application inconnue renvoie 404."""
    reponse = client.get("/v1/applications/app-fantome", headers=entetes("t-demo-a"))
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_liste_applications_du_client(client: TestClient) -> None:
    """La liste des applications est filtrée par client."""
    reponse = client.get("/v1/tenants/t-demo-a/applications", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    assert {a["id"] for a in reponse.json()} == {"ticket-classifier", "support-assistant"}


def test_liste_applications_client_inexistant(client: TestClient) -> None:
    """Un client inconnu renvoie 404."""
    reponse = client.get(
        "/v1/tenants/t-fantome/applications",
        headers=entetes("t-fantome", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 404


def test_application_du_client_b_refusee(client: TestClient) -> None:
    """R11 : l'application du client B est refusée à l'utilisateur du client A."""
    reponse = client.get(f"/v1/applications/{APP_B}", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"
