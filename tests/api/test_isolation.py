"""Tests négatifs de cloisonnement — scénario de recette R11 (ENF-SEC-02).

Un utilisateur du client A tente d'atteindre **chaque** point d'API qui expose
des données du client B : la réponse doit être 403 `tenant_isolation_violation`
et l'événement doit être journalisé dans l'audit.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import (
    APP_B,
    BASCULE_B,
    CAMPAGNE_B,
    PETIT_MODELE,
    RECO_B,
    entetes,
)

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

CLIENT_A = "t-demo-a"
CLIENT_B = "t-demo-b"
CORPS_CAMPAGNE = {"modeles_candidats": [PETIT_MODELE], "taille_echantillon": 200}

#: (méthode, chemin, corps, rôle minimal nécessaire pour atteindre la route)
CAS_R11 = [
    ("GET", f"/v1/tenants/{CLIENT_B}", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/tenants/{CLIENT_B}/applications", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}/costs", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/tenants/{CLIENT_B}/reconciliation", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}/levers", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/applications/{APP_B}/recommendations", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/tenants/{CLIENT_B}/savings", None, RoleUtilisateur.DIRECTEUR),
    (
        "GET",
        f"/v1/tenants/{CLIENT_B}/pricing/variations",
        None,
        RoleUtilisateur.DIRECTEUR,
    ),
    ("GET", f"/v1/campaigns/{CAMPAGNE_B}", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/campaigns/{CAMPAGNE_B}/results", None, RoleUtilisateur.DIRECTEUR),
    ("GET", f"/v1/rollouts/{BASCULE_B}", None, RoleUtilisateur.DIRECTEUR),
    (
        "POST",
        f"/v1/applications/{APP_B}/campaigns",
        CORPS_CAMPAGNE,
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
    ("POST", f"/v1/tenants/{CLIENT_B}/reports", {}, RoleUtilisateur.RESPONSABLE_COMPTE),
    ("POST", "/v1/chat", {"question": "Coût ?", "tenant_id": CLIENT_B}, RoleUtilisateur.DIRECTEUR),
    (
        "POST",
        f"/v1/applications/{APP_B}/protocoles",
        {"modele_juge_id": "eu.anthropic.claude-3-5-sonnet-20241022-v2:0"},
        RoleUtilisateur.ANALYSTE_FINOPS,
    ),
    (
        "POST",
        f"/v1/applications/{APP_B}/verite-terrain",
        {"porte": "numerique", "reponse_candidate": "1", "valeur_attendue": 1.0},
        RoleUtilisateur.ANALYSTE_FINOPS,
    ),
]


@pytest.mark.parametrize(("methode", "chemin", "corps", "role"), CAS_R11)
def test_client_a_refuse_sur_les_donnees_du_client_b(
    client: TestClient,
    methode: str,
    chemin: str,
    corps: dict[str, object] | None,
    role: RoleUtilisateur,
) -> None:
    """R11 : refus 403 sur chaque point d'API qui expose des données."""
    reponse = client.request(methode, chemin, json=corps, headers=entetes(CLIENT_A, role))
    assert reponse.status_code == 403, f"{methode} {chemin} devrait être refusé"
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"
    assert reponse.json()["error"]["details"]["tenant_demande"] == CLIENT_B


@pytest.mark.parametrize(("methode", "chemin", "corps", "role"), CAS_R11)
def test_refus_journalise_dans_l_audit(
    client: TestClient,
    audit: MemoryAuditStore,
    methode: str,
    chemin: str,
    corps: dict[str, object] | None,
    role: RoleUtilisateur,
) -> None:
    """Chaque refus de cloisonnement est un événement de sécurité (EF-ADM-02)."""
    client.request(methode, chemin, json=corps, headers=entetes(CLIENT_A, role))
    refus = [e for e in audit.lister(tenant_id=CLIENT_B) if not e.succes]
    assert len(refus) == 1
    assert refus[0].statut_http == 403
    assert refus[0].details["motif"] == "cloisonnement"
    assert refus[0].details["tenant_appelant"] == CLIENT_A


def test_client_a_ne_voit_que_ses_propres_donnees(client: TestClient) -> None:
    """Les listes filtrées ne laissent rien fuiter du client B."""
    assert [t["id"] for t in client.get("/v1/tenants", headers=entetes(CLIENT_A)).json()] == [
        CLIENT_A
    ]
    applications = client.get(
        f"/v1/tenants/{CLIENT_A}/applications", headers=entetes(CLIENT_A)
    ).json()
    assert all(a["tenant_id"] == CLIENT_A for a in applications)


def test_ressource_inexistante_rend_404_et_non_403(client: TestClient) -> None:
    """R11 ne doit pas masquer un 404 : une ressource absente reste un 404."""
    reponse = client.get("/v1/applications/app-inexistante", headers=entetes(CLIENT_A))
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"
