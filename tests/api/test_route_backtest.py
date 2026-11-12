"""Tests de la route de backtest contrefactuel (piste n° 5, EF-VER-01, ENF-SEC-02).

Le routeur n'est **pas** enregistré dans `jauge/api/routes/__init__.py` (fichier
verrouillé pour ce lot) : ces tests le montent sur une petite application
FastAPI locale, avec le store, le journal d'audit et la table des tarifs posés
dans `app.state`. Les chiffres attendus sont ceux du mini-jeu vérifié à la main
dans `tests/replay/test_backtest.py` (60 requêtes, 5,40 $ réels, 0,288 $ au
tarif du candidat, 5,112 $ d'économie).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.api.conftest import APP_B, entetes
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE

from jauge.api.audit import MemoryAuditStore
from jauge.api.auth import RoleUtilisateur
from jauge.api.errors import enregistrer_gestionnaires_erreurs
from jauge.api.routes.backtest import router
from jauge.engine.calculator import TableTarifs
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import TarifModele
from jauge.models.tenant import Application, Tenant
from jauge.store.memory import MemoryStore

CLIENT_A = "t-demo-a"
CLIENT_B = "t-demo-b"
APP_A = "ticket-classifier"
EQUIPE_A = "support"
JOURS = (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3))
REQUETES_PAR_JOUR = (10, 20, 30)
COUT_REEL_PAR_JOUR = (0.9, 1.8, 2.7)
JETONS_PAR_REQUETE = 1000
CHEMIN = f"/v1/applications/{APP_A}/backtest"
PARAMETRES = {"modele_candidat": PETIT_MODELE, "debut": "2026-10-01", "fin": "2026-10-03"}


def _agregat(
    tenant_id: str,
    app_id: str,
    equipe: str,
    modele: str,
    jour: date,
    requetes: int,
    cout_reel: float,
    jetons_par_requete: int = JETONS_PAR_REQUETE,
) -> AgregatQuotidien:
    """Agrégat quotidien, tel que produit par le module d'attribution."""
    return AgregatQuotidien(
        tenant_id=tenant_id,
        application_id=app_id,
        equipe=equipe,
        environnement="production",
        modele_id=modele,
        jour=jour,
        nombre_requetes=requetes,
        jetons_entree_total=requetes * jetons_par_requete,
        jetons_sortie_total=requetes * jetons_par_requete,
        cout_recalcule_usd=cout_reel,
    )


def _agregats_client_a() -> list[AgregatQuotidien]:
    """Mini-jeu du client A, plus un agrégat sans jetons (non évaluable)."""
    agregats = [
        _agregat(CLIENT_A, APP_A, EQUIPE_A, GRAND_MODELE, jour, requetes, cout)
        for jour, requetes, cout in zip(JOURS, REQUETES_PAR_JOUR, COUT_REEL_PAR_JOUR, strict=True)
    ]
    agregats.append(
        _agregat(CLIENT_A, APP_A, EQUIPE_A, MOYEN_MODELE, JOURS[0], 4, 0.0, jetons_par_requete=0)
    )
    return agregats


@pytest.fixture
def store_backtest(
    tenant: Tenant, tenant_b: Tenant, app_classement: Application, app_b: Application
) -> MemoryStore:
    """Store amorcé : deux clients, deux applications, des agrégats chiffrés."""
    memoire = MemoryStore()
    for client in (tenant, tenant_b):
        memoire.enregistrer_tenant(client)
    for application in (app_classement, app_b):
        memoire.enregistrer_application(application)
    memoire.enregistrer_agregats(_agregats_client_a())
    memoire.enregistrer_agregats(
        [_agregat(CLIENT_B, APP_B, app_b.equipe, app_b.modele_actuel, JOURS[0], 50, 0.6)]
    )
    return memoire


@pytest.fixture
def audit_backtest() -> MemoryAuditStore:
    """Journal d'audit de l'application locale."""
    return MemoryAuditStore()


@pytest.fixture
def client_backtest(
    store_backtest: MemoryStore,
    audit_backtest: MemoryAuditStore,
    tarifs: list[TarifModele],
) -> Iterator[TestClient]:
    """Application locale : le routeur de backtest monté hors `ROUTEURS`."""
    application = FastAPI()
    application.state.store = store_backtest
    application.state.audit = audit_backtest
    application.state.mode_demo = True
    application.state.tarifs = TableTarifs(tarifs)
    enregistrer_gestionnaires_erreurs(application)
    application.include_router(router)
    yield TestClient(application)


def test_route_repond_200_avec_les_champs_attendus(client_backtest: TestClient) -> None:
    """Le backtest rend le coût réel, le coût contrefactuel et l'économie."""
    reponse = client_backtest.get(CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A))
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["tenant_id"] == CLIENT_A
    assert corps["application_id"] == APP_A
    assert corps["modele_actuel"] == GRAND_MODELE
    assert corps["modele_candidat"] == PETIT_MODELE
    assert corps["periode_debut"] == "2026-10-01"
    assert corps["periode_fin"] == "2026-10-03"
    assert corps["cout_reel_usd"] == pytest.approx(5.4)
    assert corps["cout_contrefactuel_usd"] == pytest.approx(0.288)
    assert corps["economie_usd"] == pytest.approx(5.112)
    assert corps["economie_pourcentage"] == pytest.approx(94.67)
    assert corps["cout_par_requete_reel_usd"] == pytest.approx(0.09)
    assert corps["cout_par_requete_contrefactuel_usd"] == pytest.approx(0.0048)
    assert corps["economie_projection_mensuelle_usd"] == pytest.approx(51.12)


def test_hypotheses_portees_dans_la_reponse(client_backtest: TestClient) -> None:
    """La réponse rappelle que c'est une projection, pas une économie constatée."""
    corps = client_backtest.get(CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A)).json()
    hypotheses = corps["hypotheses"]
    assert hypotheses["volume_constant"] is True
    assert hypotheses["memes_jetons"] is True
    assert hypotheses["qualite_non_verifiee"] is True
    assert "Projection contrefactuelle" in hypotheses["mention"]
    assert any("mêmes requêtes" in ligne for ligne in hypotheses["liste"])


def test_intervalles_ordonnes_et_volume_constant(client_backtest: TestClient) -> None:
    """P10 ≤ P50 ≤ P90, et le nombre de requêtes ne bouge pas."""
    corps = client_backtest.get(CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A)).json()
    assert corps["economie_p10_usd"] <= corps["economie_p50_usd"] <= corps["economie_p90_usd"]
    assert corps["economie_p10_usd"] < corps["economie_p90_usd"]
    assert corps["nombre_requetes"] == sum(REQUETES_PAR_JOUR)
    assert corps["jours_observes"] == 3
    assert corps["donnees_absentes"] is False


def test_ventilation_par_dimension_et_cles_iso(client_backtest: TestClient) -> None:
    """La ventilation par jour utilise des clés ISO et retombe sur le total."""
    corps = client_backtest.get(CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A)).json()
    ventilation = corps["ventilation"]
    assert ventilation["par_application"][APP_A] == pytest.approx(5.112)
    assert ventilation["par_equipe"][EQUIPE_A] == pytest.approx(5.112)
    assert ventilation["par_modele"][GRAND_MODELE] == pytest.approx(5.112)
    assert sorted(ventilation["par_jour"]) == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert sum(ventilation["par_jour"].values()) == pytest.approx(corps["economie_usd"])


def test_requetes_non_evaluables_comptees(client_backtest: TestClient) -> None:
    """Un agrégat sans jetons est exclu du calcul et compté explicitement."""
    corps = client_backtest.get(CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A)).json()
    assert corps["requetes_non_evaluables"] == 4
    assert corps["nombre_requetes"] == 60


def test_periode_sans_donnees_reponse_vide_explicite(client_backtest: TestClient) -> None:
    """Période vide : 200, `donnees_absentes` à vrai, aucun zéro divisé."""
    reponse = client_backtest.get(
        CHEMIN,
        params={"modele_candidat": PETIT_MODELE, "debut": "2020-01-01", "fin": "2020-01-31"},
        headers=entetes(CLIENT_A),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["donnees_absentes"] is True
    assert corps["nombre_requetes"] == 0
    assert corps["economie_usd"] == 0.0
    assert corps["economie_pourcentage"] == 0.0
    assert corps["ventilation"]["par_jour"] == {}


def test_tarif_candidat_absent_refuse(client_backtest: TestClient) -> None:
    """Un candidat absent de la table datée : 422 `pricing_missing`, jamais 0 $."""
    reponse = client_backtest.get(
        CHEMIN,
        params={
            "modele_candidat": "demo-modele-inconnu",
            "debut": "2026-10-01",
            "fin": "2026-10-03",
        },
        headers=entetes(CLIENT_A),
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "pricing_missing"


def test_modele_candidat_obligatoire(client_backtest: TestClient) -> None:
    """Sans modèle candidat, la requête est refusée par la validation."""
    reponse = client_backtest.get(
        CHEMIN, params={"debut": "2026-10-01", "fin": "2026-10-03"}, headers=entetes(CLIENT_A)
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "validation_error"


def test_application_inconnue(client_backtest: TestClient) -> None:
    """Une application inexistante rend 404."""
    reponse = client_backtest.get(
        "/v1/applications/app-fantome/backtest", params=PARAMETRES, headers=entetes(CLIENT_A)
    )
    assert reponse.status_code == 404
    assert reponse.json()["error"]["code"] == "not_found"


def test_client_a_refuse_sur_application_du_client_b(client_backtest: TestClient) -> None:
    """R11 : l'application du client B est refusée au client A (ENF-SEC-02)."""
    reponse = client_backtest.get(
        f"/v1/applications/{APP_B}/backtest", params=PARAMETRES, headers=entetes(CLIENT_A)
    )
    assert reponse.status_code == 403
    corps = reponse.json()["error"]
    assert corps["code"] == "tenant_isolation_violation"
    assert corps["details"]["tenant_demande"] == CLIENT_B
    assert corps["details"]["tenant_appelant"] == CLIENT_A


def test_refus_journalise_dans_l_audit(
    client_backtest: TestClient, audit_backtest: MemoryAuditStore
) -> None:
    """Le refus de cloisonnement est un événement d'audit en échec."""
    client_backtest.get(
        f"/v1/applications/{APP_B}/backtest", params=PARAMETRES, headers=entetes(CLIENT_A)
    )
    refus = [e for e in audit_backtest.lister(tenant_id=CLIENT_B) if not e.succes]
    assert len(refus) == 1
    assert refus[0].statut_http == 403
    assert refus[0].details["motif"] == "cloisonnement"
    assert refus[0].details["tenant_appelant"] == CLIENT_A


def test_appel_reussi_journalise(
    client_backtest: TestClient, audit_backtest: MemoryAuditStore
) -> None:
    """Chaque backtest est audité : candidat, période et résultat chiffré."""
    client_backtest.get(CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A))
    evenements = audit_backtest.lister(tenant_id=CLIENT_A, action="lecture_backtest")
    assert len(evenements) == 1
    evenement = evenements[0]
    assert evenement.ressource_type == "application"
    assert evenement.ressource_id == APP_A
    assert evenement.succes is True
    assert evenement.details["modele_candidat"] == PETIT_MODELE
    assert evenement.details["periode_debut"] == "2026-10-01"
    assert evenement.details["economie_usd"] == pytest.approx(5.112)


def test_lecture_autorisee_pour_un_role_msp(client_backtest: TestClient) -> None:
    """Un analyste FinOps du MSP lit le backtest de son client."""
    reponse = client_backtest.get(
        CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A, RoleUtilisateur.ANALYSTE_FINOPS)
    )
    assert reponse.status_code == 200
    assert reponse.json()["economie_usd"] == pytest.approx(5.112)


def test_aucun_texte_de_requete_dans_la_reponse(client_backtest: TestClient) -> None:
    """ENF-CONF-01 : la réponse ne porte aucun texte de requête ni de réponse."""
    corps = client_backtest.get(CHEMIN, params=PARAMETRES, headers=entetes(CLIENT_A)).json()

    def cles(noeud: object, prefixe: str = "") -> Iterator[str]:
        if isinstance(noeud, dict):
            for cle, valeur in noeud.items():
                yield f"{prefixe}{cle}"
                yield from cles(valeur, f"{prefixe}{cle}.")
        elif isinstance(noeud, list):
            for item in noeud:
                yield from cles(item, prefixe)

    toutes = list(cles(corps))
    assert toutes, "la réponse ne doit pas être vide"
    assert not any("texte" in cle for cle in toutes)
    assert not any("reponse_candidate" in cle for cle in toutes)
