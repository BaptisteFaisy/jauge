"""Recette R6, R7 et R8 — approbation, bascule par paliers, retrait automatique.

- **R6** « Recommandation sans approbation » : la bascule est **impossible**
  (`ApprobationRequiseError`, y compris via l'API du plan de contrôle) —
  ENF-IA-02, EF-REC-02.
- **R7** « Bascule approuvée » : la règle de passerelle est créée, les paliers
  **10 % → 50 % → 100 %** sont franchis, la **qualité en ligne est mesurée** à
  chaque palier — EF-BAS-01, EF-BAS-02.
- **R8** « Qualité en ligne dégradée volontairement » : la règle est **retirée
  automatiquement**, une alerte est émise et l'état `annule_automatiquement` est
  positionné — EF-BAS-03.

Le contrôleur réel (`jauge.rollout.controller.ControlleurBascule`) est piloté
avec la passerelle fictive `FakePasserelleClient` et le juge en ligne fictif
`FakeQualiteEnLigne`. Aucun réseau, aucun AWS.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import GRAND_MODELE, PETIT_MODELE
from tests.recette.conftest import entetes

from jauge.api.auth import RoleUtilisateur
from jauge.common.errors import ApprobationRequiseError
from jauge.models.recommendation import (
    BasculeProgressive,
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
)
from jauge.models.tenant import Application, Tenant
from jauge.rollout.controller import ControlleurBascule, FakeQualiteEnLigne, MesureEnLigne
from jauge.rollout.gateway import FakePasserelleClient
from jauge.store.memory import MemoryStore

CHEF = RoleUtilisateur.RESPONSABLE_APPLICATION
RECO_ID = "r-recette-bascule"
PALIERS = (10, 50, 100)


def _recommandation(
    tenant_id: str,
    application_id: str,
    statut: StatutRecommandation,
) -> Recommandation:
    """Recommandation au format de l'Annexe B, pour la recette de bascule."""
    return Recommandation(
        id=RECO_ID,
        tenantId=tenant_id,
        applicationId=application_id,
        currentModel=GRAND_MODELE,
        targetModel=PETIT_MODELE,
        campaignId="c-recette-bascule",
        sampleSize=200,
        quality=MetriquesQualite(
            equivalenceRate=0.97,
            confidenceIntervalLow=0.93,
            selfAgreementOfCurrentModel=0.99,
            deterministicFailures=0,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=0.0035,
            costPerRequestAfter=0.0002,
            monthlyRequests=30_000,
            expectedMonthlySaving=99.0,
        ),
        latency=MetriquesLatence(p50Before=720, p50After=250, p95Before=1400, p95After=600),
        status=statut,
        approvedBy=None if statut is StatutRecommandation.EN_ATTENTE_APPROBATION else "resp-app",
    )


def _mesure(taux: float = 0.98, intervalle: float = 0.94, erreurs: float = 0.0) -> MesureEnLigne:
    """Mesure de qualité en ligne, produite par le juge en ligne fictif."""
    return MesureEnLigne(
        taux_equivalence=taux,
        intervalle_bas=intervalle,
        taux_erreur=erreurs,
        nombre_requetes=500,
    )


def _demarrer(
    controleur: ControlleurBascule,
    tenant: Tenant,
    application: Application,
    recommandation: Recommandation,
) -> BasculeProgressive:
    """Démarre la bascule d'une recommandation approuvée (§8.6, étape 1)."""
    return controleur.demarrer(
        tenant_id=tenant.id,
        application=application,
        recommandation=recommandation,
        approuve_par="responsable-applicatif",
    )


def _verifier_demarrage(
    bascule: BasculeProgressive,
    passerelle: FakePasserelleClient,
    store_recette: MemoryStore,
    tenant: Tenant,
) -> None:
    """La règle est créée au premier palier et l'état est publié (§8.6)."""
    assert bascule.palier_actuel_pourcentage == 10
    assert bascule.actif is True
    regle = passerelle.lire_regle(bascule.regle_passerelle_id)
    assert regle is not None
    assert regle.pourcentage_cible == 10
    assert regle.modele_cible == PETIT_MODELE
    assert store_recette.obtenir_bascule(tenant.id, bascule.id) is not None
    reco_en_cours = store_recette.obtenir_recommandation(tenant.id, RECO_ID)
    assert reco_en_cours is not None
    assert reco_en_cours.status is StatutRecommandation.EN_COURS_DE_BASCULE


def test_r6_bascule_impossible_sans_approbation(
    tenant: Tenant, app_classement: Application
) -> None:
    """R6 : sans approbation du responsable, la bascule est refusée (ENF-IA-02)."""
    controleur = ControlleurBascule(
        passerelle=FakePasserelleClient(), qualite=FakeQualiteEnLigne([_mesure()])
    )
    recommandation = _recommandation(
        tenant.id, app_classement.id, StatutRecommandation.EN_ATTENTE_APPROBATION
    )

    with pytest.raises(ApprobationRequiseError) as echec:
        controleur.demarrer(
            tenant_id=tenant.id,
            application=app_classement,
            recommandation=recommandation,
            approuve_par=None,
        )
    assert echec.value.details.get("recommandation_id") == RECO_ID


def test_r6_bascule_refusee_si_recommandation_non_approuvee(
    tenant: Tenant, app_classement: Application
) -> None:
    """R6 : une recommandation encore en attente ne peut pas être basculée."""
    controleur = ControlleurBascule(
        passerelle=FakePasserelleClient(), qualite=FakeQualiteEnLigne([_mesure()])
    )
    recommandation = _recommandation(
        tenant.id, app_classement.id, StatutRecommandation.EN_ATTENTE_APPROBATION
    )

    with pytest.raises(ApprobationRequiseError):
        controleur.demarrer(
            tenant_id=tenant.id,
            application=app_classement,
            recommandation=recommandation,
            approuve_par="responsable-applicatif",
        )


def test_r6_api_refuse_la_bascule_sans_approbation(
    client_api: TestClient,
    store_recette: MemoryStore,
    tenant: Tenant,
    app_classement: Application,
) -> None:
    """R6 (API) : `POST /v1/recommendations/{id}/rollout` répond 409 sans approbation."""
    store_recette.enregistrer_tenant(tenant)
    store_recette.enregistrer_application(app_classement)
    store_recette.enregistrer_recommandation(
        _recommandation(tenant.id, app_classement.id, StatutRecommandation.EN_ATTENTE_APPROBATION)
    )

    reponse = client_api.post(
        f"/v1/recommendations/{RECO_ID}/rollout",
        json={},
        headers=entetes(tenant.id, CHEF),
    )
    assert reponse.status_code == 409, reponse.text
    corps = reponse.json()
    assert corps["error"]["code"] == "approval_required"
    assert corps["error"]["details"]["statut"] == "PENDING_APPROVAL"
    # Aucune bascule n'a été créée.
    assert store_recette.lister_bascules(tenant.id) == []


def test_r7_bascule_approuvee_franchit_les_paliers(
    store_recette: MemoryStore,
    tenant: Tenant,
    app_classement: Application,
) -> None:
    """R7 : règle créée, paliers 10 → 50 → 100, qualité en ligne mesurée."""
    store_recette.enregistrer_tenant(tenant)
    store_recette.enregistrer_application(app_classement)
    recommandation = _recommandation(tenant.id, app_classement.id, StatutRecommandation.APPROUVEE)
    store_recette.enregistrer_recommandation(recommandation)

    passerelle = FakePasserelleClient()
    mesures = [_mesure() for _ in PALIERS]
    controleur = ControlleurBascule(
        passerelle=passerelle,
        qualite=FakeQualiteEnLigne(mesures),
        store=store_recette,
    )

    # 1. Démarrage : règle de passerelle créée au premier palier (10 %).
    bascule = _demarrer(controleur, tenant, app_classement, recommandation)
    _verifier_demarrage(bascule, passerelle, store_recette, tenant)

    # 2. Palier 50 %, puis 100 % : la qualité en ligne est mesurée à chaque fois.
    for palier_attendu in (50, 100):
        bascule = controleur.avancer(tenant_id=tenant.id, bascule=bascule)
        assert bascule.palier_actuel_pourcentage == palier_attendu
        assert bascule.qualite_en_ligne_mesuree == pytest.approx(0.98)
        regle = passerelle.lire_regle(bascule.regle_passerelle_id)
        assert regle is not None
        assert regle.pourcentage_cible == palier_attendu

    # 3. Palier final : la bascule est close, la recommandation appliquée.
    finale = controleur.avancer(tenant_id=tenant.id, bascule=bascule)
    assert finale.actif is False
    assert finale.palier_actuel_pourcentage == 100
    reco_finale = store_recette.obtenir_recommandation(tenant.id, RECO_ID)
    assert reco_finale is not None
    assert reco_finale.status is StatutRecommandation.APPLIQUEE

    # 4. Les mesures en ligne sont conservées pour l'audit (EF-ADM-02).
    mesures_enregistrees = controleur.mesures(bascule.id)
    assert len(mesures_enregistrees) == 3
    assert all(mesure.nombre_requetes == 500 for mesure in mesures_enregistrees)
    assert controleur.alertes(bascule.id) == []


def test_r8_qualite_degradee_retire_la_regle_automatiquement(
    store_recette: MemoryStore,
    tenant: Tenant,
    app_classement: Application,
) -> None:
    """R8 : qualité en ligne sous le seuil → règle retirée, alerte, état dédié."""
    store_recette.enregistrer_tenant(tenant)
    store_recette.enregistrer_application(app_classement)
    recommandation = _recommandation(tenant.id, app_classement.id, StatutRecommandation.APPROUVEE)
    store_recette.enregistrer_recommandation(recommandation)

    passerelle = FakePasserelleClient()
    mesures = [_mesure(), _mesure(taux=0.72, intervalle=0.65), _mesure()]
    controleur = ControlleurBascule(
        passerelle=passerelle,
        qualite=FakeQualiteEnLigne(mesures),
        store=store_recette,
    )

    bascule = _demarrer(controleur, tenant, app_classement, recommandation)
    regle_id = bascule.regle_passerelle_id

    # Le palier 10 % passe ; le palier 50 % est mesuré sous le seuil : la règle
    # doit être retirée automatiquement.
    palier_10 = controleur.avancer(tenant_id=tenant.id, bascule=bascule)
    assert palier_10.palier_actuel_pourcentage == 50
    annulee = controleur.avancer(tenant_id=tenant.id, bascule=palier_10)

    assert annulee.actif is False
    assert annulee.annule_automatiquement is True
    assert annulee.motif_annulation is not None
    assert "qualité en ligne sous le seuil" in annulee.motif_annulation
    assert annulee.palier_actuel_pourcentage == 50

    # La règle de passerelle a disparu : tout le trafic revient au modèle actuel.
    assert passerelle.lire_regle(regle_id) is None

    # Une alerte est émise et l'échec est journalisé.
    alertes = controleur.alertes(bascule.id)
    assert alertes
    assert "annulée" in alertes[0]

    # Le store reflète l'état : bascule annulée et recommandation annulée.
    bascule_stockee = store_recette.obtenir_bascule(tenant.id, bascule.id)
    assert isinstance(bascule_stockee, BasculeProgressive)
    assert bascule_stockee.annule_automatiquement is True
    reco = store_recette.obtenir_recommandation(tenant.id, RECO_ID)
    assert reco is not None
    assert reco.status is StatutRecommandation.ANNULEE


def test_r8_api_peut_rollback_une_bascule_en_cours(
    client_api: TestClient,
    store_recette: MemoryStore,
    tenant: Tenant,
    app_classement: Application,
) -> None:
    """R8 (API) : le retour arrière manuel est tracé par l'API du plan de contrôle."""
    store_recette.enregistrer_tenant(tenant)
    store_recette.enregistrer_application(app_classement)
    recommandation = _recommandation(tenant.id, app_classement.id, StatutRecommandation.APPROUVEE)
    store_recette.enregistrer_recommandation(recommandation)

    reponse = client_api.post(
        f"/v1/recommendations/{RECO_ID}/rollout",
        json={"motif": "feu vert"},
        headers=entetes(tenant.id, CHEF),
    )
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    assert corps["mode"] == "passerelle"
    bascule_id = corps["bascule"]["id"]
    assert corps["bascule"]["palier_actuel_pourcentage"] == 10

    reponse = client_api.post(
        f"/v1/rollouts/{bascule_id}/rollback",
        headers=entetes(tenant.id, CHEF),
    )
    assert reponse.status_code == 200, reponse.text
    annulee = reponse.json()
    assert annulee["actif"] is False
    assert annulee["motif_annulation"]
