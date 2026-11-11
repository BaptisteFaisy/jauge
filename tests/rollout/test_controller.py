"""Tests de la bascule par paliers, du rollback et de l'approbation (EF-BAS-02/03).

Scénarios de recette couverts : R6 (bascule sans approbation impossible),
R7 (règle créée, paliers franchis, qualité mesurée), R8 (qualité dégradée →
règle retirée automatiquement).
"""

from __future__ import annotations

import pytest

from jauge.common.errors import (
    ApprobationRequiseError,
    CloisonnementError,
    EtatIncompatibleError,
    ValidationError,
)
from jauge.models.recommendation import Recommandation, StatutRecommandation
from jauge.models.tenant import Application, Tenant
from jauge.rollout.controller import (
    ControlleurBascule,
    FakeQualiteEnLigne,
    MesureEnLigne,
    motif_rollback,
    prochain_palier,
    verifier_paliers,
)
from jauge.rollout.gateway import FakePasserelleClient
from jauge.store.memory import MemoryStore


def bonne_mesure() -> MesureEnLigne:
    """Palier nominal : qualité au-dessus des seuils, erreur stable."""
    return MesureEnLigne(taux_equivalence=0.98, intervalle_bas=0.95, taux_erreur=0.005)


def preparer(
    tenant: Tenant,
    app: Application,
    reco: Recommandation,
    mesures: list[MesureEnLigne],
) -> tuple[ControlleurBascule, FakePasserelleClient, FakeQualiteEnLigne, MemoryStore]:
    """Contrôleur prêt à l'emploi avec store peuplé (client, application, reco)."""
    passerelle = FakePasserelleClient()
    qualite = FakeQualiteEnLigne(mesures)
    store = MemoryStore()
    store.enregistrer_tenant(tenant)
    store.enregistrer_application(app)
    store.enregistrer_recommandation(reco)
    controlleur = ControlleurBascule(passerelle=passerelle, qualite=qualite, store=store)
    return controlleur, passerelle, qualite, store


# --- Approbation humaine (EF-REC-02, ENF-IA-02, R6) ------------------------


def test_refus_sans_approbation(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Aucune bascule sans nom du responsable : le contrôleur ne décide jamais."""
    controlleur, passerelle, _, _ = preparer(tenant, app_classement, recommandation, [])

    with pytest.raises(ApprobationRequiseError):
        controlleur.demarrer(tenant.id, app_classement, recommandation, approuve_par=None)

    assert passerelle.regles == {}
    assert passerelle.journal == []


def test_refus_recommandation_non_approuvee(
    tenant: Tenant,
    app_classement: Application,
    recommandation_en_attente: Recommandation,
) -> None:
    """Un nom d'approbateur ne suffit pas : la recommandation doit être APPROVED."""
    controlleur, passerelle, _, _ = preparer(tenant, app_classement, recommandation_en_attente, [])

    with pytest.raises(ApprobationRequiseError):
        controlleur.demarrer(
            tenant.id, app_classement, recommandation_en_attente, approuve_par="responsable"
        )

    assert passerelle.regles == {}


def test_refus_application_hors_passerelle(
    tenant: Tenant, app_hors_passerelle: Application, recommandation: Recommandation
) -> None:
    """Une application hors passerelle se traite par ticket, pas par règle."""
    controlleur, _, _, _ = preparer(tenant, app_hors_passerelle, recommandation, [])

    with pytest.raises(EtatIncompatibleError):
        controlleur.demarrer(
            tenant.id, app_hors_passerelle, recommandation, approuve_par="responsable"
        )


def test_cloisonnement_refuse(
    tenant: Tenant,
    tenant_b: Tenant,
    app_classement: Application,
    recommandation: Recommandation,
) -> None:
    """Client B sur l'application du client A → refus (ENF-SEC-02, R11)."""
    controlleur, _, _, _ = preparer(tenant, app_classement, recommandation, [])

    with pytest.raises(CloisonnementError):
        controlleur.demarrer(tenant_b.id, app_classement, recommandation, approuve_par="b")


# --- Progression des paliers (EF-BAS-02, R7) ------------------------------


def test_progression_10_50_100(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Trois paliers, un rejugement à chacun, puis 100 % appliqué."""
    mesures = [bonne_mesure(), bonne_mesure(), bonne_mesure()]
    controlleur, passerelle, qualite, store = preparer(
        tenant, app_classement, recommandation, mesures
    )

    bascule = controlleur.demarrer(
        tenant.id, app_classement, recommandation, approuve_par="responsable-support"
    )
    assert bascule.palier_actuel_pourcentage == 10
    assert bascule.regle_passerelle_id in passerelle.regles
    assert passerelle.regles[bascule.regle_passerelle_id].pourcentage_cible == 10

    bascule = controlleur.avancer(tenant.id, bascule)
    assert bascule.palier_actuel_pourcentage == 50
    assert passerelle.regles[bascule.regle_passerelle_id].pourcentage_cible == 50

    bascule = controlleur.avancer(tenant.id, bascule)
    assert bascule.palier_actuel_pourcentage == 100
    assert passerelle.regles[bascule.regle_passerelle_id].pourcentage_cible == 100

    bascule = controlleur.avancer(tenant.id, bascule)
    assert bascule.actif is False
    assert bascule.annule_automatiquement is False
    assert bascule.qualite_en_ligne_mesuree == pytest.approx(0.98)

    # Un rejugement par palier, dans l'ordre (EF-BAS-02).
    assert [palier for _, _, palier in qualite.appels] == [10, 50, 100]
    assert passerelle.operations() == [
        "creer_regle_ponderee",
        "mettre_a_jour_poids",
        "mettre_a_jour_poids",
    ]

    # L'état publié suit : bascule persistée, recommandation appliquée.
    assert store.obtenir_bascule(tenant.id, bascule.id) == bascule
    reco = store.obtenir_recommandation(tenant.id, recommandation.id)
    assert reco is not None and reco.status is StatutRecommandation.APPLIQUEE


def test_statut_recommandation_passe_en_bascule(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """La bascule démarre en statut ROLLING_OUT côté recommandation."""
    controlleur, _, _, store = preparer(tenant, app_classement, recommandation, [bonne_mesure()])

    controlleur.demarrer(tenant.id, app_classement, recommandation, approuve_par="responsable")

    reco = store.obtenir_recommandation(tenant.id, recommandation.id)
    assert reco is not None and reco.status is StatutRecommandation.EN_COURS_DE_BASCULE


# --- Rollback automatique (EF-BAS-03, R8) ---------------------------------


def test_rollback_sur_qualite_degradee(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Qualité sous le seuil : règle retirée, motif renseigné, alerte émise."""
    degradee = MesureEnLigne(taux_equivalence=0.80, intervalle_bas=0.74, taux_erreur=0.01)
    controlleur, passerelle, _, store = preparer(
        tenant, app_classement, recommandation, [bonne_mesure(), degradee]
    )

    bascule = controlleur.demarrer(
        tenant.id, app_classement, recommandation, approuve_par="responsable"
    )
    bascule = controlleur.avancer(tenant.id, bascule)
    assert bascule.palier_actuel_pourcentage == 50

    bascule = controlleur.avancer(tenant.id, bascule)

    assert bascule.actif is False
    assert bascule.annule_automatiquement is True
    assert bascule.motif_annulation is not None
    assert "qualité" in bascule.motif_annulation
    assert passerelle.regles == {}  # la règle est retirée : retour au modèle actuel
    assert controlleur.alertes(bascule.id), "l'échec doit être signalé"
    reco = store.obtenir_recommandation(tenant.id, recommandation.id)
    assert reco is not None and reco.status is StatutRecommandation.ANNULEE


def test_rollback_sur_hausse_du_taux_erreur(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Qualité correcte mais erreurs en hausse : la règle est retirée aussi."""
    bruyante = MesureEnLigne(taux_equivalence=0.99, intervalle_bas=0.96, taux_erreur=0.12)
    controlleur, passerelle, _, _ = preparer(tenant, app_classement, recommandation, [bruyante])

    bascule = controlleur.demarrer(
        tenant.id,
        app_classement,
        recommandation,
        approuve_par="responsable",
        taux_erreur_reference=0.01,
    )
    bascule = controlleur.avancer(tenant.id, bascule)

    assert bascule.actif is False
    assert bascule.annule_automatiquement is True
    assert bascule.motif_annulation is not None
    assert "taux d'erreur" in bascule.motif_annulation
    assert passerelle.regles == {}


def test_rollback_puis_plus_de_progression(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Une bascule annulée ne peut pas repartir : transition interdite."""
    degradee = MesureEnLigne(taux_equivalence=0.50, intervalle_bas=0.40, taux_erreur=0.0)
    controlleur, _, _, _ = preparer(tenant, app_classement, recommandation, [degradee])

    bascule = controlleur.demarrer(
        tenant.id, app_classement, recommandation, approuve_par="responsable"
    )
    bascule = controlleur.avancer(tenant.id, bascule)

    with pytest.raises(EtatIncompatibleError):
        controlleur.avancer(tenant.id, bascule)


def test_annulation_manuelle(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Le retour arrière demandé par un humain ne se marque pas « automatique »."""
    controlleur, passerelle, _, _ = preparer(tenant, app_classement, recommandation, [])

    bascule = controlleur.demarrer(
        tenant.id, app_classement, recommandation, approuve_par="responsable"
    )
    bascule = controlleur.annuler(tenant.id, bascule, "décision du responsable", automatique=False)

    assert bascule.actif is False
    assert bascule.annule_automatiquement is False
    assert passerelle.regles == {}


def test_mesures_conservees_pour_l_audit(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """L'historique des rejugements reste disponible (EF-ADM-02)."""
    controlleur, _, _, _ = preparer(
        tenant, app_classement, recommandation, [bonne_mesure(), bonne_mesure()]
    )

    bascule = controlleur.demarrer(
        tenant.id, app_classement, recommandation, approuve_par="responsable"
    )
    bascule = controlleur.avancer(tenant.id, bascule)
    bascule = controlleur.avancer(tenant.id, bascule)

    mesures = controlleur.mesures(bascule.id)
    assert [m.palier for m in mesures] == [10, 50]


# --- Règles pures ----------------------------------------------------------


@pytest.mark.parametrize(
    ("paliers", "valide"),
    [((10, 50, 100), True), ((10, 100), True), ((100,), True), ((10, 50), False)],
)
def test_verifier_paliers(paliers: tuple[int, ...], valide: bool) -> None:
    """Le dernier palier doit valoir 100 %, sinon le trafic reste partagé."""
    if valide:
        assert verifier_paliers(paliers) == paliers
    else:
        with pytest.raises(ValidationError, match="100"):
            verifier_paliers(paliers)


def test_paliers_non_croissants_refuses() -> None:
    """Un palier qui recule serait un retour arrière déguisé."""
    with pytest.raises(ValidationError, match="croissants"):
        verifier_paliers((50, 10, 100))


def test_prochain_palier() -> None:
    """Le palier suivant est le premier supérieur, `None` au dernier."""
    assert prochain_palier((10, 50, 100), 10) == 50
    assert prochain_palier((10, 50, 100), 100) is None


def test_motif_rollback_par_intervalle() -> None:
    """La borne basse de l'intervalle sous son seuil suffit à annuler (§13.4)."""
    mesure = MesureEnLigne(taux_equivalence=0.96, intervalle_bas=0.85, taux_erreur=0.0)
    motif = motif_rollback(mesure, 0.95, 0.90, 0.0, 0.02)
    assert motif is not None and "intervalle" in motif


def test_mesure_incoherente_refusee() -> None:
    """Un taux hors [0, 1] est une donnée invalide, jamais un palier validé."""
    with pytest.raises(ValidationError, match="incohérente"):
        MesureEnLigne(taux_equivalence=1.4, intervalle_bas=0.9, taux_erreur=0.0)
