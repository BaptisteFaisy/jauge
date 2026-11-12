"""Tests d'intégration de la garde kill-switch sur les routes de traitement.

Complément de `tests/api/test_killswitch.py` (qui teste la garde sur une petite
application locale) : on vérifie ici le **câblage réel** sur l'application
`creer_app`, pour les quatre routes qui engagent un traitement :

- `POST /v1/applications/{app_id}/campaigns` → `lancement_campagne` ;
- `POST /v1/recommendations/{reco_id}/decision` → `decision_recommandation` ;
- `POST /v1/tenants/{tenant_id}/reports` → `generation_rapport` ;
- `POST /v1/recommendations/{reco_id}/rollout` → `demarrage_bascule`.

Contrat (`jauge.api.killswitch`) : une fois le kill-switch coupé, tout
traitement du client est refusé en **409** avec le code explicite
`traitement_suspendu`, le refus est journalisé (EF-ADM-02), et la libération
rétablit le traitement sans verrou résiduel. Le kill-switch est coupé **par la
vraie API** (`POST /v1/tenants/{id}/kill-switch`, rôle client habilité).

Aucun réseau, aucun AWS : fixtures amorcées de `tests/api/conftest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import PETIT_MODELE, RECO_A, RECO_A_APPROUVEE, entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.api.killswitch import ACTION_AUDIT_TRAITEMENT_REFUSE, RESSOURCE_KILL_SWITCH

CLIENT_A = "t-demo-a"
MOTIF = "Fuite suspectée dans les journaux : le client coupe tout traitement"

ANALYSTE = RoleUtilisateur.ANALYSTE_FINOPS
COMPTE = RoleUtilisateur.RESPONSABLE_COMPTE
CHEF = RoleUtilisateur.RESPONSABLE_APPLICATION
DIRECTEUR = RoleUtilisateur.DIRECTEUR


@dataclass(frozen=True)
class RouteGardee:
    """Route de traitement gardée par le kill-switch et paramètres de son appel."""

    nom: str
    chemin: str
    corps: dict[str, object]
    role: RoleUtilisateur
    statut_succes: int
    action_garde: str


#: Les quatre routes qui engagent un traitement, avec le rôle habilité à les
#: appeler et le statut attendu une fois le kill-switch rétabli.
ROUTES = (
    RouteGardee(
        nom="campagne",
        chemin="/v1/applications/ticket-classifier/campaigns",
        corps={"modeles_candidats": [PETIT_MODELE], "taille_echantillon": 200},
        role=ANALYSTE,
        statut_succes=201,
        action_garde="lancement_campagne",
    ),
    RouteGardee(
        nom="decision",
        chemin=f"/v1/recommendations/{RECO_A}/decision",
        corps={"decision": "approuver"},
        role=CHEF,
        statut_succes=200,
        action_garde="decision_recommandation",
    ),
    RouteGardee(
        nom="rapport",
        chemin=f"/v1/tenants/{CLIENT_A}/reports",
        corps={"periode_debut": "2026-10-01", "periode_fin": "2026-11-30"},
        role=COMPTE,
        statut_succes=201,
        action_garde="generation_rapport",
    ),
    RouteGardee(
        nom="bascule",
        chemin=f"/v1/recommendations/{RECO_A_APPROUVEE}/rollout",
        corps={},
        role=CHEF,
        statut_succes=201,
        action_garde="demarrage_bascule",
    ),
)

IDS = [route.nom for route in ROUTES]


def basculer_kill_switch(client: TestClient, action: str) -> None:
    """Coupe (`activer`) ou rétablit (`liberer`) le kill-switch par la vraie API."""
    reponse = client.post(
        f"/v1/tenants/{CLIENT_A}/kill-switch",
        json={"action": action, "motif": MOTIF},
        headers=entetes(CLIENT_A, DIRECTEUR, "u-directeur"),
    )
    assert reponse.status_code == 200


def appeler_route(client: TestClient, route: RouteGardee):
    """Appelle la route gardée avec le rôle habilité à engager ce traitement."""
    return client.post(route.chemin, json=route.corps, headers=entetes(CLIENT_A, route.role))


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_traitement_refuse_en_409_quand_kill_switch_coupe(
    client: TestClient, route: RouteGardee
) -> None:
    """Kill-switch coupé : chaque route refuse en 409 `traitement_suspendu`."""
    basculer_kill_switch(client, "activer")
    reponse = appeler_route(client, route)
    assert reponse.status_code == 409
    erreur = reponse.json()["error"]
    assert erreur["code"] == "traitement_suspendu"
    assert erreur["details"]["tenant_id"] == CLIENT_A
    assert erreur["details"]["action"] == route.action_garde
    assert erreur["details"]["motif"] == MOTIF


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_refus_journalise_dans_audit(
    client: TestClient, audit: MemoryAuditStore, route: RouteGardee
) -> None:
    """EF-ADM-02 : le refus du traitement par le kill-switch est journalisé."""
    basculer_kill_switch(client, "activer")
    appeler_route(client, route)
    evenements = audit.lister(
        tenant_id=CLIENT_A,
        action=ACTION_AUDIT_TRAITEMENT_REFUSE,
        ressource_type=RESSOURCE_KILL_SWITCH,
    )
    assert len(evenements) == 1
    evenement = evenements[0]
    assert evenement.succes is False
    assert evenement.statut_http == 409
    assert evenement.details["action"] == route.action_garde
    assert evenement.details["etat"] == "suspendu"


@pytest.mark.parametrize("route", ROUTES, ids=IDS)
def test_traitement_retabli_apres_liberation(client: TestClient, route: RouteGardee) -> None:
    """Kill-switch rétabli : la même route repasse, aucun verrou résiduel."""
    basculer_kill_switch(client, "activer")
    assert appeler_route(client, route).status_code == 409
    basculer_kill_switch(client, "liberer")
    assert appeler_route(client, route).status_code == route.statut_succes
