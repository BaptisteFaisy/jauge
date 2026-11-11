"""Tests des paramètres d'administration (`GET/PATCH /v1/settings`) — EF-ADM-01."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import PETIT_MODELE, entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

ADMIN = RoleUtilisateur.ADMINISTRATEUR_PLATEFORME


def test_lecture_parametres(client: TestClient) -> None:
    """Les paramètres exposent seuils, échantillon, paliers et tarifs."""
    reponse = client.get("/v1/settings", headers=entetes("t-demo-a", ADMIN))
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["seuil_qualite_defaut"] == 0.95
    assert corps["taille_echantillon_defaut"] == 200
    assert corps["paliers_bascule"] == [10, 50, 100]
    assert corps["table_tarifs"] == []
    assert corps["modeles_candidats_par_client"] == {}


def test_lecture_parametres_interdite_au_client(client: TestClient) -> None:
    """Les paramètres de plateforme ne sont pas lisibles par un rôle client."""
    reponse = client.get("/v1/settings", headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_modification_parametres(client: TestClient) -> None:
    """`PATCH /v1/settings` applique une mise à jour partielle."""
    reponse = client.patch(
        "/v1/settings",
        json={
            "seuil_qualite_defaut": 0.97,
            "taille_echantillon_defaut": 400,
            "modeles_candidats_par_client": {"t-demo-a": [PETIT_MODELE]},
            "paliers_bascule": [5, 25, 100],
        },
        headers=entetes("t-demo-a", ADMIN),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["seuil_qualite_defaut"] == 0.97
    assert corps["taille_echantillon_defaut"] == 400
    assert corps["modeles_candidats_par_client"]["t-demo-a"] == [PETIT_MODELE]


def test_table_des_tarifs_modifiable(client: TestClient) -> None:
    """La table des tarifs datée fait partie des paramètres (EF-ATT-04)."""
    tarif = {
        "modele_id": PETIT_MODELE,
        "region": "eu-west-1",
        "niveau": "standard",
        "date_effet": "2026-01-01",
        "prix_entree_1k": 0.003,
        "prix_sortie_1k": 0.015,
    }
    reponse = client.patch(
        "/v1/settings", json={"table_tarifs": [tarif]}, headers=entetes("t-demo-a", ADMIN)
    )
    assert reponse.status_code == 200
    assert reponse.json()["table_tarifs"][0]["prix_entree_1k"] == 0.003


def test_paliers_non_croissants_refuses(client: TestClient) -> None:
    """Un plan de bascule incohérent est refusé (422)."""
    reponse = client.patch(
        "/v1/settings",
        json={"paliers_bascule": [50, 10, 100]},
        headers=entetes("t-demo-a", ADMIN),
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "validation_error"


def test_cle_inconnue_refusee(client: TestClient) -> None:
    """Une clé hors contrat est refusée au lieu d'être ignorée en silence."""
    reponse = client.patch(
        "/v1/settings", json={"clef_magique": 1}, headers=entetes("t-demo-a", ADMIN)
    )
    assert reponse.status_code == 422


def test_modification_reservee_a_l_administrateur(client: TestClient) -> None:
    """L'analyste FinOps lit les paramètres mais ne les modifie pas."""
    reponse = client.patch(
        "/v1/settings",
        json={"taille_echantillon_defaut": 300},
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 403


def test_modification_journalisee(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : un changement de paramètres est journalisé."""
    client.patch(
        "/v1/settings",
        json={"taille_echantillon_defaut": 300},
        headers=entetes("t-demo-a", ADMIN, "admin-plateforme"),
    )
    evenements = audit.lister(action="modification_parametres")
    assert len(evenements) == 1
    assert evenements[0].details["cles_modifiees"] == ["taille_echantillon_defaut"]
