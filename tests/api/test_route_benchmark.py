"""Tests du benchmark inter-clients `GET /v1/benchmarks/parc` (EF-RAP-02).

La logique d'anonymisation est déjà testée en unitaire dans
`tests/reports/test_anonymisation.py`. On vérifie ici le **câblage de la route** :

- l'habilitation : seuls les rôles MSP transverse voient une comparaison
  inter-clients, un rôle client reçoit 403 sans aucun chiffre ;
- la politique appliquée est la **plus stricte** des clients concernés
  (k le plus élevé, epsilon le plus faible) — jamais la plus permissive ;
- la k-anonymité est effective : une cellule fondée sur moins de k clients
  n'est pas publiée, elle est supprimée ou regroupée dans « autres » ;
- chaque valeur publiée emporte son `epsilon` et sa `sensibilite` ;
- la publication est journalisée (EF-ADM-02) et ne cite aucun client.

Aucun réseau, aucun AWS : fixtures amorcées de `tests/api/conftest.py`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import entetes
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.reports.anonymisation import K_PLANCHER
from jauge.store.memory import MemoryStore

CLIENT_A = "t-demo-a"
CLIENT_B = "t-demo-b"
JOUR_SEME = "2026-11-01"
ACTION_AUDIT_PUBLICATION = "benchmark_parc_publie"

#: Epsilon élevé : le bruit laplacien (sensibilité / epsilon) devient négligeable,
#: ce qui permet de comparer la valeur publiée au total réel sans tolérance large.
EPSILON_FAIBLE_BRUIT = 10.0
TOLERANCE_BRUIT = 1.0

ROLES_MSP = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.ANALYSTE_FINOPS,
    RoleUtilisateur.RESPONSABLE_COMPTE,
)
ROLES_CLIENT = (
    RoleUtilisateur.DIRECTEUR,
    RoleUtilisateur.RESPONSABLE_APPLICATION,
    RoleUtilisateur.RELECTEUR,
)
CHAMPS_REPONSE = (
    "k_applique",
    "epsilon",
    "sensibilite",
    "nombre_supprimees",
    "nombre_regroupees",
    "valeurs",
)


def regler_seuils(client: TestClient, tenant: str, k: int, epsilon: float | None = None) -> None:
    """Règle la politique de confidentialité d'un client (rôle administrateur)."""
    corps: dict[str, object] = {"k": k, "motif": "réglage de test"}
    if epsilon is not None:
        corps["epsilon"] = epsilon
    reponse = client.put(
        f"/v1/tenants/{tenant}/confidentialite",
        json=corps,
        headers=entetes(tenant, RoleUtilisateur.ADMINISTRATEUR_PLATEFORME),
    )
    assert reponse.status_code == 200, reponse.text


def ouvrir_deux_clients(client: TestClient, epsilon: float = EPSILON_FAIBLE_BRUIT) -> None:
    """Abaisse k au plancher chez les deux clients : la publication devient possible."""
    regler_seuils(client, CLIENT_A, K_PLANCHER, epsilon)
    regler_seuils(client, CLIENT_B, K_PLANCHER, epsilon)


def benchmark(
    client: TestClient,
    role: RoleUtilisateur = RoleUtilisateur.ANALYSTE_FINOPS,
    dimension: str = "jour",
):
    """Appelle le benchmark avec le rôle donné (client MSP par défaut)."""
    return client.get(
        f"/v1/benchmarks/parc?dimension={dimension}",
        headers=entetes(CLIENT_A, role),
    )


def cout_total_seme(store: MemoryStore) -> float:
    """Somme exacte des coûts recalculés des deux clients (valeur non bruitée)."""
    agregats = store.lister_agregats(CLIENT_A) + store.lister_agregats(CLIENT_B)
    return round(sum(agregat.cout_recalcule_usd for agregat in agregats), 6)


@pytest.mark.parametrize("role", ROLES_CLIENT)
def test_role_client_refuse_en_403(client: TestClient, role: RoleUtilisateur) -> None:
    """Un rôle client ne voit pas la comparaison inter-clients (ENF-SEC-02)."""
    reponse = benchmark(client, role)
    assert reponse.status_code == 403
    erreur = reponse.json()["error"]
    assert erreur["code"] == "forbidden"
    assert erreur["details"]["role"] == role.value
    # Aucun chiffre d'autres clients ne fuit dans le refus.
    assert "valeurs" not in erreur
    assert CLIENT_B not in reponse.text


@pytest.mark.parametrize("role", ROLES_MSP)
def test_roles_msp_autorises(client: TestClient, role: RoleUtilisateur) -> None:
    """Les trois rôles MSP transverse obtiennent la publication complète."""
    reponse = benchmark(client, role)
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    for champ in CHAMPS_REPONSE:
        assert champ in corps
    assert corps["k_applique"] >= K_PLANCHER


def test_valeur_publiee_porte_epsilon_et_effectif(client: TestClient, store: MemoryStore) -> None:
    """Une cellule fondée sur k clients est publiée, avec son epsilon et son k."""
    ouvrir_deux_clients(client)
    corps = benchmark(client).json()

    assert corps["k_applique"] == K_PLANCHER
    assert len(corps["valeurs"]) == 1
    valeur = corps["valeurs"][0]
    assert valeur["cle"] == JOUR_SEME
    assert valeur["entites"] == 2  # les deux clients contribuent à ce jour
    assert valeur["epsilon"] == corps["epsilon"] == EPSILON_FAIBLE_BRUIT
    assert valeur["sensibilite"] == corps["sensibilite"]
    assert valeur["k"] == K_PLANCHER
    assert valeur["regroupee"] is False
    # Le bruit est négligeable à cet epsilon : on retrouve le total réel.
    assert valeur["valeur"] == pytest.approx(cout_total_seme(store), abs=TOLERANCE_BRUIT)


def test_k_le_plus_eleve_l_emporte(client: TestClient) -> None:
    """Un client plus prudent impose son k aux autres : la cellule disparaît."""
    ouvrir_deux_clients(client)
    regler_seuils(client, CLIENT_B, 8)

    corps = benchmark(client).json()
    assert corps["k_applique"] == 8
    # Deux clients contributeurs < k = 8 : rien ne peut être publié.
    assert corps["valeurs"] == []
    assert corps["nombre_supprimees"] == 1


def test_epsilon_le_plus_faible_l_emporte(client: TestClient) -> None:
    """L'epsilon retenu est le plus faible : le seuil le plus protecteur gagne."""
    ouvrir_deux_clients(client, epsilon=EPSILON_FAIBLE_BRUIT)
    regler_seuils(client, CLIENT_B, K_PLANCHER, 0.25)

    corps = benchmark(client).json()
    assert corps["epsilon"] == 0.25
    assert corps["k_applique"] == K_PLANCHER


def test_cellules_sous_seuil_regroupees_en_autres(client: TestClient) -> None:
    """Par modèle, chaque cellule ne repose que sur un client : regroupement."""
    ouvrir_deux_clients(client)
    corps = benchmark(client, dimension="modele").json()

    assert corps["nombre_regroupees"] == 3
    assert [valeur["cle"] for valeur in corps["valeurs"]] == ["autres"]
    autres = corps["valeurs"][0]
    assert autres["regroupee"] is True
    assert autres["entites"] == 3
    # Le détail par modèle — qui trahirait la dépense d'un seul client — est absent.
    texte = benchmark(client, dimension="modele").text
    for modele in (GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE):
        assert modele not in texte


def test_publication_journalisee(client: TestClient, audit: MemoryAuditStore) -> None:
    """EF-ADM-02 : la publication inter-clients laisse une trace d'audit."""
    ouvrir_deux_clients(client)
    assert benchmark(client).status_code == 200

    evenements = audit.lister(tenant_id=CLIENT_A, action=ACTION_AUDIT_PUBLICATION)
    assert len(evenements) == 1
    details = evenements[0].details
    assert details["k_applique"] == K_PLANCHER
    assert details["epsilon"] == EPSILON_FAIBLE_BRUIT
    assert details["nombre_supprimees"] == 0


def test_aucun_identifiant_de_client_ne_fuit(client: TestClient) -> None:
    """La publication traverse la frontière des clients sans jamais les nommer."""
    ouvrir_deux_clients(client)
    for dimension in ("jour", "modele", "equipe", "application"):
        texte = benchmark(client, dimension=dimension).text
        assert CLIENT_A not in texte
        assert CLIENT_B not in texte
