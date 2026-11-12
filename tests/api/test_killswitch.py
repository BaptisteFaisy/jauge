"""Tests du kill-switch client et des seuils de confidentialité (piste n° 9).

Vérifiés ici : la bascule par un rôle du client, le motif obligatoire, le refus
en 409 `traitement_suspendu` de tout traitement une fois le kill-switch coupé,
la conservation des données déjà produites, le refus en 403 opposé à un rôle MSP
qui tente de gérer le kill-switch, la libération par l'administrateur de
plateforme, le cloisonnement (ENF-SEC-02), l'audit systématique (EF-ADM-02) et
le réglage du seuil k avec son plancher (EF-ADM-01).

Le routeur est monté sur une petite application FastAPI locale : il n'est pas
encore enregistré dans `jauge.api.routes.ROUTEURS` (fichier verrouillé pour ce
module, intégration prévue par PR séparée). Aucun réseau, aucun AWS.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.api.conftest import entetes

from jauge.api.app import creer_app
from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepUtilisateur
from jauge.api.errors import enregistrer_gestionnaires_erreurs
from jauge.api.killswitch import (
    ACTION_AUDIT_BASCULE,
    ACTION_AUDIT_REFUS,
    RESSOURCE_KILL_SWITCH,
    DepKillSwitch,
    garde_traitement,
)
from jauge.api.routes import killswitch as routes_killswitch
from jauge.api.routes.killswitch import ACTION_AUDIT_MODIFICATION_SEUIL
from jauge.api.tenancy import verifier_acces_tenant
from jauge.store.memory import MemoryStore

CLIENT_A = "t-demo-a"
CLIENT_B = "t-demo-b"
MOTIF = "Fuite suspectée dans les journaux : le client coupe tout traitement"

ADMIN = RoleUtilisateur.ADMINISTRATEUR_PLATEFORME
DIRECTEUR = RoleUtilisateur.DIRECTEUR
RESPONSABLE_APP = RoleUtilisateur.RESPONSABLE_APPLICATION
ANALYSTE = RoleUtilisateur.ANALYSTE_FINOPS
COMPTE = RoleUtilisateur.RESPONSABLE_COMPTE

#: Traitements que le kill-switch doit couper (rejeu, jugement, campagne…).
TRAITEMENTS = ("rejeu", "jugement", "campagne", "bascule", "rapport")


def creer_app_locale(store: MemoryStore, audit: MemoryAuditStore) -> FastAPI:
    """Petite application locale : le routeur livré + un traitement gardé."""
    app = FastAPI()
    app.state.store = store
    app.state.audit = audit
    app.state.mode_demo = True
    enregistrer_gestionnaires_erreurs(app)
    app.include_router(routes_killswitch.router)

    @app.post("/v1/tenants/{tenant_id}/traitements-demo/{action}")
    def lancer_traitement_demo(
        tenant_id: str,
        action: str,
        utilisateur: DepUtilisateur,
        kill_switch: DepKillSwitch,
    ) -> dict[str, str]:
        """Traitement fictif (campagne, rejeu, rapport…) gardé par le kill-switch."""
        verifier_acces_tenant(utilisateur, tenant_id, audit, action, "tenant")
        garde_traitement(kill_switch)(tenant_id, action)
        return {"action": action, "tenant_id": tenant_id, "statut": "lance"}

    return app


@pytest.fixture
def client_ks(store: MemoryStore, audit: MemoryAuditStore) -> TestClient:
    """Client de test de l'application locale (routeur kill-switch monté)."""
    return TestClient(creer_app_locale(store, audit))


def basculer(
    client: TestClient,
    action: str,
    role: RoleUtilisateur = DIRECTEUR,
    motif: str = MOTIF,
    tenant: str = CLIENT_A,
    utilisateur: str = "u-client",
):
    """Appelle `POST /v1/tenants/{id}/kill-switch` avec les en-têtes de démo."""
    return client.post(
        f"/v1/tenants/{tenant}/kill-switch",
        json={"action": action, "motif": motif},
        headers=entetes(tenant, role, utilisateur),
    )


def traitement(client: TestClient, action: str, role: RoleUtilisateur = ANALYSTE):
    """Appelle la route de traitement gardée par le kill-switch."""
    return client.post(
        f"/v1/tenants/{CLIENT_A}/traitements-demo/{action}",
        headers=entetes(CLIENT_A, role),
    )


def test_etat_initial_actif(client_ks: TestClient) -> None:
    """Un client sans historique est actif : aucun traitement n'est coupé."""
    reponse = client_ks.get(
        f"/v1/tenants/{CLIENT_A}/kill-switch", headers=entetes(CLIENT_A, DIRECTEUR)
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["etat"] == "actif"
    assert corps["tenant_id"] == CLIENT_A
    assert traitement(client_ks, "campagne").status_code == 200


def test_activation_par_un_role_client(client_ks: TestClient) -> None:
    """Le client coupe le traitement : l'état bascule et le motif est rendu."""
    reponse = basculer(client_ks, "activer", role=RESPONSABLE_APP, utilisateur="u-resp")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["etat"] == "suspendu"
    assert corps["motif"] == MOTIF
    assert corps["modifie_par"] == "u-resp"
    assert corps["modifie_le"]
    lecture = client_ks.get(
        f"/v1/tenants/{CLIENT_A}/kill-switch", headers=entetes(CLIENT_A, DIRECTEUR)
    )
    assert lecture.json()["etat"] == "suspendu"


@pytest.mark.parametrize("motif", ["", "   ", "ab"])
def test_motif_obligatoire(client_ks: TestClient, motif: str) -> None:
    """Un motif absent, vide, blanc ou trop court est refusé (422)."""
    reponse = client_ks.post(
        f"/v1/tenants/{CLIENT_A}/kill-switch",
        json={"action": "activer", "motif": motif},
        headers=entetes(CLIENT_A, DIRECTEUR),
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "validation_error"


def test_motif_absent_refuse(client_ks: TestClient) -> None:
    """Le corps sans motif est refusé : le motif est obligatoire."""
    reponse = client_ks.post(
        f"/v1/tenants/{CLIENT_A}/kill-switch",
        json={"action": "activer"},
        headers=entetes(CLIENT_A, DIRECTEUR),
    )
    assert reponse.status_code == 422


@pytest.mark.parametrize("action", TRAITEMENTS)
def test_traitement_refuse_en_409(client_ks: TestClient, action: str) -> None:
    """Kill-switch coupé : chaque traitement est refusé avec le bon code."""
    assert basculer(client_ks, "activer").status_code == 200
    reponse = traitement(client_ks, action)
    assert reponse.status_code == 409
    erreur = reponse.json()["error"]
    assert erreur["code"] == "traitement_suspendu"
    assert erreur["details"]["tenant_id"] == CLIENT_A
    assert erreur["details"]["motif"] == MOTIF


def test_donnees_deja_produites_conservees(client_ks: TestClient, store: MemoryStore) -> None:
    """Le kill-switch arrête le traitement, il ne supprime aucune donnée."""
    avant_agregats = len(store.lister_agregats(CLIENT_A))
    avant_campagnes = len(store.lister_campagnes(CLIENT_A))
    assert basculer(client_ks, "activer").status_code == 200
    assert len(store.lister_agregats(CLIENT_A)) == avant_agregats
    assert len(store.lister_campagnes(CLIENT_A)) == avant_campagnes
    assert traitement(client_ks, "rapport").status_code == 409


def test_liberation_retablit_le_traitement(client_ks: TestClient) -> None:
    """Après libération par le client, les traitements repassent."""
    assert basculer(client_ks, "activer").status_code == 200
    assert traitement(client_ks, "campagne").status_code == 409
    reponse = basculer(client_ks, "liberer", motif="Incident clos, reprise normale")
    assert reponse.status_code == 200
    assert reponse.json()["etat"] == "actif"
    assert traitement(client_ks, "campagne").status_code == 200


@pytest.mark.parametrize("role", [ANALYSTE, COMPTE])
def test_liberation_refusee_aux_roles_msp(
    client_ks: TestClient, audit: MemoryAuditStore, role: RoleUtilisateur
) -> None:
    """Un rôle MSP ne peut pas libérer le kill-switch à la place du client."""
    assert basculer(client_ks, "activer").status_code == 200
    reponse = basculer(client_ks, "liberer", role=role, utilisateur="u-msp", motif=MOTIF)
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"
    lecture = client_ks.get(
        f"/v1/tenants/{CLIENT_A}/kill-switch", headers=entetes(CLIENT_A, DIRECTEUR)
    )
    assert lecture.json()["etat"] == "suspendu"
    refus = audit.lister(tenant_id=CLIENT_A, action=ACTION_AUDIT_REFUS)
    assert refus and refus[-1].succes is False
    assert refus[-1].details["action_demandee"] == "liberer"


@pytest.mark.parametrize("role", [ANALYSTE, COMPTE])
def test_activation_refusee_aux_roles_msp(client_ks: TestClient, role: RoleUtilisateur) -> None:
    """Le kill-switch est un droit du client : un rôle MSP ne le coupe pas non plus."""
    reponse = basculer(client_ks, "activer", role=role, utilisateur="u-msp")
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_liberation_par_administrateur_avec_motif(
    client_ks: TestClient, audit: MemoryAuditStore
) -> None:
    """L'administrateur de plateforme peut libérer, avec un motif journalisé."""
    assert basculer(client_ks, "activer").status_code == 200
    reponse = basculer(
        client_ks,
        "liberer",
        role=ADMIN,
        utilisateur="u-admin",
        motif="Demande écrite du client reçue par le support",
    )
    assert reponse.status_code == 200
    assert reponse.json()["etat"] == "actif"
    bascules = audit.lister(tenant_id=CLIENT_A, action=ACTION_AUDIT_BASCULE)
    assert len(bascules) == 2
    assert bascules[-1].details["motif"] == "Demande écrite du client reçue par le support"
    assert bascules[-1].utilisateur_id == "u-admin"


def test_audit_conserve_qui_quand_pourquoi(client_ks: TestClient, audit: MemoryAuditStore) -> None:
    """Chaque bascule laisse une trace d'audit complète (EF-ADM-02)."""
    assert basculer(client_ks, "activer", utilisateur="u-directeur").status_code == 200
    evenements = audit.lister(
        tenant_id=CLIENT_A, action=ACTION_AUDIT_BASCULE, ressource_type=RESSOURCE_KILL_SWITCH
    )
    assert len(evenements) == 1
    evenement = evenements[0]
    assert evenement.succes is True
    assert evenement.utilisateur_id == "u-directeur"
    assert evenement.role == DIRECTEUR.value
    assert evenement.details["etat"] == "suspendu"
    assert evenement.details["motif"] == MOTIF
    assert evenement.details["modifie_le"]
    assert evenement.horodatage


def test_cloisonnement_entre_clients(client_ks: TestClient) -> None:
    """Un utilisateur du client B ne touche pas le kill-switch du client A."""
    for methode in ("GET", "POST"):
        reponse = client_ks.request(
            methode,
            f"/v1/tenants/{CLIENT_A}/kill-switch",
            json={"action": "activer", "motif": MOTIF} if methode == "POST" else None,
            headers=entetes(CLIENT_B, DIRECTEUR, "u-b"),
        )
        assert reponse.status_code == 403
        assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_client_inconnu_404(client_ks: TestClient) -> None:
    """Un client inexistant répond 404, pas un état par défaut."""
    reponse = client_ks.get(
        "/v1/tenants/t-inconnu/kill-switch", headers=entetes("t-inconnu", ADMIN)
    )
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_seuil_k_par_defaut(client_ks: TestClient) -> None:
    """La politique de confidentialité par défaut est k=5, epsilon=1, plancher 2."""
    reponse = client_ks.get(
        f"/v1/tenants/{CLIENT_A}/confidentialite", headers=entetes(CLIENT_A, DIRECTEUR)
    )
    assert reponse.status_code == 200
    assert reponse.json() == {"k": 5, "epsilon": 1.0, "plancher_k": 2}


def test_seuil_k_modifiable_par_admin(client_ks: TestClient) -> None:
    """L'administrateur règle k par client ; la valeur est relisible."""
    reponse = client_ks.put(
        f"/v1/tenants/{CLIENT_A}/confidentialite",
        json={"k": 7, "motif": "Exigence contractuelle du client A"},
        headers=entetes(CLIENT_A, ADMIN, "u-admin"),
    )
    assert reponse.status_code == 200
    assert reponse.json()["k"] == 7
    autre = client_ks.get(
        f"/v1/tenants/{CLIENT_B}/confidentialite", headers=entetes(CLIENT_B, ADMIN, "u-admin")
    )
    assert autre.json()["k"] == 5


@pytest.mark.parametrize("k", [0, 1])
def test_plancher_k_refuse(client_ks: TestClient, k: int) -> None:
    """Descendre sous le plancher k=2 est refusé (422)."""
    reponse = client_ks.put(
        f"/v1/tenants/{CLIENT_A}/confidentialite",
        json={"k": k},
        headers=entetes(CLIENT_A, ADMIN, "u-admin"),
    )
    assert reponse.status_code == 422


def test_modification_du_seuil_journalisee(client_ks: TestClient, audit: MemoryAuditStore) -> None:
    """Le réglage du seuil laisse une trace d'audit avec k et le motif."""
    client_ks.put(
        f"/v1/tenants/{CLIENT_A}/confidentialite",
        json={"k": 8, "motif": "Demande du RSSI client"},
        headers=entetes(CLIENT_A, ADMIN, "u-admin"),
    )
    evenements = audit.lister(tenant_id=CLIENT_A, action=ACTION_AUDIT_MODIFICATION_SEUIL)
    assert len(evenements) == 1
    assert evenements[0].details["k"] == 8
    assert evenements[0].details["motif"] == "Demande du RSSI client"
    assert evenements[0].utilisateur_id == "u-admin"


def test_reglage_du_seuil_refuse_aux_non_admin(client_ks: TestClient) -> None:
    """Le réglage des seuils est réservé à l'administrateur de plateforme."""
    reponse = client_ks.put(
        f"/v1/tenants/{CLIENT_A}/confidentialite",
        json={"k": 9},
        headers=entetes(CLIENT_A, DIRECTEUR),
    )
    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_routeur_montable_sur_l_application_reelle(
    store: MemoryStore, audit: MemoryAuditStore
) -> None:
    """Le routeur se monte sur l'application réelle `creer_app`, sans conflit.

    En intégration le routeur rejoint `jauge.api.routes.ROUTEURS` (fichier
    verrouillé pour ce module), donc **avant** le montage du portail statique :
    `creer_app` monte le portail en dernier et ce montage attrape tous les
    chemins. On retire ici le montage pour reproduire l'ordre réel.
    """
    app = creer_app(store=store, mode_demo=True, audit=audit)
    app.router.routes = [
        route for route in app.router.routes if getattr(route, "name", None) != "portal"
    ]
    app.include_router(routes_killswitch.router)
    client = TestClient(app)
    assert basculer(client, "activer").status_code == 200
    lecture = client.get(
        f"/v1/tenants/{CLIENT_A}/kill-switch", headers=entetes(CLIENT_A, DIRECTEUR)
    )
    assert lecture.json()["etat"] == "suspendu"
    assert (
        client.get(
            f"/v1/tenants/{CLIENT_A}/confidentialite", headers=entetes(CLIENT_A, DIRECTEUR)
        ).json()["k"]
        == 5
    )
