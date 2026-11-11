"""Tests des ports et du jeu d'essai (§12, ENF-SEC-02, ENF-COUT-01).

Ce qui est vérifié ici : les protocoles sont bien satisfaits par le jeu d'essai,
le cloisonnement refuse un client tiers, et les valeurs échangées sont calculées
par le code (estimation au niveau Flex, économie mensuelle du socle).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jauge.agents import ports
from jauge.agents.fakes import ServicesFictifs
from jauge.agents.jeu_essai import CAMPAGNE, MODELE_GRAND, MODELE_MOYEN
from jauge.common.errors import CloisonnementError, IntrouvableError
from jauge.engine.stats import economie_mensuelle
from jauge.models.tenant import Application, Tenant

PROTOCOLES = (
    ports.PortReferentiel,
    ports.PortAgregats,
    ports.PortLeviers,
    ports.PortRapprochement,
    ports.PortProfilTache,
    ports.PortModelesCandidats,
    ports.PortEstimationCout,
    ports.PortRejeu,
    ports.PortResultats,
    ports.PortEntreesRecommandation,
    ports.PortEnregistrement,
    ports.PortValidationHumaine,
)


@pytest.mark.parametrize("protocole", PROTOCOLES)
def test_jeu_d_essai_respecte_les_protocoles(jeu: ServicesFictifs, protocole: type) -> None:
    """Chaque port du §12.1 est implémenté par le jeu d'essai."""
    assert isinstance(jeu, protocole)


def test_services_assembles(jeu: ServicesFictifs, services: ports.Services) -> None:
    """`Services` regroupe bien les douze ports, tous servis par le même jeu d'essai."""
    assert services.referentiel is jeu
    assert services.entrees is jeu
    assert services.validation is jeu


def test_client_inconnu_refuse(jeu: ServicesFictifs) -> None:
    """Un client non déclaré ne lit rien : refus explicite, pas de dict vide."""
    with pytest.raises(CloisonnementError):
        jeu.synthese("t-inconnu", None, date.today() - timedelta(days=1), date.today())


def test_application_d_un_autre_client_refusee(
    jeu: ServicesFictifs, tenant_b: Tenant, application: Application
) -> None:
    """ENF-SEC-02 : le client B ne lit pas le profil de l'application du client A."""
    jeu.ajouter_tenant(tenant_b)
    with pytest.raises(CloisonnementError):
        jeu.profil_tache(tenant_b.id, application.id)
    with pytest.raises(CloisonnementError):
        jeu.modeles_candidats(tenant_b.id, application.id)


def test_campagne_d_un_autre_client_refusee(jeu: ServicesFictifs, tenant_b: Tenant) -> None:
    """ENF-SEC-02 : résultats, familles en échec et valeurs de reco sont cloisonnés."""
    jeu.ajouter_tenant(tenant_b)
    with pytest.raises(CloisonnementError):
        jeu.resultats(tenant_b.id, CAMPAGNE)
    with pytest.raises(CloisonnementError):
        jeu.familles_en_echec(tenant_b.id, CAMPAGNE)
    with pytest.raises(CloisonnementError):
        jeu.valeurs(tenant_b.id, CAMPAGNE)


def test_application_inconnue(jeu: ServicesFictifs, client: Tenant) -> None:
    """Une application inexistante lève `IntrouvableError`, pas `KeyError`."""
    with pytest.raises(IntrouvableError):
        jeu.application(client.id, "app-fantome")


def test_estimation_au_niveau_flex(
    jeu: ServicesFictifs, client: Tenant, application: Application
) -> None:
    """Le coût estimé vient du socle : 200 requêtes × (référence + candidat) en Flex."""
    estimation = jeu.estimer(client.id, application.id, [MODELE_MOYEN], 200)
    reference = (1.2 * 0.0075 + 0.18 * 0.0375) * 200
    candidat = (1.2 * 0.0015 + 0.18 * 0.0075) * 200
    assert estimation.montant_usd == pytest.approx(reference + candidat, rel=1e-6)
    assert estimation.par_modele_usd[MODELE_GRAND] == pytest.approx(reference, rel=1e-6)
    assert estimation.dans_le_plafond is True
    assert estimation.plafond_usd == client.plafond_campagne_usd


def test_estimation_depasse_le_plafond(
    jeu: ServicesFictifs, client: Tenant, application: Application
) -> None:
    """Le plafond du client est porté par le client, pas par l'appelant."""
    jeu.tenants[client.id] = client.model_copy(update={"plafond_campagne_usd": 0.5})
    estimation = jeu.estimer(client.id, application.id, [MODELE_MOYEN], 200)
    assert estimation.dans_le_plafond is False


def test_entrees_de_recommandation_calculees(jeu: ServicesFictifs, client: Tenant) -> None:
    """Les valeurs de l'Annexe B sont calculées par le code, jamais par un modèle."""
    entrees = jeu.valeurs(client.id, CAMPAGNE)
    assert entrees.cost.expectedMonthlySaving == economie_mensuelle(
        entrees.cost.costPerRequestBefore,
        entrees.cost.costPerRequestAfter,
        entrees.cost.monthlyRequests,
    )
    assert entrees.cost.costPerRequestBefore in entrees.valeurs_autorisees()


def test_resultats_et_familles(jeu: ServicesFictifs, client: Tenant) -> None:
    """Les résultats consolidés et les familles en échec sont disponibles par campagne."""
    resultats = jeu.resultats(client.id, CAMPAGNE)
    assert [resultat.modele_id for resultat in resultats] == [
        MODELE_GRAND,
        MODELE_MOYEN,
        "modele-petit-demo",
    ]
    clusters = jeu.familles_en_echec(client.id, CAMPAGNE)
    assert clusters[0].famille == "tickets en langue rare"
    assert clusters[0].taux_echec == pytest.approx(0.8)


def test_journal_des_appels(jeu: ServicesFictifs, client: Tenant) -> None:
    """Le jeu d'essai garde la trace des services sollicités (utile aux tests)."""
    jeu.estimer(client.id, "ticket-classifier", [MODELE_MOYEN], 100)
    assert jeu.appels[-1][0] == "estimer"
    assert jeu.appels[-1][1]["taille"] == 100
