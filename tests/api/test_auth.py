"""Tests d'authentification : mode démo, rôles, vérificateur Cognito, 401 hors démo."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import entetes

from jauge.api.auth import (
    AuthentificationRequiseError,
    CognitoJwksVerifier,
    RoleUtilisateur,
    compte_demonstration_juges,
    extraire_utilisateur_demo,
    mode_demo_actif,
)
from jauge.common.errors import ValidationError


def _jeton(claims: dict[str, object]) -> str:
    """Fabrique un JWT non signé porteur des claims donnés (aucun appel réseau)."""
    entete = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode()
    charge = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"{entete}.{charge}.signature"


def test_en_tetes_demo_resolvent_tenant_et_role(client: TestClient) -> None:
    """En mode démo, `X-Jauge-Tenant` et `X-Jauge-Role` portent le contexte."""
    reponse = client.get(
        "/v1/demo/identity",
        headers=entetes("t-demo-b", RoleUtilisateur.RESPONSABLE_APPLICATION, "alice"),
    )
    assert reponse.status_code == 200
    utilisateur = reponse.json()["utilisateur"]
    assert utilisateur["tenant_id"] == "t-demo-b"
    assert utilisateur["role"] == "responsable_application"
    assert utilisateur["id"] == "alice"


def test_sans_entete_le_compte_de_demonstration_est_servi(client: TestClient) -> None:
    """EF-POR-02 : les juges obtiennent un compte exploitable sans configuration."""
    corps = client.get("/v1/demo/identity").json()
    assert corps["utilisateur"]["role"] == "administrateur_plateforme"
    assert corps["compte_demo_juges"]["tenant_id"] == "t-demo-a"
    assert "responsable_application" in corps["roles_disponibles"]


def test_role_inconnu_est_refuse() -> None:
    """Un rôle inventé est refusé : pas de repli silencieux sur administrateur.

    Un repli sur le compte de démonstration (administrateur de plateforme, tous
    clients) transformerait une faute de frappe en escalade de privilèges.
    """
    with pytest.raises(ValidationError) as erreur:
        extraire_utilisateur_demo("t-demo-a", "pirate")
    assert erreur.value.code == "validation_error"
    assert (
        "pirate" in str(erreur.value.details.get("role_fourni", ""))
        or erreur.value.details.get("role_fourni") == "pirate"
    )


def test_role_inconnu_refuse_par_l_api(client: TestClient) -> None:
    """L'API répond 422 (et pas 200 administrateur) sur un rôle inventé."""
    reponse = client.get("/v1/demo/identity", headers=entetes("t-demo-b", "pirate", "alice"))
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "validation_error"


def test_compte_de_demonstration_est_une_copie() -> None:
    """Le compte de démonstration est copié : pas de mutation partagée."""
    premier = compte_demonstration_juges()
    premier.tenant_id = "t-autre"
    assert compte_demonstration_juges().tenant_id == "t-demo-a"


def test_verificateur_cognito_lit_les_claims() -> None:
    """Le vérificateur Cognito est réellement implémenté (jamais appelé en démo)."""
    jeton = _jeton(
        {
            "sub": "cognito-123",
            "email": "bob@client-a.example",
            "custom:role": "responsable_application",
            "custom:tenant_id": "t-demo-a",
            "custom:tenants": ["t-demo-a"],
        }
    )
    utilisateur = CognitoJwksVerifier().verifier_jeton(jeton)
    assert utilisateur.id == "cognito-123"
    assert utilisateur.role == RoleUtilisateur.RESPONSABLE_APPLICATION
    assert utilisateur.tenant_id == "t-demo-a"
    assert utilisateur.tenants_autorises == ["t-demo-a"]


def test_verificateur_refuse_un_jeton_malforme() -> None:
    """Un jeton sans trois segments est refusé proprement (401)."""
    with pytest.raises(AuthentificationRequiseError):
        CognitoJwksVerifier().verifier_jeton("pas-un-jeton")


def test_hors_demo_sans_jeton_refuse(client_jeton: TestClient) -> None:
    """Hors mode démo, aucun en-tête de démo n'est accepté."""
    reponse = client_jeton.get("/v1/tenants", headers=entetes("t-demo-a"))
    assert reponse.status_code == 401
    assert reponse.json()["error"]["code"] == "unauthenticated"


def test_hors_demo_avec_jeton_bearer_accepte(client_jeton: TestClient) -> None:
    """Un jeton Bearer porteur des bons claims ouvre l'accès."""
    jeton = _jeton(
        {
            "sub": "cognito-456",
            "email": "admin@msp.example",
            "custom:role": "administrateur_plateforme",
        }
    )
    reponse = client_jeton.get("/v1/tenants", headers={"Authorization": f"Bearer {jeton}"})
    assert reponse.status_code == 200
    assert len(reponse.json()) == 2


def test_mode_demo_actif_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le mode démo est actif par défaut (démonstration sans configuration)."""
    monkeypatch.delenv("JAUGE_MODE_DEMO", raising=False)
    assert mode_demo_actif() is True
    monkeypatch.setenv("JAUGE_MODE_DEMO", "0")
    assert mode_demo_actif() is False
