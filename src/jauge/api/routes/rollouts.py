"""Bascule progressive et retour arrière (§15, EF-BAS-01..04, ENF-IA-02).

Scénario R6 : une recommandation non approuvée ne peut pas être basculée.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from jauge.api.audit import AuditSink, journaliser
from jauge.api.auth import RoleUtilisateur, Utilisateur
from jauge.api.deps import DepAudit, DepReglages, DepStore, DepUtilisateur
from jauge.api.routes.recommendations import recommandation_controlee
from jauge.api.tenancy import exiger_role, localiser
from jauge.common.errors import ApprobationRequiseError, EtatIncompatibleError
from jauge.common.ids import aleatoire
from jauge.models.recommendation import (
    BasculeProgressive,
    Recommandation,
    StatutRecommandation,
)
from jauge.store.protocol import Store

router = APIRouter(tags=["bascules"])

ROLES_BASCULE = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.RESPONSABLE_APPLICATION,
)


class DemandeBascule(BaseModel):
    """Corps de `POST /v1/recommendations/{id}/rollout`."""

    paliers: list[int] | None = Field(default=None, description="Paliers en pourcentage")
    motif: str | None = None


class ResultatBascule(BaseModel):
    """Réponse d'une demande de bascule : par la passerelle ou par ticket (§8.6)."""

    mode: Literal["passerelle", "ticket"]
    bascule: BasculeProgressive | None = None
    message: str = ""


def _marquer_recommandation(
    store: Store, reco: Recommandation, statut: StatutRecommandation
) -> None:
    """Met à jour le statut d'une recommandation dans le store."""
    store.enregistrer_recommandation(reco.model_copy(update={"status": statut}))


def _refuser_sans_approbation(
    reco: Recommandation, utilisateur: Utilisateur, audit: AuditSink
) -> None:
    """Journalise puis refuse une bascule sans approbation (scénario R6)."""
    journaliser(
        audit,
        utilisateur,
        "bascule_refusee",
        "recommandation",
        reco.id,
        {"statut": reco.status.value},
        tenant_id=reco.tenantId,
        succes=False,
        statut_http=409,
    )
    raise ApprobationRequiseError(
        "Aucune bascule possible sans approbation humaine de la recommandation",
        recommandation_id=reco.id,
        statut=reco.status.value,
    )


def _ouvrir_ticket(
    reco: Recommandation, utilisateur: Utilisateur, store: Store, audit: AuditSink
) -> ResultatBascule:
    """Application hors passerelle : ticket au lieu d'une règle (EF-BAS-04)."""
    _marquer_recommandation(store, reco, StatutRecommandation.EN_COURS_DE_BASCULE)
    journaliser(
        audit,
        utilisateur,
        "bascule_ticket",
        "recommandation",
        reco.id,
        {"application_id": reco.applicationId},
        tenant_id=reco.tenantId,
        statut_http=201,
    )
    return ResultatBascule(
        mode="ticket",
        message=(
            "Application hors passerelle : un ticket de changement de modèle est ouvert "
            "au lieu d'une règle de bascule (EF-BAS-04)."
        ),
    )


def _creer_bascule(
    reco: Recommandation,
    paliers: list[int],
    utilisateur: Utilisateur,
    store: Store,
    audit: AuditSink,
) -> ResultatBascule:
    """Crée la règle de passerelle au premier palier et la journalise."""
    bascule = BasculeProgressive(
        id=aleatoire("rollout"),
        tenant_id=reco.tenantId,
        recommandation_id=reco.id,
        application_id=reco.applicationId,
        modele_cible=reco.targetModel,
        regle_passerelle_id=f"gw-{aleatoire('rollout')}",
        palier_actuel_pourcentage=paliers[0],
        seuil_alerte=reco.quality.confidenceIntervalLow,
    )
    store.enregistrer_bascule(bascule)
    _marquer_recommandation(store, reco, StatutRecommandation.EN_COURS_DE_BASCULE)
    journaliser(
        audit,
        utilisateur,
        "bascule_demarree",
        "bascule",
        bascule.id,
        {
            "recommandation_id": reco.id,
            "paliers": paliers,
            "regle_passerelle_id": bascule.regle_passerelle_id,
        },
        tenant_id=reco.tenantId,
        statut_http=201,
    )
    return ResultatBascule(
        mode="passerelle", bascule=bascule, message="Bascule progressive démarrée."
    )


@router.post(
    "/v1/recommendations/{reco_id}/rollout",
    response_model=ResultatBascule,
    status_code=status.HTTP_201_CREATED,
)
def demarrer_bascule(
    reco_id: str,
    demande: DemandeBascule,
    utilisateur: DepUtilisateur,
    store: DepStore,
    reglages: DepReglages,
    audit: DepAudit,
) -> ResultatBascule:
    """Démarre la bascule d'une recommandation approuvée (ENF-IA-02)."""
    exiger_role(utilisateur, *ROLES_BASCULE)
    reco = recommandation_controlee(reco_id, utilisateur, store, audit, "bascule")
    if reco.status != StatutRecommandation.APPROUVEE:
        _refuser_sans_approbation(reco, utilisateur, audit)
    application = store.obtenir_application(reco.tenantId, reco.applicationId)
    if application is not None and not application.passe_par_passerelle:
        return _ouvrir_ticket(reco, utilisateur, store, audit)
    paliers = demande.paliers or reglages.obtenir().paliers_bascule
    return _creer_bascule(reco, paliers, utilisateur, store, audit)


def bascule_controlee(
    bascule_id: str,
    utilisateur: Utilisateur,
    store: Store,
    audit: AuditSink,
    action: str = "lecture",
) -> BasculeProgressive:
    """Localise une bascule et applique le cloisonnement."""
    return localiser(
        store, utilisateur, bascule_id, "bascule", store.obtenir_bascule, audit, action
    )


@router.get("/v1/rollouts/{bascule_id}", response_model=BasculeProgressive)
def lire_bascule(
    bascule_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> BasculeProgressive:
    """Rend l'état d'une bascule (palier courant, qualité en ligne)."""
    return bascule_controlee(bascule_id, utilisateur, store, audit)


@router.post("/v1/rollouts/{bascule_id}/rollback", response_model=BasculeProgressive)
def annuler_bascule(
    bascule_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> BasculeProgressive:
    """Retour arrière manuel d'une bascule (EF-BAS-03)."""
    exiger_role(utilisateur, *ROLES_BASCULE)
    bascule = bascule_controlee(bascule_id, utilisateur, store, audit, "rollback")
    if not bascule.actif:
        raise EtatIncompatibleError("Cette bascule est déjà arrêtée", bascule_id=bascule.id)
    annulee = bascule.model_copy(
        update={
            "actif": False,
            "motif_annulation": f"Retour arrière demandé par {utilisateur.id}",
        }
    )
    store.enregistrer_bascule(annulee)
    reco = store.obtenir_recommandation(bascule.tenant_id, bascule.recommandation_id)
    if reco is not None:
        _marquer_recommandation(store, reco, StatutRecommandation.ANNULEE)
    journaliser(
        audit,
        utilisateur,
        "bascule_annulee",
        "bascule",
        bascule.id,
        {"recommandation_id": bascule.recommandation_id},
        tenant_id=bascule.tenant_id,
    )
    return annulee
