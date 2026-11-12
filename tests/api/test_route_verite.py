"""Tests de la route des portes à vérité terrain (`POST .../verite-terrain`).

Ce que la route promet :

- elle rend un **verdict**, jamais les textes : ni la réponse, ni la référence,
  ni le code ne sortent dans la réponse ni dans le journal (ENF-CONF-01) ;
- une porte **non applicable** ne bloque rien (`ok=True`, `critique=False`) ;
- le kill-switch du client s'applique : son code n'est plus exécuté ;
- le cloisonnement par client est respecté (ENF-SEC-02).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import entetes

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.evaluation.bac_a_sable import ISOLEMENT_NAMESPACE, niveau_isolation

CLIENT_A = "t-demo-a"
APP_A = "ticket-classifier"
ANALYSTE = RoleUtilisateur.ANALYSTE_FINOPS
MARQUEUR = "TEXTE-CONFIDENTIEL-DU-CLIENT"
CODE = "def double(x):\n    return x * 2\n"
TESTS_JUSTE = "from solution import double\n\n\ndef test_double():\n    assert double(2) == 4\n"
TESTS_FAUX = "from solution import double\n\n\ndef test_double():\n    assert double(2) == 5\n"

#: La porte « code » exécute réellement un sous-processus : elle exige une
#: isolation par le noyau (sinon le bac à sable refuse, par conception).
exige_isolation = pytest.mark.skipif(
    niveau_isolation() != ISOLEMENT_NAMESPACE,
    reason="le test exige une isolation par le noyau (bwrap, unshare ou nsjail)",
)


def test_porte_numerique_tranche_sans_juge(client: TestClient) -> None:
    """La valeur répondante est comparée à la référence : le verdict est rendu."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={
            "porte": "numerique",
            "reponse_candidate": "42",
            "valeur_attendue": 42.0,
        },
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 200, reponse.text
    verdict = reponse.json()
    assert verdict["ok"] is True
    assert verdict["applicable"] is True
    assert verdict["porte"] == "numerique"


def test_porte_numerique_ecartee_bloque(client: TestClient) -> None:
    """Un écart hors tolérance est un échec critique : le candidat est bloqué."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={
            "porte": "numerique",
            "reponse_candidate": "43",
            "valeur_attendue": 42.0,
        },
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    verdict = reponse.json()
    assert verdict["ok"] is False
    assert verdict["critique"] is True


def test_porte_schema_valide_les_cles(client: TestClient) -> None:
    """Le schéma est validé par le code existant, pas par une seconde implémentation."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={
            "porte": "schema",
            "reponse_reference": '{"label": "facturation", "score": 1}',
            "reponse_candidate": '{"label": "support", "score": 0}',
            "cles_attendues": ["label", "score"],
        },
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 200
    assert reponse.json()["ok"] is True


def test_porte_non_configuree_ne_bloque_rien(client: TestClient) -> None:
    """Sans vérité terrain configurée, la porte est non applicable."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={"porte": "numerique", "reponse_candidate": "42"},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    verdict = reponse.json()
    assert verdict["ok"] is True
    assert verdict["applicable"] is False


@exige_isolation
def test_porte_code_execute_les_tests(client: TestClient) -> None:
    """Le code candidat est exécuté : les tests de la tâche décident."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={"porte": "code", "reponse_candidate": CODE, "tests": TESTS_JUSTE},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 200, reponse.text
    verdict = reponse.json()
    assert verdict["ok"] is True
    assert verdict["applicable"] is True


@exige_isolation
def test_porte_code_bloque_un_candidat_faux(client: TestClient) -> None:
    """Un code qui échoue aux tests est un échec critique."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={"porte": "code", "reponse_candidate": CODE, "tests": TESTS_FAUX},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    verdict = reponse.json()
    assert verdict["ok"] is False
    assert verdict["critique"] is True
    assert "test_double" in verdict["motif"]


def test_le_verdict_ne_rend_jamais_le_texte(client: TestClient) -> None:
    """Ni la réponse, ni la référence, ni le code ne sortent dans la réponse."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={
            "porte": "numerique",
            "reponse_candidate": f"le total est de 4300 euros, {MARQUEUR}",
            "valeur_attendue": 42.0,
        },
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert MARQUEUR not in reponse.text
    assert "4300" not in reponse.text
    assert "euros" not in reponse.text


def test_le_journal_ne_rend_jamais_le_texte(client: TestClient, audit: MemoryAuditStore) -> None:
    """L'audit enregistre le verdict, pas les textes (ENF-CONF-01)."""
    client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={
            "porte": "numerique",
            "reponse_candidate": f"42 {MARQUEUR}",
            "valeur_attendue": 42.0,
        },
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    evenements = audit.lister(tenant_id=CLIENT_A, action="verite_terrain_evaluee")
    assert len(evenements) == 1
    assert MARQUEUR not in str(evenements[0].details)
    assert evenements[0].details["porte"] == "numerique"


def test_un_role_client_ne_declenche_pas_de_porte(client: TestClient) -> None:
    """Exécuter du code n'est pas un rôle de client."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={"porte": "code", "reponse_candidate": CODE, "tests": TESTS_JUSTE},
        headers=entetes(CLIENT_A, RoleUtilisateur.DIRECTEUR),
    )

    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "forbidden"


def test_un_client_ne_declenche_pas_de_porte_pour_un_autre(client: TestClient) -> None:
    """Cloisonnement : l'application d'un autre client est inaccessible."""
    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={"porte": "numerique", "reponse_candidate": "42", "valeur_attendue": 42.0},
        headers=entetes("t-demo-b", ANALYSTE),
    )

    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "tenant_isolation_violation"


def test_le_kill_switch_arrete_la_porte(client: TestClient) -> None:
    """Un client qui a coupé ses traitements n'a plus son code exécuté."""
    client.post(
        "/v1/tenants/t-demo-a/kill-switch",
        json={"action": "activer", "motif": "coupure de test"},
        headers=entetes(CLIENT_A, RoleUtilisateur.DIRECTEUR),
    )

    reponse = client.post(
        f"/v1/applications/{APP_A}/verite-terrain",
        json={"porte": "code", "reponse_candidate": CODE, "tests": TESTS_JUSTE},
        headers=entetes(CLIENT_A, ANALYSTE),
    )

    assert reponse.status_code == 409
    assert reponse.json()["error"]["code"] == "traitement_suspendu"
