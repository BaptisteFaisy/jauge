"""Tests de bascule et retour arrière (§15, EF-BAS-01..04, scénarios R6 et R7)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.api.conftest import BASCULE_B, RECO_A, RECO_A_APPROUVEE, RECO_B, entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.store.memory import MemoryStore

CHEF = RoleUtilisateur.RESPONSABLE_APPLICATION


def test_bascule_refusee_sans_approbation(client: TestClient) -> None:
    """R6 : aucune bascule sans approbation humaine (ENF-IA-02)."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_A}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    )
    assert reponse.status_code == 409
    corps = reponse.json()
    assert corps["error"]["code"] == "approval_required"
    assert corps["error"]["details"]["statut"] == "PENDING_APPROVAL"


def test_bascule_refusee_journalisee(client: TestClient, audit: MemoryAuditStore) -> None:
    """Le refus de bascule est un événement d'audit en échec (EF-ADM-02)."""
    client.post(f"/v1/recommendations/{RECO_A}/rollout", json={}, headers=entetes("t-demo-a", CHEF))
    evenements = audit.lister(action="bascule_refusee")
    assert len(evenements) == 1
    assert evenements[0].succes is False
    assert evenements[0].statut_http == 409


def test_bascule_demarree_apres_approbation(client: TestClient) -> None:
    """R7 : une recommandation approuvée démarre une bascule à 10 %."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={"motif": "feu vert du responsable"},
        headers=entetes("t-demo-a", CHEF),
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    assert corps["mode"] == "passerelle"
    bascule = corps["bascule"]
    assert bascule["palier_actuel_pourcentage"] == 10
    assert bascule["actif"] is True
    assert bascule["recommandation_id"] == RECO_A_APPROUVEE


def test_bascule_utilise_les_paliers_des_parametres(client: TestClient) -> None:
    """Les paliers viennent des paramètres d'administration (EF-ADM-01)."""
    client.patch(
        "/v1/settings",
        json={"paliers_bascule": [5, 25, 100]},
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    corps = client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    ).json()
    assert corps["bascule"]["palier_actuel_pourcentage"] == 5


def test_bascule_hors_passerelle_ouvre_un_ticket(client: TestClient, store: MemoryStore) -> None:
    """EF-BAS-04 : une application hors passerelle reçoit un ticket, pas une règle."""
    application = store.obtenir_application("t-demo-a", "ticket-classifier")
    store.enregistrer_application(application.model_copy(update={"passe_par_passerelle": False}))
    reponse = client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    )
    assert reponse.status_code == 201
    assert reponse.json()["mode"] == "ticket"
    assert reponse.json()["bascule"] is None


def test_bascule_journalisee(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : la création d'une règle de bascule est journalisée."""
    client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    )
    evenements = audit.lister(action="bascule_demarree")
    assert len(evenements) == 1
    assert evenements[0].ressource_type == "bascule"
    assert evenements[0].details["paliers"] == [10, 50, 100]


def test_lecture_et_retour_arriere(client: TestClient) -> None:
    """`POST /v1/rollouts/{id}/rollback` désactive la bascule et annule la reco."""
    bascule = client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    ).json()["bascule"]

    lecture = client.get(f"/v1/rollouts/{bascule['id']}", headers=entetes("t-demo-a"))
    assert lecture.status_code == 200

    annulation = client.post(
        f"/v1/rollouts/{bascule['id']}/rollback", headers=entetes("t-demo-a", CHEF)
    )
    assert annulation.status_code == 200
    assert annulation.json()["actif"] is False
    assert "Retour arrière" in annulation.json()["motif_annulation"]

    reco = client.get(
        "/v1/applications/ticket-classifier/recommendations", headers=entetes("t-demo-a")
    ).json()
    assert next(r for r in reco if r["id"] == RECO_A_APPROUVEE)["status"] == "ROLLED_BACK"


def test_retour_arriere_double_refuse(client: TestClient) -> None:
    """Une bascule déjà arrêtée ne peut pas être annulée deux fois (409)."""
    bascule = client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    ).json()["bascule"]
    client.post(f"/v1/rollouts/{bascule['id']}/rollback", headers=entetes("t-demo-a", CHEF))
    reponse = client.post(
        f"/v1/rollouts/{bascule['id']}/rollback", headers=entetes("t-demo-a", CHEF)
    )
    assert reponse.status_code == 409
    assert reponse.json()["error"]["code"] == "invalid_state_transition"


def test_retour_arriere_journalise(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : un retour arrière laisse une trace d'audit."""
    bascule = client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    ).json()["bascule"]
    client.post(f"/v1/rollouts/{bascule['id']}/rollback", headers=entetes("t-demo-a", CHEF))
    evenements = audit.lister(action="bascule_annulee")
    assert len(evenements) == 1
    assert evenements[0].ressource_id == bascule["id"]


def test_bascule_du_client_b_refusee(client: TestClient) -> None:
    """R11 : le retour arrière sur la bascule du client B est refusé au client A."""
    reponse = client.post(f"/v1/rollouts/{BASCULE_B}/rollback", headers=entetes("t-demo-a", CHEF))
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_lecture_bascule_du_client_b_refusee(client: TestClient) -> None:
    """R11 : la lecture d'une bascule du client B est refusée au client A."""
    reponse = client.get(f"/v1/rollouts/{BASCULE_B}", headers=entetes("t-demo-a"))
    assert reponse.status_code == 403


def test_rollout_recommandation_du_client_b_refuse(client: TestClient) -> None:
    """R11 : démarrer la bascule d'une recommandation du client B est refusé."""
    reponse = client.post(
        f"/v1/recommendations/{RECO_B}/rollout", json={}, headers=entetes("t-demo-a", CHEF)
    )
    assert reponse.status_code == 403
