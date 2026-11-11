"""Tests du journal d'audit (EF-ADM-02, §16) et de sa lecture cloisonnée."""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
from tests.api.conftest import PETIT_MODELE, RECO_A, RECO_A_APPROUVEE, entetes

from jauge.api.audit import EvenementAudit, MemoryAuditStore
from jauge.api.auth import RoleUtilisateur

ADMIN = RoleUtilisateur.ADMINISTRATEUR_PLATEFORME


def _activite_complete(client: TestClient) -> None:
    """Produit une campagne, une décision et une bascule pour peupler l'audit."""
    client.post(
        "/v1/applications/ticket-classifier/campaigns",
        json={"modeles_candidats": [PETIT_MODELE], "taille_echantillon": 200},
        headers=entetes("t-demo-a", RoleUtilisateur.ANALYSTE_FINOPS),
    )
    client.post(
        f"/v1/recommendations/{RECO_A}/decision",
        json={"decision": "approuver"},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )
    client.post(
        f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        json={},
        headers=entetes("t-demo-a", RoleUtilisateur.RESPONSABLE_APPLICATION),
    )


def _evenement_client_b() -> EvenementAudit:
    """Événement d'audit rattaché au client B (donnée de test)."""
    return EvenementAudit(
        tenant_id="t-demo-b",
        utilisateur_id="u-b",
        role="directeur",
        action="lecture",
        ressource_type="tenant",
        ressource_id="t-demo-b",
    )


def test_campagne_decision_bascule_journalisees(
    client: TestClient, audit: MemoryAuditStore
) -> None:
    """EF-ADM-02 : campagne, décision et bascule laissent chacune une trace."""
    _activite_complete(client)
    actions = {evenement.action for evenement in audit.lister(tenant_id="t-demo-a")}
    assert {"campagne_lancee", "decision_recommandation", "bascule_demarree"} <= actions


def test_evenement_complet(client: TestClient, audit: MemoryAuditStore) -> None:
    """Un événement porte identifiant, horodatage, auteur, rôle et ressource."""
    _activite_complete(client)
    evenement = next(iter(audit.lister(action="decision_recommandation")))
    assert evenement.id.startswith("a-")
    # Horodatage ISO-8601 avec fuseau (le socle formate en heure locale).
    assert datetime.fromisoformat(evenement.horodatage).tzinfo is not None
    assert evenement.tenant_id == "t-demo-a"
    assert evenement.utilisateur_id == "u-test"
    assert evenement.role == "responsable_application"
    assert evenement.ressource_id == RECO_A
    assert evenement.succes is True


def test_lecture_audit_cloisonnee(client: TestClient, audit: MemoryAuditStore) -> None:
    """Un utilisateur client ne lit que les événements de son client."""
    audit.enregistrer(_evenement_client_b())
    _activite_complete(client)
    evenements = client.get("/v1/audit", headers=entetes("t-demo-a")).json()
    assert evenements
    assert all(e["tenant_id"] == "t-demo-a" for e in evenements)


def test_lecture_audit_complete_pour_l_administrateur(client: TestClient) -> None:
    """L'administrateur de plateforme voit les événements de tous les clients."""
    client.post(
        "/v1/tenants/t-demo-b/reports",
        json={},
        headers=entetes("t-demo-b", RoleUtilisateur.RESPONSABLE_COMPTE),
    )
    evenements = client.get("/v1/audit", headers=entetes("t-demo-a", ADMIN)).json()
    assert {e["tenant_id"] for e in evenements} == {"t-demo-b"}


def test_audit_vide_au_demarrage(client: TestClient) -> None:
    """Le journal est vide tant qu'aucune action n'a eu lieu."""
    assert client.get("/v1/audit", headers=entetes("t-demo-a")).json() == []
