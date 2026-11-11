"""Fixtures du module M9 — un client, deux applications, un mois complet.

Le jeu de données est volontairement petit et exact : les tests comparent les
chiffres du rapport à des valeurs calculées à la main (55 USD sur 350 requêtes
en octobre 2026, 40 USD en septembre). Aucun appel réseau, aucun service AWS.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, PETIT_MODELE

from jauge.engine.attribution import rapprocher_facture
from jauge.models.campaign import Campagne, StatutCampagne
from jauge.models.cost import AgregatQuotidien, RapprochementJour
from jauge.models.lever import Levier, StatutLevier, TypeLevier
from jauge.models.recommendation import (
    BasculeProgressive,
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
    VerificationEconomie,
)
from jauge.models.tenant import Application, Tenant
from jauge.store.memory import MemoryStore

MOIS_RAPPORT = (2026, 10)
COUT_OCTOBRE = 55.0
COUT_SEPTEMBRE = 40.0
ECONOMIE_CONSTATEE = 9.5
ECONOMIE_ATTENDUE = 12.0


def _agregat(
    tenant_id: str,
    app_id: str,
    equipe: str,
    modele_id: str,
    jour: date,
    requetes: int,
    cout: float,
) -> AgregatQuotidien:
    """Agrégat quotidien minimal mais réaliste (jetons cohérents avec le coût)."""
    return AgregatQuotidien(
        tenant_id=tenant_id,
        application_id=app_id,
        equipe=equipe,
        environnement="production",
        modele_id=modele_id,
        jour=jour,
        nombre_requetes=requetes,
        jetons_entree_total=requetes * 400,
        jetons_sortie_total=requetes * 120,
        cout_recalcule_usd=cout,
        latence_p50_ms=600,
        latence_p95_ms=1800,
    )


def _campagne(tenant_id: str, app_id: str, taille: int) -> Campagne:
    """Campagne terminée, pour dater la taille d'échantillon de l'appendice."""
    return Campagne(
        id=f"c-{app_id}",
        tenant_id=tenant_id,
        application_id=app_id,
        modeles_candidats=[PETIT_MODELE],
        taille_echantillon=taille,
        modele_juge_id="judge-modele",
        statut=StatutCampagne.TERMINEE,
        cout_estime_usd=2.0,
    )


def _recommandation(
    reco_id: str,
    tenant_id: str,
    app_id: str,
    attendu: float,
    statut: StatutRecommandation,
) -> Recommandation:
    """Recommandation normalisée (Annexe B), chiffres déjà fixés."""
    return Recommandation(
        id=reco_id,
        tenantId=tenant_id,
        applicationId=app_id,
        currentModel=GRAND_MODELE,
        targetModel=PETIT_MODELE,
        campaignId=f"c-{app_id}",
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=0.97,
            confidenceIntervalLow=0.93,
            selfAgreementOfCurrentModel=0.98,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=0.01,
            costPerRequestAfter=0.002,
            monthlyRequests=1500,
            expectedMonthlySaving=attendu,
        ),
        latency=MetriquesLatence(p50Before=900, p50After=700, p95Before=2400, p95After=1900),
        status=statut,
    )


@pytest.fixture
def store_peuple(
    tenant: Tenant, app_classement: Application, app_assistant: Application
) -> MemoryStore:
    """Store d'octobre 2026 : agrégats, facture, leviers, recos, bascule, vérif."""
    store = MemoryStore()
    store.enregistrer_tenant(tenant)
    store.enregistrer_application(app_classement)
    store.enregistrer_application(app_assistant)
    _peupler_couts(store, tenant.id)
    _peupler_decisions(store, tenant.id)
    return store


def _peupler_couts(store: MemoryStore, tenant_id: str) -> None:
    """Agrégats d'octobre et de septembre, et rapprochement de la facture."""
    store.enregistrer_agregats(
        [
            _agregat(
                tenant_id,
                "ticket-classifier",
                "support",
                GRAND_MODELE,
                date(2026, 10, 1),
                100,
                30.0,
            ),
            _agregat(
                tenant_id, "support-assistant", "support", GRAND_MODELE, date(2026, 10, 2), 50, 20.0
            ),
            _agregat(
                tenant_id, "ticket-classifier", "support", PETIT_MODELE, date(2026, 10, 3), 200, 5.0
            ),
            _agregat(
                tenant_id,
                "ticket-classifier",
                "support",
                GRAND_MODELE,
                date(2026, 9, 12),
                120,
                COUT_SEPTEMBRE,
            ),
        ]
    )
    store.enregistrer_rapprochement(_rapprochement(tenant_id, date(2026, 10, 1), 35.0, 35.6))
    store.enregistrer_rapprochement(_rapprochement(tenant_id, date(2026, 10, 2), 20.0, 20.4))


def _peupler_decisions(store: MemoryStore, tenant_id: str) -> None:
    """Leviers, recommandations, bascule en cours, vérification et campagnes."""
    for levier in _leviers(tenant_id):
        store.enregistrer_levier(levier)
    store.enregistrer_recommandation(
        _recommandation(
            "r-approuvee",
            tenant_id,
            "ticket-classifier",
            ECONOMIE_ATTENDUE,
            StatutRecommandation.APPROUVEE,
        )
    )
    store.enregistrer_recommandation(
        _recommandation(
            "r-en-attente",
            tenant_id,
            "support-assistant",
            8.0,
            StatutRecommandation.EN_ATTENTE_APPROBATION,
        )
    )
    store.enregistrer_bascule(
        BasculeProgressive(
            id="ro-1",
            tenant_id=tenant_id,
            recommandation_id="r-approuvee",
            application_id="ticket-classifier",
            modele_cible=PETIT_MODELE,
            regle_passerelle_id="rule-1",
            palier_actuel_pourcentage=50,
            qualite_en_ligne_mesuree=0.96,
            seuil_alerte=0.90,
        )
    )
    store.enregistrer_verification(
        VerificationEconomie(
            application_id="ticket-classifier",
            recommandation_id="r-approuvee",
            cout_par_requete_avant=0.01,
            cout_par_requete_apres=0.002,
            volume_periode=1500,
            economie_constatee_usd=ECONOMIE_CONSTATEE,
            economie_attendue_usd=ECONOMIE_ATTENDUE,
            ecart_pourcentage=20.83,
        )
    )
    store.enregistrer_campagne(_campagne(tenant_id, "ticket-classifier", 200))
    store.enregistrer_campagne(_campagne(tenant_id, "support-assistant", 150))


def _rapprochement(
    tenant_id: str, jour: date, recalcule: float, facture: float
) -> RapprochementJour:
    """Ligne de rapprochement quotidienne, formée par le moteur du socle."""
    return rapprocher_facture(tenant_id, jour, recalcule, facture, causes=["unlabelled calls"])


def _leviers(tenant_id: str) -> list[Levier]:
    """Trois leviers : un prouvé, un appliqué, un rejeté (à exclure du rapport)."""
    return [
        Levier(
            id="lv-prouve",
            tenant_id=tenant_id,
            application_id="ticket-classifier",
            type_levier=TypeLevier.MODELE_SURDIMENSIONNE,
            statut=StatutLevier.PROUVE,
            action_proposee="Replay on a smaller model",
            preuve_exigee="Equivalence rate above the application threshold",
            economie_mensuelle_estimee_usd=12.0,
            confiance=0.85,
        ),
        Levier(
            id="lv-applique",
            tenant_id=tenant_id,
            application_id="support-assistant",
            type_levier=TypeLevier.CACHE_PROMPTS_ABSENT,
            statut=StatutLevier.APPLIQUE,
            action_proposee="Enable prompt caching",
            preuve_exigee="Cache hit rate measured in logs",
            economie_mensuelle_estimee_usd=3.0,
            confiance=0.9,
        ),
        Levier(
            id="lv-rejete",
            tenant_id=tenant_id,
            application_id="support-assistant",
            type_levier=TypeLevier.DISTILLATION,
            statut=StatutLevier.REJETE,
            action_proposee="Distil a model",
            preuve_exigee="Out of scope for this version",
            economie_mensuelle_estimee_usd=99.0,
            confiance=0.5,
        ),
    ]


@pytest.fixture
def tarifs_table(tarifs) -> list:
    """Alias lisible de la table de tarifs du socle (fixture `tarifs`)."""
    return tarifs
