"""Tests du store en mémoire et du cloisonnement par client (ENF-SEC-02).

Le scénario de recette R11 exige un refus sur chaque point d'accès quand un
utilisateur du client A demande les données du client B. Ces tests vérifient le
cloisonnement au niveau du store, avant même la couche API.
"""

from __future__ import annotations

from datetime import date

from jauge.models.campaign import Campagne
from jauge.models.cost import AgregatQuotidien
from jauge.models.lever import Levier, TypeLevier
from jauge.models.tenant import Application, Tenant
from jauge.store.memory import MemoryStore


def store_de_deux_clients(tenant: Tenant, tenant_b: Tenant) -> MemoryStore:
    """Store peuplé avec deux clients et une application chacun."""
    store = MemoryStore()
    store.enregistrer_tenant(tenant)
    store.enregistrer_tenant(tenant_b)
    store.enregistrer_application(
        Application(
            id="ticket-classifier",
            tenant_id=tenant.id,
            nom="Classement",
            equipe="support",
            modele_actuel="grand-modele",
        )
    )
    store.enregistrer_application(
        Application(
            id="kb-search",
            tenant_id=tenant_b.id,
            nom="Recherche",
            equipe="data",
            modele_actuel="grand-modele",
        )
    )
    return store


def test_cloisonnement_des_applications(tenant: Tenant, tenant_b: Tenant) -> None:
    """Le client B ne voit pas l'application du client A (R11)."""
    store = store_de_deux_clients(tenant, tenant_b)
    assert store.obtenir_application(tenant.id, "ticket-classifier") is not None
    assert store.obtenir_application(tenant_b.id, "ticket-classifier") is None
    assert [a.id for a in store.lister_applications(tenant_b.id)] == ["kb-search"]


def test_cloisonnement_des_campagnes(tenant: Tenant, tenant_b: Tenant) -> None:
    """Une campagne du client A est invisible et inaccessible au client B."""
    store = store_de_deux_clients(tenant, tenant_b)
    store.enregistrer_campagne(
        Campagne(
            id="c-0001",
            tenant_id=tenant.id,
            application_id="ticket-classifier",
            modeles_candidats=["petit-modele"],
            cout_estime_usd=1.25,
        )
    )
    assert store.obtenir_campagne(tenant.id, "c-0001") is not None
    assert store.obtenir_campagne(tenant_b.id, "c-0001") is None
    assert store.lister_campagnes(tenant_b.id) == []


def test_cloisonnement_des_leviers(tenant: Tenant, tenant_b: Tenant) -> None:
    """Les leviers détectés restent dans leur client."""
    store = store_de_deux_clients(tenant, tenant_b)
    store.enregistrer_levier(
        Levier(
            id="lv-1",
            tenant_id=tenant.id,
            application_id="ticket-classifier",
            type_levier=TypeLevier.MODELE_SURDIMENSIONNE,
            action_proposee="Rejeu sur des modèles plus petits",
            preuve_exigee="Campagne de rejeu",
            economie_mensuelle_estimee_usd=992.0,
        )
    )
    assert len(store.lister_leviers(tenant.id)) == 1
    assert store.lister_leviers(tenant_b.id) == []


def test_cloisonnement_des_agregats(tenant: Tenant, tenant_b: Tenant) -> None:
    """Les agrégats de coût ne fuient pas d'un client à l'autre."""
    store = store_de_deux_clients(tenant, tenant_b)
    store.enregistrer_agregats(
        [
            AgregatQuotidien(
                tenant_id=tenant.id,
                application_id="ticket-classifier",
                equipe="support",
                environnement="production",
                modele_id="grand-modele",
                jour=date(2026, 6, 1),
                nombre_requetes=100,
                jetons_entree_total=100_000,
                jetons_sortie_total=5_000,
                cout_recalcule_usd=12.5,
            )
        ]
    )
    assert len(store.lister_agregats(tenant.id)) == 1
    assert store.lister_agregats(tenant_b.id) == []


def test_synthese_cout_et_couverture(tenant: Tenant, tenant_b: Tenant) -> None:
    """La synthèse consolide par application, équipe et modèle, avec couverture."""
    store = store_de_deux_clients(tenant, tenant_b)
    store.enregistrer_agregats(
        [
            AgregatQuotidien(
                tenant_id=tenant.id,
                application_id="ticket-classifier",
                equipe="support",
                environnement="production",
                modele_id="grand-modele",
                jour=date(2026, 6, d),
                nombre_requetes=100,
                jetons_entree_total=100_000,
                jetons_sortie_total=5_000,
                cout_recalcule_usd=10.0,
            )
            for d in (1, 2)
        ]
    )
    synthese = store.synthese_cout(tenant.id, date(2026, 6, 1), date(2026, 6, 30))
    assert synthese.cout_total_usd == 20.0
    assert synthese.cout_par_application["ticket-classifier"] == 20.0
    assert synthese.couverture_attribution_pourcentage == 100.0
