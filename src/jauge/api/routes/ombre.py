"""Observation fantôme (shadow) puis canari : la séquence de bascule par l'API.

Le candidat répond **en parallèle** du trafic réel, sans que sa réponse ne soit
jamais renvoyée : la route rend un `BilanShadow` (agrégats, taux, coûts), puis
le canari (1 %) ne s'ouvre qu'après un shadow concluant et une approbation
humaine (ENF-IA-02).

Garde-fous : kill-switch (traitement coupé → 409), cloisonnement (le client et
l'application viennent du store, jamais du corps — ENF-SEC-02), confidentialité
(les textes ne sortent ni de la réponse ni de l'audit — ENF-CONF-01).

L'état (phase, bilan, canari) est **volatil** : il vit dans un magasin rangé
sur `app.state`, comme le kill-switch ; le contrat `Store` n'est pas modifié.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from jauge.api.audit import AuditSink, journaliser
from jauge.api.auth import RoleUtilisateur, Utilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.killswitch import DepKillSwitch, garde_traitement
from jauge.api.routes.applications import application_controlee
from jauge.api.routes.backtest import DepTarifs
from jauge.api.tenancy import exiger_role
from jauge.common.errors import ApprobationRequiseError, IntrouvableError
from jauge.common.ids import aleatoire
from jauge.engine.calculator import TableTarifs
from jauge.evaluation.judge import FakeJugeClient
from jauge.models.recommendation import (
    MetriquesCout,
    MetriquesLatence,
    MetriquesQualite,
    Recommandation,
    StatutRecommandation,
)
from jauge.models.tenant import Application, Tenant
from jauge.pricing_data.loader import lignes
from jauge.replay.client import FakeModeleClient
from jauge.rollout.canary import (
    ETAPE_CANARY,
    ETAPE_SHADOW,
    Canary,
    EtapePrealableManquante,
    SequenceBascule,
    controleur_de_sequence,
)
from jauge.rollout.controller import FakeQualiteEnLigne
from jauge.rollout.devis import BornesEchantillon
from jauge.rollout.gateway import FakePasserelleClient
from jauge.rollout.journal import JournalEtapes, StatutEtape
from jauge.rollout.ports import DepotPairesMemoire
from jauge.rollout.shadow import BilanShadow, MoteurShadow, ObservationReelle, ParametresShadow
from jauge.store.protocol import Store

router = APIRouter(tags=["ombre"])

#: Rôles habilités : opérations qui engagent le trafic du client.
ROLES_OMBRE = (
    RoleUtilisateur.ANALYSTE_FINOPS,
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
)

#: Valeurs par défaut du corps d'observation.
TAILLE_MAX_DEFAUT = 20
SEUIL_ECART_DEFAUT = 0.05
DATE_EFFET_DEFAUT = date(2026, 1, 1)

#: Actions d'audit de la séquence ombre → canari (EF-ADM-02).
ACTION_OMBRE = "ombre_lancee"
ACTION_CANARI = "canari_lance"
ACTION_OMBRE_REFUSEE = "ombre_refusee"


class ObservationEntree(BaseModel):
    """Requête du trafic réel et réponse servie : textes du plan de données."""

    requete_id: str = Field(min_length=1)
    texte_requete: str
    reponse_reelle: str
    jetons_entree: int = Field(default=0, ge=0)
    jetons_sortie: int = Field(default=0, ge=0)
    latence_ms: int = Field(default=0, ge=0)
    type_requete: str = "general"


class DemandeOmbre(BaseModel):
    """Corps de `POST /v1/applications/{id}/ombre`.

    Le client et l'application sont déduits du contexte d'appel (ENF-SEC-02).
    """

    modele_candidat: str = Field(min_length=1)
    taille_max: int = Field(default=TAILLE_MAX_DEFAUT, ge=1)
    taux: float | None = Field(default=None, gt=0.0, le=1.0)
    plafond_usd: float | None = Field(default=None, ge=0.0)
    seuil_qualite: float | None = Field(default=None, ge=0.0, le=1.0)
    seuil_ecart_labo_production: float = Field(default=SEUIL_ECART_DEFAUT, ge=0.0, le=1.0)
    approuve_par: str | None = None
    observations: list[ObservationEntree] = Field(default_factory=list)


class DemandeCanari(BaseModel):
    """Corps de `POST /v1/applications/{id}/canari`."""

    approuve_par: str | None = None
    taux_erreur_reference: float = Field(default=0.0, ge=0.0, le=1.0)


class PhaseOmbre(StrEnum):
    """Phase de la séquence observée pour une application."""

    AUCUNE = "aucune"
    OMBRE = "ombre"
    CANARI = "canari"


class EtatOmbre(BaseModel):
    """État courant de l'observation d'une application (aucun texte)."""

    application_id: str
    tenant_id: str
    phase: PhaseOmbre = PhaseOmbre.AUCUNE
    etapes: dict[str, str] = Field(default_factory=dict)
    bilan: BilanShadow | None = None
    canari: Canary | None = None


@dataclass
class SessionOmbre:
    """Session d'observation d'une application : séquence, cible et états."""

    tenant_id: str
    application_id: str
    modele_candidat: str
    sequence: SequenceBascule
    recommandation: Recommandation
    bilan: BilanShadow | None = None
    canari: Canary | None = None


class MagasinOmbre:
    """Sessions d'observation en mémoire : l'état de ce point d'API est volatil."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionOmbre] = {}

    def obtenir(self, application_id: str) -> SessionOmbre | None:
        """Session d'une application, si elle existe."""
        return self._sessions.get(application_id)

    def enregistrer(self, session: SessionOmbre) -> None:
        """Mémorise la session courante d'une application."""
        self._sessions[session.application_id] = session


def obtenir_magasin_ombre(request: Request) -> MagasinOmbre:
    """Rend le magasin d'ombres, rangé dans `app.state.ombre` à la demande."""
    magasin = getattr(request.app.state, "ombre", None)
    if magasin is None:
        magasin = MagasinOmbre()
        request.app.state.ombre = magasin
    return magasin


#: Alias de dépendance FastAPI du magasin d'ombres.
DepMagasinOmbre = Annotated[MagasinOmbre, Depends(obtenir_magasin_ombre)]


def _date_des_tarifs(tarifs: TableTarifs) -> date:
    """Date d'effet la plus récente de la table : c'est la date du devis."""
    return max((ligne.date_effet for ligne in lignes(tarifs)), default=DATE_EFFET_DEFAUT)


def _sequence(
    store: Store, application: Application, tenant: Tenant, tarifs: TableTarifs
) -> SequenceBascule:
    """Séquence hors ligne (shadow → canary → paliers) sur les doubles du dépôt.

    Le contrôleur ne persiste rien : seule la référence « laboratoire »
    (campagne de rejeu) est lue dans le store, pour comparer au trafic réel.
    """
    moteur = MoteurShadow(
        client=FakeModeleClient(jetons_sortie=40, jetons_entree_fixe=120, latence_ms=200),
        juge=FakeJugeClient(modele_id=application.modele_actuel),
        tarifs=tarifs,
        depot=DepotPairesMemoire(),
        region=tenant.region_principale,
        a_la_date=_date_des_tarifs(tarifs),
    )
    # La qualité en ligne n'est pas mesurée ici : les paliers restent au contrôleur.
    controleur = controleur_de_sequence(FakePasserelleClient(), FakeQualiteEnLigne([]))
    sequence = SequenceBascule(moteur=moteur, controleur=controleur, journal=JournalEtapes())
    # La séquence lit la campagne de rejeu (référence « laboratoire ») dans le store ;
    # le contrôleur, lui, ne persiste rien : l'état de ce point d'API est volatil.
    sequence.store = store
    return sequence


def _recommandation_cible(
    store: Store, application: Application, modele_candidat: str, approuve_par: str | None
) -> Recommandation:
    """Recommandation visant le candidat (preuve de laboratoire), ou support technique."""
    recommandations = store.lister_recommandations(application.tenant_id, app_id=application.id)
    cibles = [reco for reco in recommandations if reco.targetModel == modele_candidat]
    approuvees = [reco for reco in cibles if reco.status is StatutRecommandation.APPROUVEE]
    if approuvees:
        return approuvees[0]
    if cibles:
        return cibles[0]
    return _recommandation_technique(application, modele_candidat, approuve_par)


def _recommandation_technique(
    application: Application, modele_candidat: str, approuve_par: str | None
) -> Recommandation:
    """Support technique de la séquence : métriques neutres, jamais exposé."""
    return Recommandation(
        id=aleatoire("recommendation"),
        tenantId=application.tenant_id,
        applicationId=application.id,
        currentModel=application.modele_actuel,
        targetModel=modele_candidat,
        campaignId="",
        sampleSize=0,
        quality=MetriquesQualite(
            equivalenceRate=0.0,
            confidenceIntervalLow=0.0,
            selfAgreementOfCurrentModel=0.0,
        ),
        cost=MetriquesCout(
            costPerRequestBefore=0.0,
            costPerRequestAfter=0.0,
            monthlyRequests=0,
            expectedMonthlySaving=0.0,
        ),
        latency=MetriquesLatence(p50Before=0, p50After=0, p95Before=0, p95After=0),
        status=StatutRecommandation.APPROUVEE,
        approvedBy=approuve_par,
    )


def _observations(demande: DemandeOmbre, application: Application) -> list[ObservationReelle]:
    """Construit les observations, rattachées à l'application du store (ENF-SEC-02)."""
    return [
        ObservationReelle(
            tenant_id=application.tenant_id,
            application_id=application.id,
            requete_id=entree.requete_id,
            texte_requete=entree.texte_requete,
            reponse_reelle=entree.reponse_reelle,
            jetons_entree=entree.jetons_entree,
            jetons_sortie=entree.jetons_sortie,
            latence_ms=entree.latence_ms,
            type_requete=entree.type_requete,
        )
        for entree in demande.observations
    ]


def _parametres(
    demande: DemandeOmbre, application: Application, tenant: Tenant
) -> ParametresShadow:
    """Paramètres figés du shadow : bornes, plafond et seuils."""
    return ParametresShadow(
        modele_actuel=application.modele_actuel,
        modele_candidat=demande.modele_candidat,
        bornes=BornesEchantillon(taille_max=demande.taille_max, taux=demande.taux),
        plafond_usd=(
            demande.plafond_usd if demande.plafond_usd is not None else tenant.plafond_campagne_usd
        ),
        seuil_qualite=(
            demande.seuil_qualite
            if demande.seuil_qualite is not None
            else application.seuil_qualite
        ),
        seuil_ecart_labo_production=demande.seuil_ecart_labo_production,
    )


def _exiger_approbation(
    demande: DemandeOmbre, utilisateur: Utilisateur, audit: AuditSink, application: Application
) -> None:
    """Refuse un shadow sans décision humaine nommée (ENF-IA-02)."""
    if demande.approuve_par:
        return
    journaliser(
        audit,
        utilisateur,
        ACTION_OMBRE_REFUSEE,
        "application",
        application.id,
        {"raison": "approbation_absente"},
        tenant_id=application.tenant_id,
        succes=False,
        statut_http=409,
    )
    raise ApprobationRequiseError(
        "Aucun shadow sans approbation humaine (ENF-IA-02).",
        application_id=application.id,
    )


def _journaliser_ombre(
    audit: AuditSink, utilisateur: Utilisateur, bilan: BilanShadow, approuve_par: str | None
) -> None:
    """Journalise le shadow : des identifiants et des chiffres, aucun texte."""
    journaliser(
        audit,
        utilisateur,
        ACTION_OMBRE,
        "application",
        bilan.application_id,
        {
            "run_id": bilan.run_id,
            "modele_candidat": bilan.modele_candidat,
            "volume_observe": bilan.volume_observe,
            "nombre_paires": bilan.nombre_paires,
            "taux_equivalence": bilan.taux_equivalence,
            "cout_estime_usd": bilan.cout_estime_usd,
            "cout_shadow_usd": bilan.cout_shadow_usd,
            "plafond_usd": bilan.plafond_usd,
            "concluant": bilan.concluant,
            "approuve_par": approuve_par or "",
        },
        tenant_id=bilan.tenant_id,
        statut_http=201,
    )


def _journaliser_canari(
    audit: AuditSink, utilisateur: Utilisateur, canari: Canary, approuve_par: str | None
) -> None:
    """Journalise l'ouverture du canari : règle, palier et seuils (aucun texte)."""
    journaliser(
        audit,
        utilisateur,
        ACTION_CANARI,
        "application",
        canari.application_id,
        {
            "regle_id": canari.regle_id,
            "palier_pourcentage": canari.palier_pourcentage,
            "seuil_qualite": canari.declencheurs.seuil_qualite,
            "seuil_intervalle_bas": canari.declencheurs.seuil_intervalle_bas,
            "taux_erreur_reference": canari.declencheurs.taux_erreur_reference,
            "approuve_par": approuve_par or "",
        },
        tenant_id=canari.tenant_id,
        statut_http=201,
    )


def _phase(session: SessionOmbre) -> PhaseOmbre:
    """Phase de la session : canari ouvert, shadow lancé, ou rien encore."""
    if session.canari is not None:
        return PhaseOmbre.CANARI
    if session.bilan is not None:
        return PhaseOmbre.OMBRE
    return PhaseOmbre.AUCUNE


@router.post(
    "/v1/applications/{app_id}/ombre",
    response_model=BilanShadow,
    status_code=status.HTTP_201_CREATED,
)
def lancer_ombre(
    app_id: str,
    demande: DemandeOmbre,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
    kill_switch: DepKillSwitch,
    magasin: DepMagasinOmbre,
    tarifs: DepTarifs,
) -> BilanShadow:
    """Démarre l'observation fantôme ; la réponse du candidat n'est jamais renvoyée.

    Un devis au-delà du plafond est refusé **avant** tout appel de modèle
    (ENF-COUT-01).
    """
    exiger_role(utilisateur, *ROLES_OMBRE)
    application = application_controlee(app_id, utilisateur, store, audit, "ombre")
    garde_traitement(kill_switch, audit, utilisateur)(application.tenant_id, "ombre")
    _exiger_approbation(demande, utilisateur, audit, application)
    tenant = store.obtenir_tenant(application.tenant_id)
    if tenant is None:
        raise IntrouvableError("Client introuvable", tenant_id=application.tenant_id)
    sequence = _sequence(store, application, tenant, tarifs)
    recommandation = _recommandation_cible(
        store, application, demande.modele_candidat, demande.approuve_par
    )
    bilan = sequence.lancer_shadow(
        tenant_id=application.tenant_id,
        application=application,
        recommandation=recommandation,
        observations=_observations(demande, application),
        parametres=_parametres(demande, application, tenant),
        approuve_par=demande.approuve_par,
    )
    magasin.enregistrer(
        SessionOmbre(
            tenant_id=application.tenant_id,
            application_id=application.id,
            modele_candidat=demande.modele_candidat,
            sequence=sequence,
            recommandation=recommandation,
            bilan=bilan,
        )
    )
    _journaliser_ombre(audit, utilisateur, bilan, demande.approuve_par)
    return bilan


@router.get("/v1/applications/{app_id}/ombre", response_model=EtatOmbre)
def lire_ombre(
    app_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
    magasin: DepMagasinOmbre,
) -> EtatOmbre:
    """Rend l'état courant de l'observation : phase, bilan et canari."""
    application = application_controlee(app_id, utilisateur, store, audit, "ombre")
    session = magasin.obtenir(application.id)
    if session is None:
        return EtatOmbre(application_id=application.id, tenant_id=application.tenant_id)
    return EtatOmbre(
        application_id=application.id,
        tenant_id=application.tenant_id,
        phase=_phase(session),
        etapes={nom: statut.value for nom, statut in session.sequence.etats().items()},
        bilan=session.bilan,
        canari=session.canari,
    )


@router.post(
    "/v1/applications/{app_id}/canari",
    response_model=Canary,
    status_code=status.HTTP_201_CREATED,
)
def lancer_canari(
    app_id: str,
    demande: DemandeCanari,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
    kill_switch: DepKillSwitch,
    magasin: DepMagasinOmbre,
) -> Canary:
    """Ouvre le canari (1 %) : après un shadow concluant et une approbation.

    Sans shadow concluant, la demande est refusée (409 `preceding_step_missing`).
    """
    exiger_role(utilisateur, *ROLES_OMBRE)
    application = application_controlee(app_id, utilisateur, store, audit, "canari")
    garde_traitement(kill_switch, audit, utilisateur)(application.tenant_id, "canari")
    session = magasin.obtenir(application.id)
    if session is None:
        raise EtapePrealableManquante(
            "Le canari exige un shadow concluant : lancez l'observation d'abord.",
            etape=ETAPE_CANARY,
            etape_precedente=ETAPE_SHADOW,
            statut=StatutEtape.NON_LANCEE.value,
        )
    canari = session.sequence.lancer_canary(
        tenant_id=application.tenant_id,
        application=application,
        recommandation=session.recommandation,
        approuve_par=demande.approuve_par,
        taux_erreur_reference=demande.taux_erreur_reference,
    )
    session.canari = canari
    _journaliser_canari(audit, utilisateur, canari, demande.approuve_par)
    return canari
