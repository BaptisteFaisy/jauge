"""Finitions : sonde de vie, bascule câblée sur M6, schéma d'attribution, ASGI.

Ces tests couvrent les quatre écarts fermés par le lot M18 :

- `/health` : la sonde attendue par la passerelle API du plan de contrôle
  (`infra/control-plane`) existe et ne dépend d'aucune authentification ;
- **É5** : `POST /v1/recommendations/{id}/rollout` passe par le
  `ControlleurBascule` de M6 quand le port est câblé — la règle de passerelle est
  réellement créée, les paliers avancent, le retour arrière retire la règle ;
- **É7** : `POST /v1/tenants` accepte et rend le schéma d'attribution (EF-ATT-03) ;
- le point d'entrée ASGI documenté (`uvicorn jauge.api.app:app`) existe, sert le
  portail et un store amorcé par la démonstration.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import RECO_A, entetes

from jauge.api.app import creer_app, ports_par_defaut
from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.integration.bascules import AdaptateurBascules
from jauge.rollout.gateway import FakePasserelleClient
from jauge.store.memory import MemoryStore

CHEF = RoleUtilisateur.RESPONSABLE_APPLICATION


def approuver(client: TestClient, reco_id: str = RECO_A) -> None:
    """Fait approuver la recommandation par le responsable (ENF-IA-02).

    Le contrôleur M6 exige le nom de la personne qui a approuvé : une
    recommandation simplement marquée APPROUVEE en base ne suffit pas.
    """
    reponse = client.post(
        f"/v1/recommendations/{reco_id}/decision",
        json={"decision": "approuver", "motif": "feu vert du responsable"},
        headers=entetes("t-demo-a", CHEF, "alice"),
    )
    assert reponse.status_code == 200, reponse.text


@pytest.fixture
def passerelle() -> FakePasserelleClient:
    """Passerelle hors ligne dont on peut observer les règles créées."""
    return FakePasserelleClient()


@pytest.fixture
def client_cable(store: MemoryStore, audit: MemoryAuditStore, passerelle: FakePasserelleClient):
    """Application dont le port de bascule est branché sur le contrôleur M6."""
    ports = ports_par_defaut(store, mode_demo=True)
    ports.bascules = AdaptateurBascules(store, passerelle=passerelle)
    return TestClient(creer_app(store=store, mode_demo=True, audit=audit, ports=ports))


def test_sonde_de_vie_sans_authentification(client: TestClient) -> None:
    """`/health` répond sans jeton : c'est la sonde de la passerelle API."""
    reponse = client.get("/health")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["status"] == "ok"
    assert corps["mode_demo"] is True
    assert corps["clients_enregistres"] == 2


def test_sonde_de_vie_hors_demo(client_jeton: TestClient) -> None:
    """La sonde reste ouverte hors mode démo (aucune donnée client exposée)."""
    reponse = client_jeton.get("/health")
    assert reponse.status_code == 200
    assert reponse.json()["mode_demo"] is False


def test_bascule_creee_par_le_controleur_m6(
    client_cable: TestClient, passerelle: FakePasserelleClient
) -> None:
    """É5 fermé : la règle de passerelle est réellement créée au premier palier."""
    approuver(client_cable)
    reponse = client_cable.post(
        f"/v1/recommendations/{RECO_A}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    )
    assert reponse.status_code == 201
    bascule = reponse.json()["bascule"]
    assert bascule["palier_actuel_pourcentage"] == 10
    regles = passerelle.lister_regles("gw-ticket-classifier")
    assert len(regles) == 1
    assert regles[0].id == bascule["regle_passerelle_id"]
    assert regles[0].ponderee is True


def test_paliers_avancent_jusqu_a_100_pourcent(
    client_cable: TestClient, passerelle: FakePasserelleClient
) -> None:
    """Les paliers 10 → 50 → 100 sont franchis par le contrôleur M6 (EF-BAS-02).

    Le troisième appel clôture la bascule à 100 % (`actif` à faux) : c'est la
    sémantique du contrôleur M6, vérifiée par ses propres tests.
    """
    approuver(client_cable)
    bascule = client_cable.post(
        f"/v1/recommendations/{RECO_A}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    ).json()["bascule"]
    assert bascule["palier_actuel_pourcentage"] == 10

    paliers = []
    for _ in range(3):
        reponse = client_cable.post(
            f"/v1/rollouts/{bascule['id']}/advance", headers=entetes("t-demo-a", CHEF)
        )
        assert reponse.status_code == 200, reponse.text
        etat = reponse.json()
        paliers.append((etat["palier_actuel_pourcentage"], etat["actif"]))

    assert paliers == [(50, True), (100, True), (100, False)]
    assert passerelle.lire_regle(bascule["regle_passerelle_id"]) is not None


def test_retour_arriere_retire_la_regle(
    client_cable: TestClient, passerelle: FakePasserelleClient
) -> None:
    """Le retour arrière retire la règle de la passerelle (EF-BAS-03)."""
    approuver(client_cable)
    bascule = client_cable.post(
        f"/v1/recommendations/{RECO_A}/rollout",
        json={},
        headers=entetes("t-demo-a", CHEF),
    ).json()["bascule"]
    assert passerelle.lire_regle(bascule["regle_passerelle_id"]) is not None

    reponse = client_cable.post(
        f"/v1/rollouts/{bascule['id']}/rollback", headers=entetes("t-demo-a", CHEF)
    )
    assert reponse.status_code == 200
    assert reponse.json()["actif"] is False
    assert passerelle.lire_regle(bascule["regle_passerelle_id"]) is None


def test_avance_refusee_sans_controleur(client: TestClient) -> None:
    """Sans port de bascule câblé, l'avancement refuse explicitement (pas de 500)."""
    reponse = client.post("/v1/rollouts/ro-inexistant/advance", headers=entetes("t-demo-a", CHEF))
    assert reponse.status_code in (404, 409)


def test_schema_attribution_accepte_et_rendu(client: TestClient) -> None:
    """É7 fermé : le schéma d'attribution se règle à la création (EF-ATT-03)."""
    reponse = client.post(
        "/v1/tenants",
        json={
            "nom": "Client C",
            "compte_aws": "333344445555",
            "role_arn": "arn:aws:iam::333344445555:role/jauge",
            "schema_attribution": {
                "dimensions": ["application", "team", "environment", "cost_center"],
                "valeurs_equipes": ["support", "data"],
                "application_par_defaut": "non-attribue-c",
            },
        },
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 201
    schema = reponse.json()["schema_attribution"]
    assert schema["dimensions"] == ["application", "team", "environment", "cost_center"]
    assert schema["application_par_defaut"] == "non-attribue-c"

    identifiant = reponse.json()["id"]
    relu = client.get(
        f"/v1/tenants/{identifiant}",
        headers=entetes(identifiant, RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert relu.status_code == 200
    assert relu.json()["schema_attribution"]["valeurs_equipes"] == ["support", "data"]


def test_schema_attribution_par_defaut(client: TestClient) -> None:
    """Sans schéma fourni, les dimensions par défaut du socle s'appliquent."""
    reponse = client.post(
        "/v1/tenants",
        json={
            "nom": "Client D",
            "compte_aws": "444455556666",
            "role_arn": "arn:aws:iam::444455556666:role/jauge",
        },
        headers=entetes("t-demo-a", RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 201
    assert reponse.json()["schema_attribution"]["dimensions"] == [
        "application",
        "team",
        "environment",
    ]


def test_point_entree_asgi_amorce_et_portail() -> None:
    """`uvicorn jauge.api.app:app` : objet créé à la demande, store amorcé, portail servi."""
    import jauge.api.app as module_app

    application = module_app.app
    with TestClient(application) as client_asgi:
        sante = client_asgi.get("/health")
        assert sante.status_code == 200
        assert sante.json()["clients_enregistres"] >= 2
        assert sante.json()["bascules_cablees"] is True

        portal = client_asgi.get("/")
        assert portal.status_code == 200
        assert "<html" in portal.text.lower()

        economies = client_asgi.get(
            "/v1/tenants/t-demo-a/savings", headers=entetes("t-demo-a", RoleUtilisateur.DIRECTEUR)
        )
        assert economies.status_code == 200
