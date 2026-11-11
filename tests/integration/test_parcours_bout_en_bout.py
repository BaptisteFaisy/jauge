"""Parcours de bout en bout : trafic → attribution → leviers → campagne →
évaluation → recommandation → approbation → bascule par paliers → vérification.

C'est la recette du câblage (M17) : chaque étape est produite par son module
réel, jamais par une donnée figée, et **aucun appel réseau** n'est effectué.

Exigences visées : EF-ATT-05/06, EF-LEV-01..06, EF-REJ-01..04, EF-EVA-01..04,
EF-REC-01/02, EF-BAS-01..03, EF-VER-01, ENF-IA-01, ENF-SEC-02.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.integration.conftest import Environnement, periode

from jauge.agents.schemas import (
    RedactionRecommandation,
    composer_recommandation,
    valider_recommandation,
)
from jauge.engine.calculator import calculer_cout_requete
from jauge.integration.leviers import AdaptateurLeviers
from jauge.integration.recommandation import AdaptateurValidation
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.models.recommendation import Recommandation, StatutRecommandation
from jauge.rollout.controller import ControlleurBascule, FakeQualiteEnLigne, MesureEnLigne
from jauge.rollout.gateway import FakePasserelleClient
from jauge.rollout.verify import verifier_economie

TENANT = "t-demo-a"
APPLICATION = "ticket-classifier"
APPROBATEUR = "responsable-applications@client-a.example"


def test_attribution_et_rapprochement(environnement: Environnement) -> None:
    """Les agrégats et le rapprochement viennent du module M1."""
    debut, fin = periode(environnement.records)
    agregats = environnement.store.lister_agregats(TENANT, debut=debut, fin=fin)
    assert agregats, "aucun agrégat produit par l'attribution"
    assert all(agregat.nombre_requetes > 0 for agregat in agregats)
    rapprochements = environnement.store.lister_rapprochements(TENANT)
    assert rapprochements, "aucun rapprochement produit"
    assert all(jour.conforme for jour in rapprochements), "écart facture supérieur à 3 %"


def test_plan_de_donnees_alimente_le_rejeu(environnement: Environnement) -> None:
    """Le plan de données porte les requêtes rejouables et leurs textes."""
    plan = environnement.plan
    requetes = plan.requetes(TENANT, APPLICATION)
    assert len(requetes) > 100
    textes = plan.textes(TENANT, APPLICATION)
    assert all(textes[requete.requete_id] for requete in requetes[:10])


def test_leviers_detectes_par_le_module(environnement: Environnement) -> None:
    """Le détecteur (M3) trouve les leviers et les classe par économie."""
    leviers = AdaptateurLeviers(environnement.store).leviers(TENANT, APPLICATION)
    assert leviers, "aucun levier détecté"
    types = {levier.type_levier.value for levier in leviers}
    assert "modele_surdimensionne" in types
    economies = [levier.economie_mensuelle_estimee_usd for levier in leviers]
    assert economies == sorted(economies, reverse=True), "leviers non classés (EF-LEV-06)"


def test_campagne_terminee_avec_resultats(services, campagne: str) -> None:
    """La campagne est terminée et porte les résultats consolidés (M4 + M5)."""
    assert services.rejeu.statut(TENANT, campagne).value == "terminee"
    resultats = services.resultats.resultats(TENANT, campagne)
    assert len(resultats) >= 2
    reference = next(resultat for resultat in resultats if resultat.est_reference)
    assert reference.taux_equivalence == pytest.approx(1.0)
    candidats = [resultat for resultat in resultats if not resultat.est_reference]
    assert any(resultat.conforme_seuils for resultat in candidats), "aucun candidat conforme"
    assert all(0.0 <= resultat.taux_equivalence <= 1.0 for resultat in candidats)


def test_rejeu_deterministe(services, campagne: str) -> None:
    """Même graine ⇒ même échantillon ⇒ mêmes résultats (EF-REJ-01)."""
    candidats = [c.modele_id for c in services.candidats.modeles_candidats(TENANT, APPLICATION)]
    seconde = services.rejeu.lancer(TENANT, APPLICATION, candidats, 80, 42)
    premiere = {
        r.modele_id: r.taux_equivalence for r in services.resultats.resultats(TENANT, campagne)
    }
    suivante = {
        r.modele_id: r.taux_equivalence for r in services.resultats.resultats(TENANT, seconde)
    }
    assert premiere == suivante


def test_recommandation_composee_et_validee(
    environnement: Environnement, services, campagne: str
) -> None:
    """La recommandation est composée par le schéma de l'Annexe B (M7)."""
    entrees = services.entrees.valeurs(TENANT, campagne)
    assert entrees.modele_cible != entrees.modele_actuel
    assert entrees.cost.expectedMonthlySaving > 0
    assert entrees.plan_bascule == (10, 50, 100)
    recommandation = composer_recommandation(
        entrees,
        RedactionRecommandation(
            risks=["Surveillance de la qualité en ligne à chaque palier de la bascule."],
            status=StatutRecommandation.EN_ATTENTE_APPROBATION,
        ),
    )
    tenant = environnement.store.obtenir_tenant(TENANT)
    valider_recommandation(recommandation, tenant.modeles_autorises)
    assert services.enregistrement.enregistrer(TENANT, recommandation) == recommandation.id
    assert environnement.store.obtenir_recommandation(TENANT, recommandation.id) is not None


def test_validation_humaine_jamais_automatique(services, campagne: str) -> None:
    """L'enregistrement est interrompu jusqu'à l'accord d'un humain (§12.3)."""
    validation: AdaptateurValidation = services.validation  # type: ignore[assignment]
    entrees = services.entrees.valeurs(TENANT, campagne)
    demande_id = validation.demander(TENANT, APPLICATION, campagne, entrees.modele_cible)
    assert validation.statut_validation(TENANT, demande_id) == "en_attente"
    validation.approuver(demande_id, APPROBATEUR)
    assert validation.statut_validation(TENANT, demande_id) == "approuvee"


def test_bascule_par_paliers_et_verification(
    environnement: Environnement, services, campagne: str
) -> None:
    """Approbation → bascule 10/50/100 (passerelle factice) → économie vérifiée."""
    recommandation = _recommandation_approuvee(environnement, services, campagne)
    application = environnement.store.obtenir_application(TENANT, APPLICATION)
    passerelle = FakePasserelleClient()
    mesures = [MesureEnLigne(0.99, 0.95, 0.01, 50, palier) for palier in (10, 50, 100)]
    controleur = ControlleurBascule(
        passerelle, FakeQualiteEnLigne(mesures), store=environnement.store
    )
    bascule = controleur.demarrer(TENANT, application, recommandation, APPROBATEUR)
    assert bascule.palier_actuel_pourcentage == 10
    bascule = controleur.avancer(TENANT, bascule)
    assert bascule.palier_actuel_pourcentage == 50
    bascule = controleur.avancer(TENANT, bascule)
    assert bascule.palier_actuel_pourcentage == 100
    bascule = controleur.avancer(TENANT, bascule)
    assert not bascule.actif
    assert passerelle.operations() == [
        "creer_regle_ponderee",
        "mettre_a_jour_poids",
        "mettre_a_jour_poids",
    ]
    verification = _verifier_economie(environnement, application, recommandation)
    assert verification.economie_constatee_usd > 0
    environnement.store.enregistrer_verification(verification)
    assert environnement.store.lister_verifications(TENANT)


def _recommandation_approuvee(
    environnement: Environnement, services, campagne: str
) -> Recommandation:
    """Recommandation approuvée par un humain (hors agent, ENF-IA-02)."""
    entrees = services.entrees.valeurs(TENANT, campagne)
    recommandation = composer_recommandation(
        entrees,
        RedactionRecommandation(
            risks=["Surveillance de la qualité en ligne à chaque palier."],
            status=StatutRecommandation.EN_ATTENTE_APPROBATION,
        ),
    )
    recommandation = recommandation.model_copy(
        update={"status": StatutRecommandation.APPROUVEE, "approvedBy": APPROBATEUR}
    )
    environnement.store.enregistrer_recommandation(recommandation)
    return recommandation


def _verifier_economie(environnement: Environnement, application, recommandation):
    """Vérifie l'économie à volume constant (M6) sur une période « après »."""
    debut, fin = periode(environnement.records)
    avant = environnement.store.lister_agregats(TENANT, application.id, debut, fin)
    apres = _agregats_apres(avant, environnement.tarifs, recommandation.targetModel, fin)
    return verifier_economie(TENANT, application, recommandation, avant, apres)


def _agregats_apres(agregats, tarifs, modele_cible: str, jour: date):
    """Période « après » : mêmes volumes, tarif du modèle cible (socle)."""
    tarif = tarifs.trouver(modele_cible, "eu-west-1", ServiceTier.STANDARD, jour)
    return [
        agregat.model_copy(
            update={
                "modele_id": modele_cible,
                "cout_recalcule_usd": round(
                    calculer_cout_requete(
                        tarif,
                        MetriquesRequete(
                            jetons_entree=agregat.jetons_entree_total,
                            jetons_sortie=agregat.jetons_sortie_total,
                        ),
                    ).cout_total,
                    4,
                ),
            }
        )
        for agregat in agregats
    ]
