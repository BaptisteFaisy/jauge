"""Cloisonnement par client à travers les adaptateurs (ENF-SEC-02, R11).

Chaque port des agents prend `tenant_id` en premier argument : une donnée d'un
autre client doit être refusée (`CloisonnementError`), une donnée inexistante
signalée (`IntrouvableError`). Ces tests négatifs complètent ceux de l'API.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.integration.conftest import TENANT_A, TENANT_B, Environnement

from jauge.agents.schemas import RedactionRecommandation, composer_recommandation
from jauge.common.errors import CloisonnementError, IntrouvableError
from jauge.integration import creer_services
from jauge.integration.client_demo import ClientModeleDemo
from jauge.integration.lecture_journaux import construire_plan_de_donnees
from jauge.integration.plan_de_donnees import PlanDeDonnees
from jauge.store.memory import MemoryStore

DEBUT = date(2020, 1, 1)
FIN = date(2030, 1, 1)


def test_referentiel_refuse_une_application_dun_autre_client(services) -> None:
    """Le client B ne peut pas lire une application du client A."""
    with pytest.raises(CloisonnementError):
        services.referentiel.application(TENANT_B, "ticket-classifier")
    with pytest.raises(CloisonnementError):
        services.referentiel.tenant("t-inconnu")


def test_referentiel_signale_une_application_inexistante(services) -> None:
    """Une application inconnue lève `IntrouvableError`, pas un 403."""
    with pytest.raises(IntrouvableError):
        services.referentiel.application(TENANT_A, "application-qui-n-existe-pas")


def test_agregats_filtres_sur_le_client(services) -> None:
    """Les lectures de coûts ne rendent que les données du client demandé."""
    agregats = services.agregats.agregats(TENANT_B, None, DEBUT, FIN)
    assert agregats, "le client B doit avoir ses propres agrégats"
    assert all(agregat.tenant_id == TENANT_B for agregat in agregats)
    with pytest.raises(CloisonnementError):
        services.agregats.agregats(TENANT_B, "ticket-classifier", DEBUT, FIN)
    with pytest.raises(CloisonnementError):
        services.agregats.synthese(TENANT_B, "ticket-classifier", DEBUT, FIN)


def test_rapprochement_refuse_un_client_inconnu(services) -> None:
    """Le rapprochement est refusé pour un client qui n'existe pas."""
    with pytest.raises(CloisonnementError):
        services.rapprochement.rapprochements("t-inconnu", DEBUT, FIN)
    jours = services.rapprochement.rapprochements(TENANT_B, DEBUT, FIN)
    assert all(jour.tenant_id == TENANT_B for jour in jours)


def test_leviers_et_profils_cloisonnes(services) -> None:
    """Les leviers et le profil de tâche refusent l'accès inter-clients."""
    with pytest.raises(CloisonnementError):
        services.leviers.leviers(TENANT_B, "ticket-classifier")
    with pytest.raises(CloisonnementError):
        services.profils.profil_tache(TENANT_B, "ticket-classifier")


def test_candidats_et_estimation_cloisonnes(services) -> None:
    """Les candidats et l'estimation refusent l'accès inter-clients."""
    with pytest.raises(CloisonnementError):
        services.candidats.modeles_candidats(TENANT_B, "ticket-classifier")
    with pytest.raises(CloisonnementError):
        services.estimation.estimer(TENANT_B, "ticket-classifier", ["modele-x"], 50)


def test_rejeu_refuse_une_application_dun_autre_client(services) -> None:
    """Le lancement d'une campagne est refusé hors du client."""
    with pytest.raises(CloisonnementError):
        services.rejeu.lancer(TENANT_B, "ticket-classifier", ["modele-x"], 50, 42)


def test_resultats_et_clusters_cloisonnes(services, campagne: str) -> None:
    """Les résultats d'une campagne du client A sont refusés au client B."""
    with pytest.raises(CloisonnementError):
        services.resultats.resultats(TENANT_B, campagne)
    with pytest.raises(CloisonnementError):
        services.resultats.familles_en_echec(TENANT_B, campagne)
    with pytest.raises(IntrouvableError):
        services.resultats.resultats(TENANT_A, "campagne-inconnue")


def test_entrees_de_recommandation_cloisonnees(services, campagne: str) -> None:
    """Les valeurs de recommandation refusent l'accès inter-clients."""
    with pytest.raises(CloisonnementError):
        services.entrees.valeurs(TENANT_B, campagne)


def test_enregistrement_refuse_une_recommandation_dun_autre_client(
    environnement: Environnement, services, campagne: str
) -> None:
    """Une recommandation d'un autre client n'est jamais enregistrée."""
    from jauge.models.recommendation import StatutRecommandation

    entrees = services.entrees.valeurs(TENANT_A, campagne)
    recommandation = composer_recommandation(
        entrees,
        RedactionRecommandation(
            risks=["Risque surveillé."], status=StatutRecommandation.EN_ATTENTE_APPROBATION
        ),
    )
    with pytest.raises(CloisonnementError):
        services.enregistrement.enregistrer(TENANT_B, recommandation)
    assert environnement.store.obtenir_recommandation(TENANT_B, recommandation.id) is None


def test_validation_humaine_cloisonnee(services) -> None:
    """Une demande de validation d'un autre client est refusée."""
    demande_id = services.validation.demander(TENANT_A, "ticket-classifier", "c-x", "modele-x")
    assert services.validation.statut_validation(TENANT_A, demande_id) == "en_attente"
    with pytest.raises(CloisonnementError):
        services.validation.statut_validation(TENANT_B, demande_id)


def test_resultats_ne_transportent_aucun_texte(services, campagne: str) -> None:
    """Aucun texte de requête ne sort des ports (ENF-CONF-01)."""
    for resultat in services.resultats.resultats(TENANT_A, campagne):
        charge = resultat.model_dump()
        assert "texte_requete" not in charge
        assert "reponse_candidate" not in charge
    for cluster in services.resultats.familles_en_echec(TENANT_A, campagne):
        assert isinstance(cluster.famille, str)
        assert len(cluster.famille) < 60


def test_services_isoles_sur_store_vide() -> None:
    """Un store vide ne donne accès à aucune donnée d'un autre client."""
    services = creer_services(MemoryStore(), mode_demo=True, plan=PlanDeDonnees())
    with pytest.raises(CloisonnementError):
        services.referentiel.tenant(TENANT_A)


def test_plan_de_donnees_par_client(environnement: Environnement) -> None:
    """Le plan de données reste cloisonné par client et par application."""
    plan_client_b = construire_plan_de_donnees(environnement.lignes[:10], TENANT_B)
    assert plan_client_b.profil(TENANT_A, "ticket-classifier") is None
    assert ClientModeleDemo(environnement.plan) is not None
