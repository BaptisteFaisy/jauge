"""Tests du scénario de démonstration (campagnes, recommandations, bascule).

Vérifient la cohérence du récit du §18.2 : le classement passe le seuil et
attend l'approbation, l'assistant est refusé avec preuve, le résumé bascule au
niveau Flex. Les chiffres viennent du code, jamais d'un modèle.
"""

from __future__ import annotations

import pytest
from demo.analyse import analyser_trafic, construire_leviers
from demo.profils import DATE_FIN_DEFAUT, GRAINE_DEFAUT, PROFILS
from demo.scenario import (
    construire_bascules,
    construire_campagnes,
    construire_recommandations,
    construire_verifications,
)
from demo.seed import charger_socle_demo
from demo.traffic import generer_trafic

from jauge.engine.calculator import TableTarifs
from jauge.models.recommendation import StatutRecommandation

JOURS_TEST = 3


@pytest.fixture(scope="module")
def scenario() -> dict:
    """Statistiques, campagnes, recommandations et bascules de trois jours."""
    socle = charger_socle_demo()
    table = TableTarifs(socle.tarifs)
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=JOURS_TEST)
    stats = analyser_trafic(records, table, PROFILS, JOURS_TEST, DATE_FIN_DEFAUT)
    campagnes = construire_campagnes(stats, table, DATE_FIN_DEFAUT)
    recos = construire_recommandations(stats, campagnes)
    return {
        "stats": stats,
        "table": table,
        "campagnes": campagnes,
        "recos": recos,
        "bascules": construire_bascules(recos),
        "verifications": construire_verifications(recos),
    }


def test_statistiques_par_application(scenario: dict) -> None:
    """Une statistique par application de démonstration."""
    stats = scenario["stats"]
    assert set(stats) == set(PROFILS)
    assistant = stats["support-assistant"]
    classement = stats["ticket-classifier"]
    assert assistant.jetons_sortie_mediane > 5 * classement.jetons_sortie_mediane
    assert assistant.latence_p50 > classement.latence_p50
    assert set(stats["doc-summarizer"].heures) <= {0, 1, 2, 3, 4, 5}


def test_leviers_du_scenario(scenario: dict) -> None:
    """Les leviers du scénario sont produits avec une économie positive."""
    leviers = construire_leviers(scenario["stats"], scenario["table"], DATE_FIN_DEFAUT)
    par_application = {(lv.application_id, lv.type_levier.value) for lv in leviers}
    assert ("ticket-classifier", "modele_surdimensionne") in par_application
    assert ("support-assistant", "cache_prompts_absent") in par_application
    assert ("doc-summarizer", "traitement_differable") in par_application
    assert ("doc-summarizer", "sorties_trop_longues") in par_application
    assert all(lv.economie_mensuelle_estimee_usd > 0 for lv in leviers)


def test_trois_campagnes_terminees(scenario: dict) -> None:
    """Trois campagnes terminées, chacune avec sa référence et ses candidats."""
    campagnes = scenario["campagnes"]
    assert len(campagnes) == 3
    for campagne in campagnes:
        references = [r for r in campagne.resultats if r.est_reference]
        candidats = [r for r in campagne.resultats if not r.est_reference]
        assert len(references) == 1
        assert candidats
        assert campagne.cout_estime_usd > 0
        assert campagne.cout_reel_usd <= campagne.cout_estime_usd
        assert campagne.graine_aleatoire == 42


def test_classement_passe_le_seuil(scenario: dict) -> None:
    """Le petit modèle est conforme sur le classement (seuil 95 %)."""
    campagne = next(c for c in scenario["campagnes"] if c.application_id == "ticket-classifier")
    petit = next(r for r in campagne.resultats if r.modele_id != campagne.resultats[0].modele_id)
    assert petit.taux_equivalence >= 0.95
    assert petit.intervalle_confiance_bas > 0.90
    assert petit.echecs_deterministes == 0
    assert petit.conforme_seuils
    assert petit.cout_par_requete_usd < campagne.resultats[0].cout_par_requete_usd


def test_assistant_echoue_au_seuil(scenario: dict) -> None:
    """Les candidats de l'assistant restent sous le seuil de 97 %."""
    campagne = next(c for c in scenario["campagnes"] if c.application_id == "support-assistant")
    for candidat in (r for r in campagne.resultats if not r.est_reference):
        assert not candidat.conforme_seuils
        assert candidat.echecs_deterministes > 0 or candidat.taux_equivalence < 0.97


def test_statuts_des_recommandations(scenario: dict) -> None:
    """En attente pour le classement, refusée pour l'assistant, bascule pour le résumé."""
    statuts = {reco.applicationId: reco.status for reco in scenario["recos"]}
    assert statuts["ticket-classifier"] == StatutRecommandation.EN_ATTENTE_APPROBATION
    assert statuts["support-assistant"] == StatutRecommandation.REFUSEE
    assert statuts["doc-summarizer"] == StatutRecommandation.EN_COURS_DE_BASCULE


def test_chiffres_des_recommandations(scenario: dict) -> None:
    """Les blocs coût et qualité de l'Annexe B sont remplis et cohérents."""
    for reco in scenario["recos"]:
        assert 0.0 <= reco.quality.equivalenceRate <= 1.0
        assert reco.quality.confidenceIntervalLow <= reco.quality.equivalenceRate
        assert reco.cost.costPerRequestBefore > 0
        assert reco.cost.costPerRequestAfter > 0
        assert reco.cost.monthlyRequests > 0
        assert reco.rolloutPlan == [10, 50, 100]
        attendue = (
            reco.cost.costPerRequestBefore - reco.cost.costPerRequestAfter
        ) * reco.cost.monthlyRequests
        assert reco.cost.expectedMonthlySaving == pytest.approx(attendue, rel=0.01)
        assert reco.risks


def test_bascule_et_verification(scenario: dict) -> None:
    """Une seule bascule, sur le résumé, avec une vérification d'économie."""
    bascules = scenario["bascules"]
    assert len(bascules) == 1
    assert bascules[0].application_id == "doc-summarizer"
    assert bascules[0].palier_actuel_pourcentage == 50
    assert bascules[0].qualite_en_ligne_mesuree >= bascules[0].seuil_alerte
    verifications = scenario["verifications"]
    assert len(verifications) == 1
    assert verifications[0].economie_constatee_usd <= verifications[0].economie_attendue_usd
    assert verifications[0].ecart_pourcentage < 10.0


def test_scenario_deterministe() -> None:
    """Deux constructions identiques donnent les mêmes recommandations."""
    socle = charger_socle_demo()
    table = TableTarifs(socle.tarifs)
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=JOURS_TEST)
    stats = analyser_trafic(records, table, PROFILS, JOURS_TEST, DATE_FIN_DEFAUT)
    premier = construire_recommandations(stats, construire_campagnes(stats, table, DATE_FIN_DEFAUT))
    second = construire_recommandations(stats, construire_campagnes(stats, table, DATE_FIN_DEFAUT))
    assert [r.model_dump() for r in premier] == [r.model_dump() for r in second]
