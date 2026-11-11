"""Tests des clients : création, lecture, liste filtrée, rôles (§15, §6)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import PETIT_MODELE, entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

CORPS_TENANT = {
    "nom": "Client C — Nouveau",
    "compte_aws": "111122223333",
    "role_arn": "arn:aws:iam::111122223333:role/jauge-dataplane",
    "region_principale": "eu-west-1",
    "external_id": "ext-demo-c",
    "plafond_campagne_usd": 30.0,
    "modeles_autorises": [PETIT_MODELE],
}


def test_creation_client_par_administrateur(client: TestClient) -> None:
    """`POST /v1/tenants` crée un client et l'identifie."""
    reponse = client.post(
        "/v1/tenants",
        json=CORPS_TENANT,
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["id"].startswith("t-")
    assert corps["nom"] == "Client C — Nouveau"
    assert corps["compte_aws"] == "111122223333"


def test_creation_client_reservee_a_l_administrateur(client: TestClient) -> None:
    """Un analyste FinOps ne crée pas de client (droits du §6)."""
    reponse = client.post(
        "/v1/tenants",
        json=CORPS_TENANT,
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_creation_client_compte_aws_invalide(client: TestClient) -> None:
    """Un numéro de compte AWS hors format 12 chiffres est refusé (422)."""
    corps = dict(CORPS_TENANT, compte_aws="12345")
    reponse = client.post(
        "/v1/tenants",
        json=corps,
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 422


def test_lecture_client(client: TestClient) -> None:
    """`GET /v1/tenants/{id}` rend le client de l'appelant."""
    reponse = client.get("/v1/tenants/t-demo-a", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    assert reponse.json()["formule"] == "optimisation_continue"


def test_lecture_client_inexistant(client: TestClient) -> None:
    """Un identifiant inconnu renvoie 404 au format normalisé."""
    reponse = client.get(
        "/v1/tenants/t-fantome",
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_liste_filtree_pour_un_utilisateur_client(client: TestClient) -> None:
    """Un utilisateur client ne voit que son propre client dans la liste."""
    reponse = client.get("/v1/tenants", headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR))
    assert reponse.status_code == 200
    assert [t["id"] for t in reponse.json()] == ["t-demo-a"]


def test_liste_complete_pour_l_administrateur(client: TestClient) -> None:
    """L'administrateur de plateforme voit tout le parc (EF-RAP-02)."""
    reponse = client.get(
        "/v1/tenants", headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME)
    )
    assert {t["id"] for t in reponse.json()} == {"t-demo-a", "t-demo-b"}


def test_creation_client_journalisee(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : la création d'un client laisse une trace d'audit."""
    client.post(
        "/v1/tenants",
        json=CORPS_TENANT,
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME, "alice"),
    )
    evenements = audit.lister(action="creation_tenant")
    assert len(evenements) == 1
    assert evenements[0].utilisateur_id == "alice"
    assert evenements[0].role == "administrateur_plateforme"
