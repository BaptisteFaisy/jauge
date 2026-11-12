"""Tests des routes de tarifs : `GET /v1/pricing/catalogue` et variations client.

Deux frontières distinctes sont vérifiées :

- le **catalogue** n'est pas une donnée de client : tout rôle authentifié peut
  lire les prix en vigueur et l'empreinte de la table ;
- les **variations** et leur impact sont cloisonnés : un client ne voit que les
  siennes (le refus inter-clients est couvert par le jeu R11 de
  `tests/api/test_isolation.py`), et la route ne réécrit rien dans le store.

Aucun réseau, aucun AWS : la table de tarifs est celle des fixtures
(`tests/pricing_data/conftest.py`), injectée sur `app.state.catalogue_tarifs`.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import entetes
from tests.pricing_data.conftest import (
    DATE_BAISSE,
    DATE_INITIALE,
    PRIX_APRES,
    table_deux_versions,
)

from jauge.api.app import creer_app
from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.models.pricing import ServiceTier
from jauge.pricing_data.catalogue import CatalogueTarifs
from jauge.store.memory import MemoryStore

CLIENT_A = "t-demo-a"
CLIENT_B = "t-demo-b"
CHEMIN_CATALOGUE = "/v1/pricing/catalogue"


def chemin_variations(tenant_id: str) -> str:
    """Chemin des variations de prix d'un client."""
    return f"/v1/tenants/{tenant_id}/pricing/variations"


@pytest.fixture
def client_tarifs(store: MemoryStore, audit: MemoryAuditStore) -> TestClient:
    """Application de test dont le catalogue est la table à deux versions de prix."""
    app = creer_app(store=store, mode_demo=True, audit=audit)
    app.state.catalogue_tarifs = CatalogueTarifs(lignes_table=table_deux_versions())
    return TestClient(app)


def test_catalogue_publie_prix_courants_et_empreinte(client_tarifs: TestClient) -> None:
    """Le catalogue rend les prix en vigueur, leur validité et l'empreinte datée."""
    reponse = client_tarifs.get(
        CHEMIN_CATALOGUE,
        params={"a_la_date": DATE_BAISSE.isoformat()},
        headers=entetes(CLIENT_A, RoleUtilisateur.DIRECTEUR),
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["a_la_date"] == DATE_BAISSE.isoformat()
    assert corps["nombre_prix"] == 3 == len(corps["prix"])
    assert len(corps["empreinte"]) == 16
    petit = next(p for p in corps["prix"] if p["modele_id"].endswith("haiku-20241022-v1:0"))
    assert petit["prix_entree_1k"] == PRIX_APRES[petit["modele_id"]][0]
    assert petit["date_effet"] == DATE_BAISSE.isoformat()
    assert petit["date_fin"] is None  # ligne en cours : aucune fin de validité


def test_catalogue_filtre_par_region_et_niveau(client_tarifs: TestClient) -> None:
    """Filtres de lecture : région inconnue ou niveau absent → aucun prix."""
    entete = entetes(CLIENT_A, RoleUtilisateur.ANALYSTE_FINOPS)

    assert (
        client_tarifs.get(CHEMIN_CATALOGUE, params={"niveau": "flex"}, headers=entete).json()[
            "nombre_prix"
        ]
        == 0
    )
    assert (
        client_tarifs.get(CHEMIN_CATALOGUE, params={"region": "us-east-1"}, headers=entete).json()[
            "nombre_prix"
        ]
        == 0
    )
    assert (
        client_tarifs.get(
            CHEMIN_CATALOGUE, params={"niveau": ServiceTier.STANDARD.value}, headers=entete
        ).json()["nombre_prix"]
        == 3
    )


def test_catalogue_vide_avant_la_premiere_date_effet(client_tarifs: TestClient) -> None:
    """Avant toute date d'effet, il n'y a pas de prix en vigueur."""
    reponse = client_tarifs.get(
        CHEMIN_CATALOGUE,
        params={"a_la_date": "2025-01-01"},
        headers=entetes(CLIENT_A, RoleUtilisateur.RELECTEUR),
    )

    assert reponse.status_code == 200
    assert reponse.json()["nombre_prix"] == 0
    assert reponse.json()["prix"] == []


def test_catalogue_sans_date_prend_celle_du_jour(client_tarifs: TestClient) -> None:
    """Sans paramètre, le catalogue se place à la date du jour."""
    reponse = client_tarifs.get(
        CHEMIN_CATALOGUE, headers=entetes(CLIENT_A, RoleUtilisateur.DIRECTEUR)
    )

    assert reponse.status_code == 200
    assert reponse.json()["a_la_date"] == date.today().isoformat()


def test_variations_du_client_avec_impact_et_preuves(client_tarifs: TestClient) -> None:
    """Baisse et hausse sont rendues, chiffrées pour le client, avec les preuves caduques."""
    reponse = client_tarifs.get(
        chemin_variations(CLIENT_A),
        params={"depuis": DATE_INITIALE.isoformat(), "jusqu": DATE_BAISSE.isoformat()},
        headers=entetes(CLIENT_A, RoleUtilisateur.ANALYSTE_FINOPS),
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["tenant_id"] == CLIENT_A
    assert len(corps["variations"]) == 2
    par_sens = {v["sens"]: v for v in corps["variations"]}
    assert par_sens["baisse"]["variation_entree_pourcentage"] == 25.0
    assert par_sens["hausse"]["variation_entree_pourcentage"] == -20.0

    # Les deux modèles sont utilisés par le client A : deux impacts chiffrés.
    assert len(corps["impacts"]) == 2
    economies = {i["modele_id"]: i["economie_mensuelle_usd"] for i in corps["impacts"]}
    assert max(economies.values()) > 0 > min(economies.values())
    # Une preuve caduque est signalée, avec son motif et le modèle concerné.
    assert corps["preuves_a_rejouer"]
    assert all(p["motif"] and p["modele_id"] for p in corps["preuves_a_rejouer"])
    assert {p["kind"] for p in corps["preuves_a_rejouer"]} <= {"recommandation", "campagne"}


def test_impact_calcule_seulement_pour_les_modeles_utilises(
    client_tarifs: TestClient,
) -> None:
    """Le client B n'utilise pas les modèles dont le prix bouge : aucun impact."""
    reponse = client_tarifs.get(
        chemin_variations(CLIENT_B),
        params={"depuis": DATE_INITIALE.isoformat(), "jusqu": DATE_BAISSE.isoformat()},
        headers=entetes(CLIENT_B, RoleUtilisateur.DIRECTEUR),
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert len(corps["variations"]) == 2  # les prix ont bien bougé…
    assert corps["impacts"] == []  # …mais ce client n'utilise aucun de ces modèles

    # Ses recommandations portent en revanche sur des modèles dont le prix a
    # changé : leur économie attendue est à recalculer, même sans dépense observée.
    modeles_modifies = {v["modele_id"] for v in corps["variations"]}
    assert corps["preuves_a_rejouer"]
    assert all(p["tenant_id"] == CLIENT_B for p in corps["preuves_a_rejouer"])
    assert all(p["modele_id"] in modeles_modifies for p in corps["preuves_a_rejouer"])


def test_client_inconnu_refuse_en_404(client_tarifs: TestClient) -> None:
    """Un client inexistant n'a pas de variations de prix."""
    reponse = client_tarifs.get(
        chemin_variations("t-inconnu"), headers=entetes("t-inconnu", RoleUtilisateur.DIRECTEUR)
    )

    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_periode_inversee_refusee_en_422(client_tarifs: TestClient) -> None:
    """`depuis` postérieur à `jusqu` : demande invalide, refus explicite."""
    reponse = client_tarifs.get(
        chemin_variations(CLIENT_A),
        params={"depuis": "2026-08-01", "jusqu": "2026-02-01"},
        headers=entetes(CLIENT_A, RoleUtilisateur.ANALYSTE_FINOPS),
    )

    assert reponse.status_code == 422
    erreur = reponse.json()["error"]
    assert erreur["code"] == "validation_error"
    assert erreur["details"]["depuis"] == "2026-08-01"


def test_variations_sans_depuis_partent_de_la_premiere_table(
    client_tarifs: TestClient,
) -> None:
    """Sans `depuis`, la comparaison part de la première table connue."""
    reponse = client_tarifs.get(
        chemin_variations(CLIENT_A), headers=entetes(CLIENT_A, RoleUtilisateur.ANALYSTE_FINOPS)
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["depuis"] is None
    assert len(corps["variations"]) == 2
    assert all(v["date_effet_avant"] == DATE_INITIALE.isoformat() for v in corps["variations"])


def test_lecture_des_variations_ne_reecrit_rien(
    client_tarifs: TestClient, store: MemoryStore
) -> None:
    """Signaler n'est pas décider : le store est inchangé (ENF-IA-02)."""
    avant = sorted((r.id, r.status.value) for r in store.lister_recommandations(CLIENT_A))
    campagnes_avant = [(c.id, c.statut.value) for c in store.lister_campagnes(CLIENT_A)]

    assert (
        client_tarifs.get(
            chemin_variations(CLIENT_A), headers=entetes(CLIENT_A, RoleUtilisateur.ANALYSTE_FINOPS)
        ).status_code
        == 200
    )

    assert sorted((r.id, r.status.value) for r in store.lister_recommandations(CLIENT_A)) == avant
    assert [(c.id, c.statut.value) for c in store.lister_campagnes(CLIENT_A)] == campagnes_avant
