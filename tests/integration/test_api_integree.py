"""L'API du plan de contrôle répond avec les **services réels** (M17).

Chaque route clé est exercée sur le store amorcé par le chemin de production :
coûts, rapprochement, leviers, campagne de rejeu, résultats, recommandations,
décision humaine, bascule, économies, rapport mensuel et chat.

Aucun appel réseau : le mode démonstration utilise les doubles hors ligne.
"""

from __future__ import annotations

import pytest
from tests.integration.conftest import Environnement, entetes, periode

TENANT = "t-demo-a"
APPLICATION = "ticket-classifier"
CANDIDAT = "anthropic.claude-3-5-sonnet-20241022-v2:0"


@pytest.fixture(scope="module")
def campagne_api(client) -> str:
    """Campagne lancée **via l'API** (port M4 branché sur le module réel)."""
    reponse = client.post(
        f"/v1/applications/{APPLICATION}/campaigns",
        json={"modeles_candidats": [CANDIDAT], "taille_echantillon": 60, "graine_aleatoire": 7},
        headers=entetes(TENANT, "analyste_finops"),
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["id"]


def test_couts_et_rapprochement(client, environnement: Environnement) -> None:
    """Les coûts et le rapprochement viennent de l'attribution (M1)."""
    debut, fin = periode(environnement.records)
    reponse = client.get(
        f"/v1/applications/{APPLICATION}/costs",
        params={"debut": debut.isoformat(), "fin": fin.isoformat()},
        headers=entetes(TENANT),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["cout_total_usd"] > 0
    assert corps["requetes_total"] > 0
    rapprochement = client.get(f"/v1/tenants/{TENANT}/reconciliation", headers=entetes(TENANT))
    assert rapprochement.status_code == 200
    assert rapprochement.json(), "aucun jour rapproché"


def test_leviers_depuis_le_detecteur(client, campagne_api: str) -> None:
    """Les leviers affichés sont ceux du détecteur (M3)."""
    reponse = client.get(f"/v1/applications/{APPLICATION}/levers", headers=entetes(TENANT))
    assert reponse.status_code == 200
    leviers = reponse.json()
    assert leviers, "aucun levier détecté"
    economies = [levier["economie_mensuelle_estimee_usd"] for levier in leviers]
    assert economies == sorted(economies, reverse=True)


def test_campagne_lancee_par_lapi(client, campagne_api: str) -> None:
    """La campagne créée par l'API est terminée et porte ses résultats."""
    reponse = client.get(f"/v1/campaigns/{campagne_api}", headers=entetes(TENANT))
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["statut"] == "terminee"
    assert corps["cout_estime_usd"] > 0
    resultats = client.get(f"/v1/campaigns/{campagne_api}/results", headers=entetes(TENANT))
    assert resultats.status_code == 200
    assert len(resultats.json()) >= 2


def test_recommandation_decision_et_bascule(client, campagne_api: str) -> None:
    """Décision humaine puis bascule par paliers via l'API (ENF-IA-02)."""
    from jauge.agents.schemas import RedactionRecommandation, composer_recommandation
    from jauge.models.recommendation import StatutRecommandation

    app = client.app
    services = app.state.services
    entrees = services.entrees.valeurs(TENANT, campagne_api)
    recommandation = composer_recommandation(
        entrees,
        RedactionRecommandation(
            risks=["Surveillance de la qualité en ligne à chaque palier."],
            status=StatutRecommandation.EN_ATTENTE_APPROBATION,
        ),
    )
    services.enregistrement.enregistrer(TENANT, recommandation)

    liste = client.get(f"/v1/applications/{APPLICATION}/recommendations", headers=entetes(TENANT))
    assert liste.status_code == 200
    assert any(item["id"] == recommandation.id for item in liste.json())

    decision = client.post(
        f"/v1/recommendations/{recommandation.id}/decision",
        json={"decision": "approuver", "motif": "preuve de rejeu conforme"},
        headers=entetes(TENANT, "responsable_application"),
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "APPROVED"

    bascule = client.post(
        f"/v1/recommendations/{recommandation.id}/rollout",
        json={"paliers": [10, 50, 100]},
        headers=entetes(TENANT, "responsable_application"),
    )
    assert bascule.status_code == 201
    assert bascule.json()["mode"] == "passerelle"
    assert bascule.json()["bascule"]["palier_actuel_pourcentage"] == 10


def test_economies_et_rapport(client, environnement: Environnement) -> None:
    """Les économies et le rapport mensuel sont servis par les modules (M6/M9)."""
    economies = client.get(f"/v1/tenants/{TENANT}/savings", headers=entetes(TENANT))
    assert economies.status_code == 200
    debut, fin = periode(environnement.records)
    rapport = client.post(
        f"/v1/tenants/{TENANT}/reports",
        json={"periode_debut": debut.isoformat(), "periode_fin": fin.isoformat()},
        headers=entetes(TENANT, "analyste_finops"),
    )
    assert rapport.status_code == 201, rapport.text
    corps = rapport.json()
    assert corps["tenant_id"] == TENANT
    assert corps["cout_total_usd"] >= 0
    lecture = client.get(f"/v1/reports/{corps['id']}", headers=entetes(TENANT))
    assert lecture.status_code == 200


def test_chat_repond_avec_des_chiffres_calcules(client) -> None:
    """Le chat est branché sur les agents ; en démo la réponse est déterministe."""
    reponse = client.post(
        "/v1/chat",
        json={"question": "Pourquoi le classement de tickets coûte-t-il autant ?"},
        headers=entetes(TENANT),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["mode"] == "demonstration"
    assert corps["chiffres"]["cout_total_usd"] > 0
    assert "texte_requete" not in corps
    assert "reponse_candidate" not in corps


def test_audit_journalise_les_actions(client, environnement: Environnement) -> None:
    """Les actions sensibles laissent une trace (EF-ADM-02)."""
    reponse = client.get("/v1/audit", headers=entetes(TENANT))
    assert reponse.status_code == 200
    actions = {evenement["action"] for evenement in reponse.json()}
    assert "question_chat" in actions or "campagne_lancee" in actions
