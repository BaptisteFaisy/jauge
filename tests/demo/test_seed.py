"""Tests de l'amorçage du store de démonstration (`demo.seed.peupler_store`).

Vérifient le contenu du store (2 clients, 4 applications, agrégats sur 14
jours), le cloisonnement (ENF-SEC-02) et le scénario de démonstration
(recommandation en attente, recommandation refusée, bascule en cours).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from demo.profils import DATE_FIN_DEFAUT, JOURS_DEFAUT, TENANT_A, TENANT_B
from demo.seed import charger_socle_demo, peupler_store, resume

from jauge.models.recommendation import StatutRecommandation
from jauge.store.memory import MemoryStore

JOURS_TEST = 3


@pytest.fixture(scope="module")
def store() -> MemoryStore:
    """Store de démonstration complet (14 jours, graine par défaut)."""
    magasin = MemoryStore()
    peupler_store(magasin)
    return magasin


@pytest.fixture(scope="module")
def store_court() -> MemoryStore:
    """Store de démonstration réduit à trois jours (tests rapides)."""
    magasin = MemoryStore()
    peupler_store(magasin, jours=JOURS_TEST)
    return magasin


def test_deux_clients(store: MemoryStore) -> None:
    """Le store contient les deux clients de démonstration."""
    tenants = store.lister_tenants()
    assert {t.id for t in tenants} == {TENANT_A, TENANT_B}
    client_a = store.obtenir_tenant(TENANT_A)
    assert client_a is not None
    assert client_a.compte_aws == "123456789012"


def test_quatre_applications(store: MemoryStore) -> None:
    """Trois applications chez le client A, une chez le client B (§18.1)."""
    apps_a = {a.id for a in store.lister_applications(TENANT_A)}
    apps_b = {a.id for a in store.lister_applications(TENANT_B)}
    assert apps_a == {"support-assistant", "ticket-classifier", "doc-summarizer"}
    assert apps_b == {"kb-search"}


def test_agregats_sur_14_jours(store: MemoryStore) -> None:
    """Les agrégats couvrent 14 jours consécutifs pour chaque application."""
    debut = DATE_FIN_DEFAUT - timedelta(days=JOURS_DEFAUT - 1)
    for application in store.lister_applications(TENANT_A):
        agregats = store.lister_agregats(TENANT_A, application.id)
        jours = sorted({a.jour for a in agregats})
        assert len(jours) == JOURS_DEFAUT
        assert jours[0] == debut
        assert jours[-1] == DATE_FIN_DEFAUT
        assert all(a.nombre_requetes > 0 for a in agregats)
        assert all(a.cout_recalcule_usd > 0 for a in agregats)


def test_rapprochements_quotidiens_conformes(store: MemoryStore) -> None:
    """Un rapprochement par jour et par client, tous conformes (< 3 %)."""
    for tenant_id in (TENANT_A, TENANT_B):
        rapprochements = store.lister_rapprochements(tenant_id)
        assert len(rapprochements) == JOURS_DEFAUT
        assert all(r.conforme for r in rapprochements)
        assert all(r.ecart_pourcentage < 3.0 for r in rapprochements)


def test_leviers_detectes(store: MemoryStore) -> None:
    """Les six leviers attendus sont présents, avec une économie estimée."""
    leviers = store.lister_leviers(TENANT_A)
    types = {levier.type_levier.value for levier in leviers}
    assert {
        "modele_surdimensionne",
        "cache_prompts_absent",
        "traitement_differable",
        "sorties_trop_longues",
        "requetes_repetees",
        "erreurs_et_relances",
    } <= types
    assert all(levier.economie_mensuelle_estimee_usd > 0 for levier in leviers)
    classement = store.lister_leviers(TENANT_A, "ticket-classifier")
    assert any(levier.type_levier.value == "modele_surdimensionne" for levier in classement)


def test_campagnes_et_recommandations(store: MemoryStore) -> None:
    """Trois campagnes terminées et trois recommandations cohérentes."""
    campagnes = store.lister_campagnes(TENANT_A)
    assert len(campagnes) == 3
    assert all(c.statut.value == "terminee" for c in campagnes)
    assert all(c.taille_echantillon == 200 for c in campagnes)
    assert all(len(c.resultats) >= 2 for c in campagnes)
    recos = store.lister_recommandations(TENANT_A)
    assert len(recos) == 3
    for reco in recos:
        assert reco.campaignId in {c.id for c in campagnes}
        assert reco.cost.expectedMonthlySaving > 0


def test_recommandation_en_attente_et_refusee(store: MemoryStore) -> None:
    """Le classement attend l'approbation, l'assistant est refusé (§18.2)."""
    classement = store.lister_recommandations(TENANT_A, "ticket-classifier")
    assistant = store.lister_recommandations(TENANT_A, "support-assistant")
    assert classement and classement[0].status == StatutRecommandation.EN_ATTENTE_APPROBATION
    assert classement[0].approvedBy is None
    assert assistant and assistant[0].status == StatutRecommandation.REFUSEE
    assert assistant[0].quality.deterministicFailures > 0


def test_bascule_et_verification(store: MemoryStore) -> None:
    """Une bascule est en cours sur le résumé, avec sa vérification."""
    bascules = store.lister_bascules(TENANT_A)
    assert len(bascules) == 1
    bascule = bascules[0]
    assert bascule.application_id == "doc-summarizer"
    assert bascule.actif
    assert bascule.palier_actuel_pourcentage in {10, 50, 100}
    verifications = store.lister_verifications(TENANT_A)
    assert len(verifications) == 1
    assert verifications[0].economie_constatee_usd > 0


def test_synthese_cout_et_couverture(store: MemoryStore) -> None:
    """La synthèse couvre 100 % de la dépense du client A."""
    debut = DATE_FIN_DEFAUT - timedelta(days=JOURS_DEFAUT - 1)
    synthese = store.synthese_cout(TENANT_A, debut, DATE_FIN_DEFAUT)
    assert synthese.cout_total_usd > 0
    assert synthese.couverture_attribution_pourcentage == pytest.approx(100.0)
    assert set(synthese.cout_par_application) == {
        "support-assistant",
        "ticket-classifier",
        "doc-summarizer",
    }
    assert synthese.ecart_rapprochement_moyen_pourcentage < 3.0


def test_cloisonnement_entre_clients(store: MemoryStore) -> None:
    """Le client B ne voit ni les applications ni les agrégats du client A."""
    assert store.obtenir_application(TENANT_B, "ticket-classifier") is None
    assert store.lister_agregats(TENANT_B, "ticket-classifier") == []
    assert store.lister_leviers(TENANT_B) == []
    assert store.lister_campagnes(TENANT_B) == []
    assert store.lister_recommandations(TENANT_B) == []
    assert store.obtenir_application(TENANT_A, "kb-search") is None


def test_resume_console(store: MemoryStore) -> None:
    """Le résumé console cite les applications, les leviers et l'économie."""
    texte = resume(store)
    assert "support-assistant" in texte
    assert "doc-summarizer" in texte
    assert "modele_surdimensionne" in texte
    assert "économie mensuelle estimée" in texte
    assert "rapprochement" in texte


def test_socle_demo_reutilise_les_fixtures() -> None:
    """Les clients et tarifs viennent bien des fixtures du socle."""
    socle = charger_socle_demo()
    assert socle.tenant_a.id == TENANT_A
    assert socle.tenant_b.formule.value == "visibilite"
    assert len(socle.tarifs) == 5
    assert {a.id for a in socle.par_tenant(TENANT_A)} == {
        "support-assistant",
        "ticket-classifier",
        "doc-summarizer",
    }


def test_store_court_reproductible(store_court: MemoryStore) -> None:
    """Deux amorçages avec les mêmes paramètres donnent les mêmes agrégats."""
    autre = MemoryStore()
    peupler_store(autre, jours=JOURS_TEST)
    premier = [
        (a.application_id, a.jour.isoformat(), a.nombre_requetes)
        for a in store_court.lister_agregats(TENANT_A)
    ]
    second = [
        (a.application_id, a.jour.isoformat(), a.nombre_requetes)
        for a in autre.lister_agregats(TENANT_A)
    ]
    assert premier == second
    assert all(isinstance(a.jour, date) for a in store_court.lister_agregats(TENANT_A))
