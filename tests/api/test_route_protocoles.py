"""Tests de la route de pré-enregistrement du protocole (`POST .../protocoles`).

Ce que la route promet :

- le protocole est construit à partir de l'**application du client** (ses seuils,
  pas ceux de l'évaluateur) ;
- il est **scellé** : empreinte posée, objet immuable ;
- l'empreinte ne dépend que du contenu, pas de l'ordre de saisie ;
- aucune réponse d'un autre client n'est accessible (ENF-SEC-02), et le journal
  d'audit ne contient aucun texte (ENF-CONF-01).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import CAMPAGNE_A, entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

CLIENT_A = "t-demo-a"
APP_A = "ticket-classifier"
ANALYSTE = RoleUtilisateur.ANALYSTE_FINOPS
JEUGE = "eu.anthropic.claude-3-5-sonnet-20241022-v2:0"
CANDIDAT = "anthropic.claude-3-5-haiku-20241022-v1:0"


def corps(**champs) -> dict[str, object]:
    """Corps de la demande de pré-enregistrement."""
    return {"modeles_candidats": [CANDIDAT], "modele_juge_id": JEUGE, **champs}


def test_le_protocole_est_scelle(client: TestClient) -> None:
    """La route rend un protocole scellé, avec son empreinte et son sceau."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps(),
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 201, reponse.text
    protocole = reponse.json()
    assert protocole["scelle"] is not None
    assert protocole["scelle"]["empreinte"]
    assert protocole["scelle"]["scelle_par"]
    assert protocole["version"]
    # Les seuils viennent de l'application du client (0.95 / 0.90).
    assert protocole["seuils"]["seuil_equivalence"] == 0.95
    assert protocole["seuils"]["borne_basse_intervalle"] == 0.90
    assert protocole["modele_juge_id"] == JEUGE


def test_le_plan_est_fige_avant_le_rejeu(client: TestClient) -> None:
    """Taille, fenêtre et graines sont fixées dans le protocole scellé."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps(taille_echantillon=300, fenetre_jours=7, graines=[3, 1, 3]),
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 201
    plan = reponse.json()["plan"]
    assert plan["taille"] == 300
    assert plan["fenetre_jours"] == 7
    # Les graines sont triées et dédupliquées : l'empreinte ne dépend que du contenu.
    assert plan["graines"] == [1, 3]


def test_l_empreinte_ne_depend_que_du_contenu(client: TestClient) -> None:
    """Même contenu, même empreinte — l'ordre de saisie n'entre pas en ligne de compte."""
    premier = client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps(strates=["z", "a"], exclusions=["x"]),
        headers=entetes(CLIENT_A, ANALYSTE),
    ).json()
    second = client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps(strates=["a", "z"], exclusions=["x"]),
        headers=entetes(CLIENT_A, ANALYSTE),
    ).json()

    assert premier["scelle"]["empreinte"] == second["scelle"]["empreinte"]
    assert premier["plan"]["strates"] == ["a", "z"]


def test_un_role_client_ne_preregistre_pas(client: TestClient) -> None:
    """Pré-enregistrer une méthode n'est pas un rôle de client (ENF-IA-02)."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps(),
        headers=entetes(CLIENT_A, RoleUtilisateur.DIRECTEUR),
    )

    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_un_client_ne_preenregistre_pas_pour_un_autre(client: TestClient) -> None:
    """Cloisonnement : l'application d'un autre client est inaccessible."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps(),
        headers=entetes("t-demo-b", ANALYSTE),
    )

    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_le_scellement_est_journalise(client: TestClient, audit: MemoryAuditStore) -> None:
    """Le scellement laisse une trace d'audit, sans aucun texte de requête."""
    client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps(),
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    evenements = audit.lister(tenant_id=CLIENT_A, action="protocole_scelle")
    assert len(evenements) == 1
    details = evenements[0].details
    assert details["empreinte"]
    assert details["modele_juge_id"] == JEUGE


def test_le_protocole_d_une_campagne_est_lisible(client: TestClient) -> None:
    """La campagne restitue les empreintes de son protocole et de son échantillon."""
    reponse = client.get(
        f"/v1/campagnes/{CAMPAGNE_A}/protocole", headers=entetes(CLIENT_A, ANALYSTE)
    )

    assert reponse.status_code == 200, reponse.text
    corps_reponse = reponse.json()
    assert "protocole_empreinte" in corps_reponse
    assert "echantillon_empreinte" in corps_reponse


def test_le_protocole_d_une_campagne_est_cloisonne(client: TestClient) -> None:
    """La campagne d'un autre client reste inaccessible."""
    reponse = client.get(
        f"/v1/campagnes/{CAMPAGNE_A}/protocole", headers=entetes("t-demo-b", ANALYSTE)
    )

    assert reponse.status_code == 403


@pytest.mark.parametrize("champ", ["modele_juge_id", "taille_echantillon", "marge_non_inferiorite"])
def test_le_corps_est_valide(client: TestClient, champ: str) -> None:
    """Un corps invalide est refusé avant toute action (422)."""
    corps_invalide = corps()
    corps_invalide[champ] = "" if champ == "modele_juge_id" else -1

    reponse = client.post(
        f"/v1/applications/{APP_A}/protocoles",
        json=corps_invalide,
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 422
