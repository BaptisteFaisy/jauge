"""Tests des rapports (EF-RAP-03) et du refus explicite quand M9 n'est pas livré."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

GESTIONNAIRE = RoleUtilisateur.RESPONSABLE_COMPTE


def test_generation_rapport(client: TestClient) -> None:
    """`POST /v1/tenants/{id}/reports` génère un rapport sur les chiffres du socle."""
    reponse = client.post(
        "/v1/tenants/t-demo-a/reports",
        json={"periode_debut": "2026-10-01", "periode_fin": "2026-11-30"},
        headers=entetes("t-demo-a", GESTIONNAIRE),
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["tenant_id"] == "t-demo-a"
    assert corps["synthese"]["cout_total_usd"] == 16.2
    assert "synthese_redigee" in corps


def test_lecture_rapport(client: TestClient) -> None:
    """`GET /v1/reports/{id}` rend un rapport déjà généré."""
    rapport = client.post(
        "/v1/tenants/t-demo-a/reports",
        json={"periode_debut": "2026-10-01", "periode_fin": "2026-11-30"},
        headers=entetes("t-demo-a", GESTIONNAIRE),
    ).json()
    reponse = client.get(f"/v1/reports/{rapport['id']}", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    assert reponse.json()["id"] == rapport["id"]


def test_rapport_inconnu(client: TestClient) -> None:
    """Un rapport inconnu renvoie 404."""
    reponse = client.get("/v1/reports/rp-fantome", headers=entetes("t-demo-a"))
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_periode_inversee_refusee(client: TestClient) -> None:
    """Une période de fin antérieure au début est refusée (422)."""
    reponse = client.post(
        "/v1/tenants/t-demo-a/reports",
        json={"periode_debut": "2026-11-30", "periode_fin": "2026-10-01"},
        headers=entetes("t-demo-a", GESTIONNAIRE),
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "validation_error"


def test_rapport_reserve_aux_roles_msp(client: TestClient) -> None:
    """Un directeur client ne génère pas de rapport (§6 : lecture seule)."""
    reponse = client.post(
        "/v1/tenants/t-demo-a/reports",
        json={},
        headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR),
    )
    assert reponse.status_code == 403


def test_rapport_journalise(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : la génération d'un rapport est journalisée."""
    client.post(
        "/v1/tenants/t-demo-a/reports",
        json={"periode_debut": "2026-10-01", "periode_fin": "2026-11-30"},
        headers=entetes("t-demo-a", GESTIONNAIRE),
    )
    evenements = audit.lister(action="rapport_genere")
    assert len(evenements) == 1
    assert evenements[0].tenant_id == "t-demo-a"


def test_rapport_client_b_refuse(client: TestClient) -> None:
    """R11 : générer un rapport pour le client B est refusé au client A."""
    reponse = client.post(
        "/v1/tenants/t-demo-b/reports", json={}, headers=entetes("t-demo-a", GESTIONNAIRE)
    )
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_rapport_du_client_b_non_lisible(client: TestClient) -> None:
    """R11 : un rapport du client B n'est pas lisible par le client A."""
    rapport = client.post(
        "/v1/tenants/t-demo-b/reports",
        json={"periode_debut": "2026-10-01", "periode_fin": "2026-11-30"},
        headers=entetes("t-demo-b", GESTIONNAIRE),
    ).json()
    reponse = client.get(f"/v1/reports/{rapport['id']}", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_rapports_indisponibles_sans_module_m9(client_sans_modules: TestClient) -> None:
    """Tant que M9 n'est pas livré, la route répond 502 avec un message clair."""
    reponse = client_sans_modules.post(
        "/v1/tenants/t-demo-a/reports", json={}, headers=entetes("t-demo-a", GESTIONNAIRE)
    )
    assert reponse.status_code == 502
    corps = reponse.json()
    assert corps["error"]["code"] == "upstream_unavailable"
    assert corps["error"]["details"]["module"] == "M9"
