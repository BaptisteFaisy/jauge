"""Tests des schémas de sortie structurée (§12.3, Annexe B, ENF-IA-01).

Ce qui est vérifié : une sortie non conforme est rejetée avant enregistrement ;
les blocs chiffrés de la recommandation viennent des valeurs calculées, jamais
de l'agent ; un texte qui cite un nombre non calculé est refusé ; un agent ne
peut pas écrire un statut d'approbation.
"""

from __future__ import annotations

import pytest

from jauge.agents.jeu_essai import CAMPAGNE, MODELE_GRAND, MODELE_MOYEN, entrees_demo
from jauge.agents.schemas import (
    PLAN_BASCULE_ATTENDU,
    AnalyseCouts,
    LectureResultats,
    PlanRejeu,
    RedactionRecommandation,
    composer_recommandation,
    nombres_cites,
    valider_recommandation,
    valider_sortie,
    verifier_chiffres_autorises,
)
from jauge.common.errors import ValidationError
from jauge.models.recommendation import StatutRecommandation
from jauge.models.tenant import Application, Tenant

# --- Schémas des spécialistes ---------------------------------------------


def test_analyse_couts_valide() -> None:
    """Une analyse conforme passe : constats courts, montants calculés."""
    analyse = valider_sortie(
        AnalyseCouts,
        {
            "application_id": "ticket-classifier",
            "periode_jours": 30,
            "cout_total_usd": 52.0,
            "constats": ["La dépense augmente sur la seconde moitié de la période."],
            "leviers_prioritaires": ["lv-0001"],
        },
    )
    assert isinstance(analyse, AnalyseCouts)
    assert analyse.leviers_prioritaires == ["lv-0001"]


def test_analyse_couts_refuse_un_constat_vide() -> None:
    """Un constat vide n'apporte rien : le schéma le rejette."""
    with pytest.raises(ValidationError):
        valider_sortie(
            AnalyseCouts,
            {
                "application_id": "ticket-classifier",
                "periode_jours": 30,
                "cout_total_usd": 52.0,
                "constats": ["   "],
            },
        )


def test_analyse_couts_refuse_un_champ_en_trop() -> None:
    """§12.3 : `extra="forbid"` — un agent ne remplit pas les champs du code."""
    with pytest.raises(ValidationError) as capture:
        valider_sortie(
            AnalyseCouts,
            {
                "application_id": "ticket-classifier",
                "periode_jours": 30,
                "cout_total_usd": 52.0,
                "constats": ["Constat."],
                "economie_estimee_usd": 12.0,
            },
        )
    assert "AnalyseCouts" in str(capture.value.details["schema"])


def test_plan_rejeu_valide() -> None:
    """Un plan de rejeu conforme passe, contrôles et justification compris."""
    plan = valider_sortie(
        PlanRejeu,
        {
            "application_id": "ticket-classifier",
            "modeles_candidats": [MODELE_MOYEN],
            "taille_echantillon": 200,
            "controles_deterministes": ["format_json"],
            "justification": "200 requêtes suffisent pour un intervalle de ±3 points.",
        },
    )
    assert plan.taille_echantillon == 200


@pytest.mark.parametrize(
    "charge",
    [
        {"modeles_candidats": [], "taille_echantillon": 200},
        {"modeles_candidats": [MODELE_MOYEN, MODELE_MOYEN], "taille_echantillon": 200},
        {"modeles_candidats": [MODELE_MOYEN], "taille_echantillon": 5},
        {"modeles_candidats": [MODELE_MOYEN], "taille_echantillon": 5_000},
    ],
)
def test_plan_rejeu_refuse_les_plans_invalides(charge: dict) -> None:
    """Candidats vides ou dupliqués, échantillon hors bornes : refus."""
    charge = {
        "application_id": "ticket-classifier",
        "justification": "Justification.",
        **charge,
    }
    with pytest.raises(ValidationError):
        valider_sortie(PlanRejeu, charge)


def test_lecture_resultats_valide() -> None:
    """Le lecteur nomme un candidat et des constats, sans recalculer."""
    lecture = valider_sortie(
        LectureResultats,
        {
            "campagne_id": CAMPAGNE,
            "candidat_retenu": MODELE_MOYEN,
            "constats": ["Le candidat moyen respecte les seuils de l'application."],
            "familles_en_echec": ["tickets en langue rare"],
        },
    )
    assert lecture.candidat_retenu == MODELE_MOYEN


# --- Rédaction de la recommandation ---------------------------------------


def test_redaction_valide() -> None:
    """Le rédacteur ne fournit que des risques et un statut."""
    redaction = valider_sortie(
        RedactionRecommandation,
        {"risks": ["Tickets en langue rare : 4 échecs sur 5"], "status": "PENDING_APPROVAL"},
    )
    assert redaction.status is StatutRecommandation.EN_ATTENTE_APPROBATION


@pytest.mark.parametrize("statut", ["APPROVED", "APPLIED", "ROLLING_OUT", "ROLLED_BACK"])
def test_redaction_refuse_un_statut_d_approbation(statut: str) -> None:
    """ENF-IA-02 : un agent n'approuve ni ne bascule — le statut est refusé."""
    with pytest.raises(ValidationError) as capture:
        valider_sortie(RedactionRecommandation, {"risks": [], "status": statut})
    erreurs = str(capture.value.details["erreurs"]).lower()
    assert "approuv" in erreurs or "humaine" in erreurs


def test_redaction_refuse_un_bloc_chiffre() -> None:
    """§12.3 : l'agent ne peut pas fournir `quality`, `cost` ou `latency`."""
    with pytest.raises(ValidationError):
        valider_sortie(
            RedactionRecommandation,
            {"risks": [], "quality": {"equivalenceRate": 0.99}},
        )


def test_redaction_refuse_un_risque_vide() -> None:
    """Un risque vide n'est pas un risque."""
    with pytest.raises(ValidationError):
        valider_sortie(RedactionRecommandation, {"risks": [" "]})


# --- Chiffres cités dans les textes ---------------------------------------


def test_nombres_cites() -> None:
    """Les nombres sont lus en point ou virgule décimale, espaces de milliers compris."""
    assert nombres_cites("97,5 % et 0.943 puis 200") == [97.5, 0.943, 200.0]
    assert nombres_cites("4 200 dollars") == [4200.0]


def test_chiffres_autorises_en_fraction_et_en_pourcentage(
    client: Tenant, application: Application
) -> None:
    """Un taux calculé peut être cité en fraction comme en pourcentage."""
    autorisees = entrees_demo(client, application).valeurs_autorisees()
    verifier_chiffres_autorises("Taux de 0,975 et borne basse de 94,3 %.", autorisees)
    verifier_chiffres_autorises("Taux de 97,5 % et 98,5 % sur 200 requêtes.", autorisees)


def test_chiffre_invente_refuse(client: Tenant, application: Application) -> None:
    """ENF-IA-01 : un nombre absent des valeurs calculées fait rejeter le texte."""
    autorisees = entrees_demo(client, application).valeurs_autorisees()
    with pytest.raises(ValidationError):
        verifier_chiffres_autorises("Économie de 12 345 dollars par mois.", autorisees)


# --- Composition et validation finale -------------------------------------


def test_composition_reprend_les_valeurs_du_code(client: Tenant, application: Application) -> None:
    """Annexe B : chiffres du code, textes de l'agent, plan de bascule imposé."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(
        RedactionRecommandation, {"risks": ["Tickets en langue rare : 4 échecs sur 5"]}
    )
    recommandation = composer_recommandation(entrees, redaction, recommandation_id="r-0001")
    assert recommandation.id == "r-0001"
    assert recommandation.targetModel == MODELE_MOYEN
    assert recommandation.currentModel == MODELE_GRAND
    assert recommandation.campaignId == CAMPAGNE
    assert recommandation.sampleSize == 200
    assert recommandation.quality.equivalenceRate == entrees.quality.equivalenceRate
    assert recommandation.cost.expectedMonthlySaving == entrees.cost.expectedMonthlySaving
    assert recommandation.latency.p95After == entrees.latency.p95After
    assert recommandation.rolloutPlan == PLAN_BASCULE_ATTENDU
    assert recommandation.risks == ["Tickets en langue rare : 4 échecs sur 5"]
    assert recommandation.status is StatutRecommandation.EN_ATTENTE_APPROBATION
    assert recommandation.approvedBy is None


def test_composition_refuse_un_risque_avec_chiffre_invente(
    client: Tenant, application: Application
) -> None:
    """Un risque qui cite un chiffre non calculé fait échouer la composition."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(
        RedactionRecommandation, {"risks": ["Économie estimée à 4 200 dollars par mois"]}
    )
    with pytest.raises(ValidationError):
        composer_recommandation(entrees, redaction)


def test_recommandation_conforme_acceptee(client: Tenant, application: Application) -> None:
    """Une recommandation conforme à l'Annexe B passe les contrôles finaux."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(RedactionRecommandation, {"risks": ["Tickets rares."]})
    recommandation = composer_recommandation(entrees, redaction, recommandation_id="r-0001")
    validee = valider_recommandation(recommandation, client.modeles_autorises)
    assert validee.id == "r-0001"


def test_recommandation_refusee_si_economie_incoherente(
    client: Tenant, application: Application
) -> None:
    """ENF-IA-01 : une économie qui ne découle pas des coûts est rejetée."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(RedactionRecommandation, {"risks": []})
    recommandation = composer_recommandation(entrees, redaction, recommandation_id="r-0001")
    trafiquee = recommandation.model_copy(
        update={"cost": recommandation.cost.model_copy(update={"expectedMonthlySaving": 10.0})}
    )
    with pytest.raises(ValidationError):
        valider_recommandation(trafiquee, client.modeles_autorises)


def test_recommandation_refusee_si_modele_hors_liste(
    client: Tenant, application: Application
) -> None:
    """§12.3 : le modèle cible doit rester dans la liste autorisée du client."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(RedactionRecommandation, {"risks": []})
    recommandation = composer_recommandation(entrees, redaction, recommandation_id="r-0001")
    with pytest.raises(ValidationError):
        valider_recommandation(recommandation, [MODELE_GRAND])


def test_recommandation_refusee_si_cible_egale_actuelle(
    client: Tenant, application: Application
) -> None:
    """Une recommandation qui ne change pas de modèle n'a pas de sens."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(RedactionRecommandation, {"risks": []})
    recommandation = composer_recommandation(entrees, redaction, recommandation_id="r-0001")
    identique = recommandation.model_copy(update={"targetModel": MODELE_GRAND})
    with pytest.raises(ValidationError):
        valider_recommandation(identique)


def test_recommandation_refusee_si_plan_de_bascule_non_conforme(
    client: Tenant, application: Application
) -> None:
    """Annexe C : les paliers sont 10, 50, 100 — un autre plan est refusé."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(RedactionRecommandation, {"risks": []})
    recommandation = composer_recommandation(entrees, redaction, recommandation_id="r-0001")
    trafiquee = recommandation.model_copy(update={"rolloutPlan": [50, 100]})
    with pytest.raises(ValidationError):
        valider_recommandation(trafiquee)


def test_recommandation_refusee_si_deja_approuvee(client: Tenant, application: Application) -> None:
    """ENF-IA-02 : une recommandation enregistrée ne peut pas être déjà approuvée."""
    entrees = entrees_demo(client, application)
    redaction = valider_sortie(RedactionRecommandation, {"risks": []})
    recommandation = composer_recommandation(entrees, redaction, recommandation_id="r-0001")
    approuvee = recommandation.model_copy(update={"status": StatutRecommandation.APPROUVEE})
    with pytest.raises(ValidationError):
        valider_recommandation(approuvee)
