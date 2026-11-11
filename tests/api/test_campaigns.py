"""Tests des campagnes de rejeu (§15, EF-REJ, ENF-COUT-01, ENF-CONF-01)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import APP_B, CAMPAGNE_A, CAMPAGNE_B, PETIT_MODELE, entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

CORPS_CAMPAGNE = {
    "modeles_candidats": [PETIT_MODELE],
    "taille_echantillon": 200,
    "graine_aleatoire": 42,
}


def test_lancement_campagne(client: TestClient) -> None:
    """`POST .../campaigns` crée une campagne planifiée via le port M4 de démo."""
    reponse = client.post(
        "/v1/applications/ticket-classifier/campaigns",
        json=CORPS_CAMPAGNE,
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["statut"] == "planifiee"
    assert corps["taille_echantillon"] == 200
    assert corps["cout_estime_usd"] > 0


def test_lancement_campagne_journalise(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : chaque campagne laisse une trace d'audit."""
    client.post(
        "/v1/applications/ticket-classifier/campaigns",
        json=CORPS_CAMPAGNE,
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS, "ines"),
    )
    evenements = audit.lister(action="campagne_lancee")
    assert len(evenements) == 1
    assert evenements[0].tenant_id == "t-demo-a"
    assert evenements[0].utilisateur_id == "ines"
    assert evenements[0].ressource_type == "campagne"


def test_lancement_campagne_refuse_si_plafond_depasse(client: TestClient) -> None:
    """ENF-COUT-01 : un coût estimé au-dessus du plafond du client est refusé."""
    corps = dict(CORPS_CAMPAGNE, taille_echantillon=2000, modeles_candidats=[PETIT_MODELE] * 3)
    reponse = client.post(
        "/v1/applications/ticket-classifier/campaigns",
        json=corps,
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "campaign_cost_cap_exceeded"
    assert reponse.json()["error"]["details"]["plafond_usd"] == 50.0


def test_lancement_campagne_modele_non_autorise(client: TestClient) -> None:
    """Le client valide la liste des candidats : un modèle hors liste est refusé."""
    corps = dict(CORPS_CAMPAGNE, modeles_candidats=["modele-inconnu"])
    reponse = client.post(
        "/v1/applications/ticket-classifier/campaigns",
        json=corps,
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "validation_error"


def test_lancement_campagne_application_inconnue(client: TestClient) -> None:
    """Une application inconnue renvoie 404."""
    reponse = client.post(
        "/v1/applications/app-fantome/campaigns",
        json=CORPS_CAMPAGNE,
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 404


def test_lancement_campagne_reserve_aux_roles_msp(client: TestClient) -> None:
    """Un directeur client ne lance pas de campagne (§6)."""
    reponse = client.post(
        "/v1/applications/ticket-classifier/campaigns",
        json=CORPS_CAMPAGNE,
        headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR),
    )
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_lecture_campagne(client: TestClient) -> None:
    """`GET /v1/campaigns/{id}` rend l'état et les coûts de la campagne."""
    reponse = client.get(f"/v1/campaigns/{CAMPAGNE_A}", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    assert reponse.json()["statut"] == "terminee"
    assert reponse.json()["cout_reel_usd"] == 1.31


def test_lecture_campagne_inconnue(client: TestClient) -> None:
    """Une campagne inconnue renvoie 404."""
    reponse = client.get("/v1/campaigns/c-fantome", headers=entetes("t-demo-a"))
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_resultats_par_candidat(client: TestClient) -> None:
    """`GET /v1/campaigns/{id}/results` rend les scores agrégés par candidat."""
    reponse = client.get(f"/v1/campaigns/{CAMPAGNE_A}/results", headers=entetes("t-demo-a"))
    assert reponse.status_code == 200
    resultats = reponse.json()
    assert len(resultats) == 2
    assert resultats[1]["taux_equivalence"] == 0.97
    assert resultats[1]["conforme_seuils"] is True


def test_resultats_ne_contiennent_aucun_texte(client: TestClient) -> None:
    """ENF-CONF-01 : aucun texte de requête ni de réponse ne sort du plan de données."""
    resultats = client.get(
        f"/v1/campaigns/{CAMPAGNE_A}/results", headers=entetes("t-demo-a")
    ).json()
    for resultat in resultats:
        assert "texte_requete" not in resultat
        assert "reponse_candidate" not in resultat
        assert "reponse_reference" not in resultat


def test_campagne_du_client_b_refusee(client: TestClient) -> None:
    """R11 : la campagne du client B est refusée au client A."""
    reponse = client.get(f"/v1/campaigns/{CAMPAGNE_B}", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_resultats_du_client_b_refuses(client: TestClient) -> None:
    """R11 : les résultats de campagne du client B sont refusés au client A."""
    reponse = client.get(f"/v1/campaigns/{CAMPAGNE_B}/results", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403


def test_campagne_sur_application_du_client_b_refusee(client: TestClient) -> None:
    """R11 : lancer une campagne sur l'application du client B est refusé."""
    reponse = client.post(
        f"/v1/applications/{APP_B}/campaigns",
        json=CORPS_CAMPAGNE,
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    assert reponse.status_code == 403
