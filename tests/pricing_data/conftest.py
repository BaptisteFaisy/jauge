"""Fixtures des tests de tarifs : catalogue daté à deux versions, store amorcé.

Le scénario est volontairement minimal mais complet : une **baisse** de prix sur
le petit modèle, une **hausse** sur le grand modèle, un moyen modèle **inchangé**.
Ces trois cas suffisent à couvrir la détection, l'impact et les preuves à rejouer.

Elles s'appuient sur les fixtures partagées de `tests/conftest.py` (client,
applications, `tarifs`) et n'ajoutent que ce qui est propre à la piste.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION

from jauge.models.campaign import Campagne, ResultatCandidat, StatutCampagne
from jauge.models.cost import AgregatQuotidien
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
)
from jauge.models.tenant import Application, Tenant
from jauge.pricing_data.catalogue import CatalogueTarifs
from jauge.store.memory import MemoryStore

#: Date d'effet de la table initiale, et date du changement de prix.
DATE_INITIALE = date(2026, 1, 1)
DATE_BAISSE = date(2026, 6, 1)
#: Jour des agrégats observés (entre les deux dates d'effet).
JOUR_OBSERVE = date(2026, 3, 10)

APP_ID = "ticket-classifier"
CAMPAGNE_ID = "c-prix-001"
RECO_REFUSEE = "r-refusee-pour-economie"
RECO_EN_ATTENTE = "r-en-attente"

#: Prix pour 1 000 jetons (entrée, sortie), par modèle et par version.
PRIX_INITIAUX = {GRAND_MODELE: (0.015, 0.075), PETIT_MODELE: (0.0008, 0.004)}
PRIX_APRES = {GRAND_MODELE: (0.018, 0.090), PETIT_MODELE: (0.0006, 0.003)}
PRIX_MOYEN = (0.003, 0.015)


def tarif(modele_id: str, date_effet: date, entree: float, sortie: float) -> TarifModele:
    """Ligne de tarif Standard pour un modèle, à une date d'effet."""
    return TarifModele(
        modele_id=modele_id,
        region=REGION,
        niveau=ServiceTier.STANDARD,
        date_effet=date_effet,
        prix_entree_1k=entree,
        prix_sortie_1k=sortie,
    )


def table_deux_versions() -> list[TarifModele]:
    """Table datée : baisse sur le petit modèle, hausse sur le grand, moyen stable."""
    lignes = [tarif(MOYEN_MODELE, DATE_INITIALE, *PRIX_MOYEN)]
    for modele, (entree, sortie) in PRIX_INITIAUX.items():
        lignes.append(tarif(modele, DATE_INITIALE, entree, sortie))
    for modele, (entree, sortie) in PRIX_APRES.items():
        lignes.append(tarif(modele, DATE_BAISSE, entree, sortie))
    return lignes


@pytest.fixture
def catalogue() -> CatalogueTarifs:
    """Catalogue historisé portant les deux versions de prix."""
    return CatalogueTarifs(lignes_table=table_deux_versions())


def construire_recommandation(
    tenant_id: str,
    identifiant: str,
    modele_cible: str,
    statut: StatutRecommandation,
    conforme_seuils: bool = True,
) -> Recommandation:
    """Recommandation de démonstration, dont la qualité est réglable."""
    taux = 0.97 if conforme_seuils else 0.60
    return Recommandation(
        id=identifiant,
        tenantId=tenant_id,
        applicationId=APP_ID,
        currentModel=GRAND_MODELE,
        targetModel=modele_cible,
        campaignId=CAMPAGNE_ID,
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=taux,
            confidenceIntervalLow=taux - 0.03,
            selfAgreementOfCurrentModel=0.98,
            deterministicFailures=0 if conforme_seuils else 4,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=0.01,
            costPerRequestAfter=0.002,
            monthlyRequests=100_000,
            expectedMonthlySaving=800.0,
        ),
        latency=MetriquesLatence(p50Before=400, p50After=300, p95Before=900, p95After=700),
        status=statut,
    )


def construire_campagne(
    tenant_id: str, modele_candidat: str, conforme_seuils: bool = True
) -> Campagne:
    """Campagne dont le candidat est conforme aux seuils (verdict du socle)."""
    return Campagne(
        id=CAMPAGNE_ID,
        tenant_id=tenant_id,
        application_id=APP_ID,
        modeles_candidats=[modele_candidat],
        statut=StatutCampagne.TERMINEE,
        cout_estime_usd=4.2,
        resultats=[
            ResultatCandidat(
                modele_id=modele_candidat,
                nombre_requetes_rejouees=200,
                taux_equivalence=0.97 if conforme_seuils else 0.60,
                intervalle_confiance_bas=0.94 if conforme_seuils else 0.55,
                intervalle_confiance_haut=0.99,
                cout_par_requete_usd=0.002,
                conforme_seuils=conforme_seuils,
            )
        ],
    )


def agregats_deux_modeles(tenant: Tenant, application: Application) -> list[AgregatQuotidien]:
    """Une journée observée : 1 000 requêtes sur le petit modèle, 500 sur le grand."""
    return [
        AgregatQuotidien(
            tenant_id=tenant.id,
            application_id=APP_ID,
            equipe=application.equipe,
            environnement="production",
            modele_id=PETIT_MODELE,
            jour=JOUR_OBSERVE,
            nombre_requetes=1_000,
            jetons_entree_total=400_000,
            jetons_sortie_total=100_000,
            cout_recalcule_usd=0.72,
        ),
        AgregatQuotidien(
            tenant_id=tenant.id,
            application_id=APP_ID,
            equipe=application.equipe,
            environnement="production",
            modele_id=GRAND_MODELE,
            jour=JOUR_OBSERVE,
            nombre_requetes=500,
            jetons_entree_total=200_000,
            jetons_sortie_total=50_000,
            cout_recalcule_usd=6.75,
        ),
    ]


def recommandations_du_scenario(tenant_id: str) -> list[Recommandation]:
    """Les deux recommandations du scénario de changement de prix.

    - la **refusée** vise le petit modèle (celui dont le prix baisse) avec une
      qualité suffisante : c'est un refus pour motif économique ;
    - celle **en attente** vise le moyen modèle (prix inchangé), mais son modèle
      courant est le grand modèle (prix en hausse).
    """
    return [
        construire_recommandation(
            tenant_id, RECO_REFUSEE, PETIT_MODELE, StatutRecommandation.REFUSEE
        ),
        construire_recommandation(
            tenant_id, RECO_EN_ATTENTE, MOYEN_MODELE, StatutRecommandation.EN_ATTENTE_APPROBATION
        ),
    ]


@pytest.fixture
def store(tenant: Tenant, app_classement: Application) -> MemoryStore:
    """Store amorcé : agrégats sur deux modèles, une campagne, deux recommandations."""
    memoire = MemoryStore()
    memoire.enregistrer_tenant(tenant)
    memoire.enregistrer_application(app_classement)
    memoire.enregistrer_agregats(agregats_deux_modeles(tenant, app_classement))
    memoire.enregistrer_campagne(construire_campagne(tenant.id, PETIT_MODELE))
    for recommandation in recommandations_du_scenario(tenant.id):
        memoire.enregistrer_recommandation(recommandation)
    return memoire


@pytest.fixture
def store_qualite_insuffisante(tenant: Tenant, app_classement: Application) -> MemoryStore:
    """Même store, mais le candidat **n'était pas** conforme aux seuils.

    Le refus de la recommandation n'est donc pas économique : une baisse de prix
    ne doit rien rouvrir (la qualité restait insuffisante).
    """
    memoire = MemoryStore()
    memoire.enregistrer_tenant(tenant)
    memoire.enregistrer_application(app_classement)
    memoire.enregistrer_campagne(
        construire_campagne(tenant.id, PETIT_MODELE, conforme_seuils=False)
    )
    memoire.enregistrer_recommandation(
        construire_recommandation(
            tenant.id,
            RECO_REFUSEE,
            PETIT_MODELE,
            StatutRecommandation.REFUSEE,
            conforme_seuils=False,
        )
    )
    return memoire
