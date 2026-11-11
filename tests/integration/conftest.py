"""Fixtures partagées des tests d'intégration (M17).

Tous les tests de ce répertoire tournent **100 % hors ligne** : aucun appel
AWS, aucun modèle distant, aucun réseau. Ils s'appuient sur le store en mémoire
(`jauge.store.memory.MemoryStore`), le plan de données (`PlanDeDonnees`) et les
adaptateurs de `jauge.integration`.

Le socle de démonstration (`demo.socle`) fournit les deux clients et les quatre
applications ; le trafic synthétique (`demo.traffic`) alimente l'attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from demo.cur import generer_lignes_cur
from demo.profils import DATE_FIN_DEFAUT, GRAINE_DEFAUT, TENANT_A, TENANT_B
from demo.seed import charger_socle_demo, comptes_demo
from demo.socle import SocleDemo
from demo.traffic import JournalInvocation, generer_trafic, lignes_cloudwatch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jauge.agents.ports import Services
from jauge.api.audit import MemoryAuditStore
from jauge.engine.calculator import TableTarifs
from jauge.integration import (
    Clients,
    PlanDeDonnees,
    ServiceAttribution,
    ServiceDetectionLeviers,
    construire_contexte_levier,
    creer_app,
    creer_services,
    remplir_plan_de_donnees,
)
from jauge.integration.leviers import parametres_demonstration
from jauge.models.tenant import FormuleOffre
from jauge.pricing_data.seed import table_demo
from jauge.store.memory import MemoryStore

#: Fenêtre courte : les tests n'ont pas besoin des 14 jours de la démo.
JOURS_TESTS = 3

#: Ratio « sortie longue » aligné sur celui de la démonstration.
RATIO_SORTIE_LONGUE = 1.4


@dataclass(frozen=True)
class Environnement:
    """Contexte d'un test d'intégration : store, plan, référentiel, tarifs."""

    store: MemoryStore
    plan: PlanDeDonnees
    socle: SocleDemo
    tarifs: TableTarifs
    records: list[JournalInvocation]
    lignes: list[dict]
    lignes_cur: list[dict]
    audit: MemoryAuditStore


@pytest.fixture(scope="module")
def environnement() -> Environnement:
    """Store et plan de données amorcés par le chemin de production (M1)."""
    store = MemoryStore()
    socle = charger_socle_demo()
    store.enregistrer_tenant(socle.tenant_a)
    store.enregistrer_tenant(socle.tenant_b)
    for application in socle.applications.values():
        store.enregistrer_application(application)
    tarifs = table_demo()
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=JOURS_TESTS, date_fin=DATE_FIN_DEFAUT)
    lignes = lignes_cloudwatch(records)
    lignes_cur = [
        ligne.vers_dictionnaire()
        for ligne in generer_lignes_cur(records, tarifs, GRAINE_DEFAUT, comptes_demo())
    ]
    attribution = ServiceAttribution(store, tarifs)
    plan = PlanDeDonnees()
    for tenant_id, lignes_tenant in attribution.lignes_par_tenant(lignes).items():
        resultat = attribution.enregistrer_agregats(tenant_id, lignes_tenant)
        attribution.rapprocher(tenant_id, resultat, lignes_cur)
        remplir_plan_de_donnees(
            attribution.enrichir(tenant_id, lignes_tenant),
            tenant_id,
            plan,
            ratio_sortie_longue=RATIO_SORTIE_LONGUE,
        )
    _detecter_leviers(store, plan, tarifs, records)
    return Environnement(
        store=store,
        plan=plan,
        socle=socle,
        tarifs=tarifs,
        records=records,
        lignes=lignes,
        lignes_cur=lignes_cur,
        audit=MemoryAuditStore(),
    )


@pytest.fixture(scope="module")
def services(environnement: Environnement) -> Services:
    """Les douze ports assemblés par la fabrique sur le store amorcé."""
    return creer_services(
        environnement.store,
        mode_demo=True,
        clients=Clients(audit=environnement.audit),
        tarifs=environnement.tarifs,
        plan=environnement.plan,
    )


@pytest.fixture(scope="module")
def app(environnement: Environnement) -> FastAPI:
    """Application du plan de contrôle branchée sur les services réels."""
    return creer_app(
        mode_demo=True,
        store=environnement.store,
        audit=environnement.audit,
        tarifs=environnement.tarifs,
        plan=environnement.plan,
    )


@pytest.fixture(scope="module")
def client(app: FastAPI) -> TestClient:
    """Client HTTP de test (aucun réseau)."""
    return TestClient(app)


@pytest.fixture(scope="module")
def campagne(services) -> str:
    """Campagne de rejeu exécutée par M4 puis notée par M5 (client A)."""
    candidats = [
        candidat.modele_id
        for candidat in services.candidats.modeles_candidats("t-demo-a", "ticket-classifier")
    ]
    return services.rejeu.lancer("t-demo-a", "ticket-classifier", candidats, 80, 42)


def entetes(tenant_id: str = TENANT_A, role: str = "administrateur_plateforme") -> dict[str, str]:
    """En-têtes de démonstration attendus par l'API."""
    return {
        "X-Jauge-Tenant": tenant_id,
        "X-Jauge-Role": role,
        "X-Jauge-User": "test-integration",
    }


def _detecter_leviers(
    store: MemoryStore, plan: PlanDeDonnees, tarifs: TableTarifs, records: list
) -> None:
    """Fait tourner le détecteur réel (M3) sur les clients sous optimisation."""
    fin = max(record.event_time.date() for record in records)
    detection = ServiceDetectionLeviers(store)
    for tenant in store.lister_tenants():
        if tenant.formule == FormuleOffre.VISIBILITE:
            continue
        for application in store.lister_applications(tenant.id):
            profil = plan.profil(tenant.id, application.id)
            if profil is None:
                continue
            contexte = construire_contexte_levier(
                store,
                tenant.id,
                application.id,
                profil,
                parametres_demonstration(fin, tenant.region_principale),
                tarifs,
            )
            detection.detecter(contexte)


def periode(records: list[JournalInvocation]) -> tuple[date, date]:
    """Bornes de la période couverte par le trafic de test."""
    jours = sorted({record.event_time.date() for record in records})
    return jours[0], jours[-1]


__all__ = ["TENANT_A", "TENANT_B", "entetes", "periode"]
