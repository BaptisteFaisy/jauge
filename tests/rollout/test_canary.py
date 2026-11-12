"""Tests du canary et de la séquence imposée : shadow → canary → paliers.

Piste n° 7 du top 10. Scénarios couverts : le canary à 1 % crée bien une règle
pondérée ; un déclencheur firing retire la règle automatiquement et motive
l'annulation ; impossible de passer au canary sans shadow concluant ; impossible
de passer aux paliers sans approbation ; le rollback automatique est journalisé.

Les adaptateurs réels (AgentCore, Bedrock) ne sont jamais appelés : seuls les
doubles hors ligne du dépôt sont utilisés (`FakePasserelleClient`,
`FakeQualiteEnLigne`, `FakeModeleClient`, `FakeJugeClient`).
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION

from jauge.common.errors import ApprobationRequiseError, CloisonnementError
from jauge.engine.calculator import TableTarifs
from jauge.evaluation.judge import FakeJugeClient
from jauge.models.campaign import Campagne, ResultatCandidat
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
)
from jauge.models.tenant import Application, Tenant
from jauge.replay.client import FakeModeleClient
from jauge.rollout.canary import (
    ETAPE_CANARY,
    ETAPE_SHADOW,
    EtapePrealableManquante,
    SequenceBascule,
    controleur_de_sequence,
)
from jauge.rollout.controller import FakeQualiteEnLigne, MesureEnLigne
from jauge.rollout.devis import BornesEchantillon
from jauge.rollout.gateway import FakePasserelleClient
from jauge.rollout.journal import JournalEtapes, StatutEtape
from jauge.rollout.ports import DepotPairesMemoire
from jauge.rollout.shadow import MoteurShadow, ObservationReelle, ParametresShadow
from jauge.store.memory import MemoryStore

# Identifiant de la règle de passerelle créée pour l'application de démonstration.
PASSERELLE = "gw-ticket-classifier"
# Date d'effet de la table des tarifs partagée (`tests/conftest.py`).
DATE_EFFET = date(2026, 1, 1)


@pytest.fixture
def recommandation(tenant: Tenant, app_classement: Application) -> Recommandation:
    """Recommandation approuvée vers un modèle qui a un tarif."""
    return Recommandation(
        id="r-shadow-001",
        tenantId=tenant.id,
        applicationId=app_classement.id,
        currentModel=GRAND_MODELE,
        targetModel=PETIT_MODELE,
        campaignId="c-shadow-001",
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=0.97,
            confidenceIntervalLow=0.94,
            selfAgreementOfCurrentModel=0.98,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=0.01,
            costPerRequestAfter=0.002,
            monthlyRequests=100_000,
            expectedMonthlySaving=800.0,
        ),
        latency=MetriquesLatence(p50Before=400, p50After=300, p95Before=900, p95After=700),
        approvedBy="responsable-applicatif",
        status=StatutRecommandation.APPROUVEE,
    )


def observations(nombre: int = 20) -> list[ObservationReelle]:
    """Trafic réel observé sur l'application de classement."""
    return [
        ObservationReelle(
            tenant_id="t-demo-a",
            application_id="ticket-classifier",
            requete_id=f"req-{index:03d}",
            texte_requete=f"classer le ticket {index}",
            reponse_reelle=f"categorie-{index}",
            jetons_entree=120,
            jetons_sortie=40,
            latence_ms=300,
        )
        for index in range(nombre)
    ]


def parametres(tenant: Tenant) -> ParametresShadow:
    """Paramètres du shadow : bornes, plafond et seuils explicites."""
    return ParametresShadow(
        modele_actuel=GRAND_MODELE,
        modele_candidat=PETIT_MODELE,
        bornes=BornesEchantillon(taille_max=10, taux=0.5),
        plafond_usd=tenant.plafond_campagne_usd,
        seuil_qualite=0.95,
        seuil_ecart_labo_production=0.05,
        jetons_entree_juge=1200,
        jetons_sortie_juge=400,
    )


def monter(
    tenant: Tenant,
    application: Application,
    reco: Recommandation,
    tarifs,
):
    """Séquence hors ligne : shadow, canary et paliers, doubles uniquement."""
    store = MemoryStore()
    store.enregistrer_tenant(tenant)
    store.enregistrer_application(application)
    store.enregistrer_recommandation(reco)
    moteur = MoteurShadow(
        client=FakeModeleClient(jetons_sortie=40, jetons_entree_fixe=120, latence_ms=200),
        juge=FakeJugeClient(modele_id=MOYEN_MODELE),
        tarifs=TableTarifs(tarifs),
        depot=DepotPairesMemoire(),
        region=REGION,
        a_la_date=DATE_EFFET,
    )
    passerelle = FakePasserelleClient()
    journal = JournalEtapes()
    controleur = controleur_de_sequence(passerelle, FakeQualiteEnLigne([]), store=store)
    sequence = SequenceBascule(moteur=moteur, controleur=controleur, journal=journal)
    return (sequence, passerelle, store, journal)


def bonne_mesure() -> MesureEnLigne:
    """Mesure nominale : qualité au-dessus des seuils, erreur stable."""
    return MesureEnLigne(taux_equivalence=0.98, intervalle_bas=0.95, taux_erreur=0.001)


def lancer_shadow(sequence, tenant, application, reco):
    """Lance le shadow (approuvé) et rend son bilan."""
    return sequence.lancer_shadow(
        tenant_id=tenant.id,
        application=application,
        recommandation=reco,
        observations=observations(),
        parametres=parametres(tenant),
        approuve_par="responsable-applicatif",
    )


def lancer_canary(sequence, tenant, application, reco, taux_erreur_reference: float = 0.0):
    """Lance le canary (approuvé) et rend son état."""
    return sequence.lancer_canary(
        tenant_id=tenant.id,
        application=application,
        recommandation=reco,
        approuve_par="responsable-applicatif",
        taux_erreur_reference=taux_erreur_reference,
    )


# --- Canary : 1 % du trafic réel -------------------------------------------


def test_canary_1_pourcent_cree_une_regle_ponderee(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Le canary sert 1 % du trafic réel, par une règle pondérée de passerelle."""
    sequence, passerelle, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    canary = lancer_canary(sequence, tenant, app_classement, recommandation)
    regles = passerelle.lister_regles(PASSERELLE)
    assert len(regles) == 1
    assert regles[0].pourcentage_cible == 1
    assert regles[0].ponderee
    assert canary.palier_pourcentage == 1
    assert canary.regle_id == regles[0].id


def test_declencheur_firing_retire_la_regle_et_motive(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Un déclencheur qui fires retire la règle automatiquement et motive l'annulation."""
    sequence, passerelle, _, journal = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    lancer_canary(sequence, tenant, app_classement, recommandation)
    degradee = MesureEnLigne(taux_equivalence=0.80, intervalle_bas=0.75, taux_erreur=0.001)
    annulee = sequence.surveiller_canary(degradee)
    assert passerelle.lister_regles(PASSERELLE) == []
    assert annulee.annule_automatiquement is True
    assert "qualité en ligne sous le seuil" in (annulee.motif_annulation or "")
    annulations = [e for e in journal.entrees(ETAPE_CANARY) if e.decision == "annule"]
    assert len(annulations) == 1
    assert annulations[0].motif == annulee.motif_annulation


def test_taux_d_erreur_en_hausse_declenche_le_retour_arriere(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """La hausse du taux d'erreur au-delà de la marge est un déclencheur (§13.4)."""
    sequence, passerelle, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    lancer_canary(sequence, tenant, app_classement, recommandation, taux_erreur_reference=0.01)
    en_erreur = MesureEnLigne(taux_equivalence=0.99, intervalle_bas=0.95, taux_erreur=0.05)
    annulee = sequence.surveiller_canary(en_erreur)
    assert passerelle.lister_regles(PASSERELLE) == []
    assert "taux d'erreur en hausse" in (annulee.motif_annulation or "")


# --- Séquence imposée : shadow → canary → paliers --------------------------


def test_impossible_de_passer_au_canary_sans_shadow_concluant(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Sans shadow concluant, le canary n'existe pas (§8.6)."""
    sequence, passerelle, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    with pytest.raises(EtapePrealableManquante):
        lancer_canary(sequence, tenant, app_classement, recommandation)
    assert passerelle.regles == {}


def test_shadow_divergent_bloque_le_canary(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Une divergence labo/production bloque le canary : c'est un résultat, pas une note."""
    sequence, passerelle, store, _ = monter(tenant, app_classement, recommandation, tarifs)
    store.enregistrer_campagne(
        Campagne(
            id="c-shadow-001",
            tenant_id=tenant.id,
            application_id=app_classement.id,
            modeles_candidats=[PETIT_MODELE],
            cout_estime_usd=0.5,
            resultats=[
                ResultatCandidat(
                    modele_id=PETIT_MODELE,
                    nombre_requetes_rejouees=200,
                    taux_equivalence=0.50,
                    intervalle_confiance_bas=0.43,
                    intervalle_confiance_haut=0.57,
                    cout_par_requete_usd=0.002,
                )
            ],
        )
    )
    bilan = lancer_shadow(sequence, tenant, app_classement, recommandation)
    assert bilan.divergence is True
    with pytest.raises(EtapePrealableManquante):
        lancer_canary(sequence, tenant, app_classement, recommandation)
    assert passerelle.regles == {}


def test_impossible_de_passer_aux_paliers_sans_approbation(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Chaque palier exige une décision humaine (ENF-IA-02)."""
    sequence, _, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    lancer_canary(sequence, tenant, app_classement, recommandation)
    sequence.surveiller_canary(bonne_mesure())
    with pytest.raises(ApprobationRequiseError):
        sequence.avancer_paliers(approuve_par=None)
    with pytest.raises(ApprobationRequiseError):
        sequence.avancer_paliers(approuve_par="")


def test_approbation_absente_bloque_le_shadow(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Le shadow lui-même est une étape soumise à approbation humaine."""
    sequence, _, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    with pytest.raises(ApprobationRequiseError):
        sequence.lancer_shadow(
            tenant_id=tenant.id,
            application=app_classement,
            recommandation=recommandation,
            observations=observations(),
            parametres=parametres(tenant),
            approuve_par=None,
        )


def test_paliers_10_50_100_apres_le_canary(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Après le canary validé, les paliers existants montent 10 → 50 → 100."""
    sequence, passerelle, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    lancer_canary(sequence, tenant, app_classement, recommandation)
    sequence.surveiller_canary(bonne_mesure())
    percentages = []
    for _ in range(3):
        poussee = sequence.avancer_paliers(
            approuve_par="responsable-applicatif", mesure=bonne_mesure()
        )
        percentages.append(poussee.palier_actuel_pourcentage)
    assert percentages == [10, 50, 100]
    regles = passerelle.lister_regles(PASSERELLE)
    assert len(regles) == 1
    assert regles[0].pourcentage_cible == 100


def test_paliers_exigent_un_canary_concluant(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Un canary annulé ne cède pas la place aux paliers."""
    sequence, _, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    lancer_canary(sequence, tenant, app_classement, recommandation)
    sequence.surveiller_canary(
        MesureEnLigne(taux_equivalence=0.80, intervalle_bas=0.75, taux_erreur=0.001)
    )
    with pytest.raises(EtapePrealableManquante):
        sequence.avancer_paliers(approuve_par="responsable-applicatif")


def test_rollback_automatique_journalise(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Le retour arrière automatique laisse une trace : qui, quand, quel motif."""
    sequence, _, _, journal = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    lancer_canary(sequence, tenant, app_classement, recommandation)
    sequence.surveiller_canary(
        MesureEnLigne(taux_equivalence=0.80, intervalle_bas=0.75, taux_erreur=0.001)
    )
    entrees = journal.entrees(ETAPE_CANARY)
    assert [e.decision for e in entrees] == ["lance", "annule"]
    annulation = entrees[-1]
    assert annulation.quand
    assert annulation.qui == "systeme"
    assert annulation.motif
    assert dict(annulation.seuils)["seuil_qualite"] == 0.95


def test_etats_de_la_sequence(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """La séquence connaît l'état de chaque étape."""
    sequence, _, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    assert sequence.etats()[ETAPE_SHADOW] is StatutEtape.NON_LANCEE
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    assert sequence.etats()[ETAPE_SHADOW] is StatutEtape.CONCLUANTE
    lancer_canary(sequence, tenant, app_classement, recommandation)
    assert sequence.etats()[ETAPE_CANARY] is StatutEtape.LANCEE
    sequence.surveiller_canary(bonne_mesure())
    assert sequence.etats()[ETAPE_CANARY] is StatutEtape.CONCLUANTE


def test_cloisonnement_refuse(
    tenant: Tenant, tenant_b: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Un autre client ne peut pas lancer la bascule de cette application."""
    sequence, _, _, _ = monter(tenant, app_classement, recommandation, tarifs)
    with pytest.raises(CloisonnementError):
        sequence.lancer_shadow(
            tenant_id=tenant_b.id,
            application=app_classement,
            recommandation=recommandation,
            observations=observations(),
            parametres=parametres(tenant),
            approuve_par="responsable-applicatif",
        )


def test_aucun_texte_dans_le_journal(
    tenant: Tenant, app_classement: Application, recommandation, tarifs
) -> None:
    """Le journal exploitable par l'audit ne contient aucun texte (ENF-CONF-01)."""
    sequence, _, _, journal = monter(tenant, app_classement, recommandation, tarifs)
    lancer_shadow(sequence, tenant, app_classement, recommandation)
    lancer_canary(sequence, tenant, app_classement, recommandation)
    rendu = json.dumps([e.vers_dict() for e in journal.entrees()], ensure_ascii=False)
    assert "classer le ticket" not in rendu
    assert "categorie-" not in rendu
    assert "reponse-" not in rendu
