"""Tests du chat (`POST /v1/chat`) : chiffres calculés par le code, pas par un LLM."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur


def test_question_repond_avec_des_chiffres_calcules(client: TestClient) -> None:
    """La réponse s'appuie sur les agrégats du store (ENF-IA-01)."""
    reponse = client.post(
        "/v1/chat",
        json={"question": "Pourquoi le classement de tickets coûte-t-il plus ce mois-ci ?"},
        headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["chiffres"]["cout_total_usd"] == 16.2
    assert corps["chiffres"]["leviers_detectes"] == 2
    assert corps["mode"] == "demonstration"
    assert "16.2" in corps["reponse"]


def test_question_ne_renvoie_aucun_texte_de_requete(client: TestClient) -> None:
    """ENF-CONF-01 : la réponse ne contient ni texte de requête ni réponse candidate."""
    corps = client.post(
        "/v1/chat", json={"question": "Où part l'argent ?"}, headers=entetes("t-demo-a")
    ).json()
    assert "texte_requete" not in corps
    assert "reponse_candidate" not in corps


def test_question_vide_refusee(client: TestClient) -> None:
    """Une question faite d'espaces est refusée (422)."""
    reponse = client.post("/v1/chat", json={"question": "   "}, headers=entetes("t-demo-a"))
    assert reponse.status_code == 422


def test_question_sur_le_client_b_refusee(client: TestClient) -> None:
    """R11 : interroger les coûts du client B est refusé au client A."""
    reponse = client.post(
        "/v1/chat",
        json={"question": "Combien coûte le résumé nocturne ?", "tenant_id": "t-demo-b"},
        headers=entetes("t-demo-a"),
    )
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_question_journalisee(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : la question est journalisée sans son texte intégral."""
    client.post(
        "/v1/chat",
        json={"question": "Où part l'argent ce mois-ci ?"},
        headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR, "dir"),
    )
    evenements = audit.lister(action="question_chat")
    assert len(evenements) == 1
    assert evenements[0].utilisateur_id == "dir"
    assert evenements[0].details["longueur_question"] == len("Où part l'argent ce mois-ci ?")
    assert "question" not in evenements[0].details


def test_chat_indisponible_sans_module_m7(client_sans_modules: TestClient) -> None:
    """Tant que M7 (agents Strands) n'est pas branché, la route répond 502."""
    reponse = client_sans_modules.post(
        "/v1/chat", json={"question": "Où part l'argent ?"}, headers=entetes("t-demo-a")
    )
    assert reponse.status_code == 502
    assert reponse.json()["error"]["details"]["module"] == "M7"
