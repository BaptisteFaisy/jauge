"""Recommandations et décisions (`GET .../recommendations`, `POST .../decision`)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from jauge.api.audit import AuditSink, journaliser
from jauge.api.auth import RoleUtilisateur, Utilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.routes.applications import application_controlee
from jauge.api.tenancy import exiger_role, localiser
from jauge.common.errors import EtatIncompatibleError
from jauge.models.recommendation import Recommandation, StatutRecommandation
from jauge.store.protocol import Store

router = APIRouter(tags=["recommandations"])

ROLES_DECISION = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.RESPONSABLE_APPLICATION,
)

STATUTS_DECISION = {
    "approuver": StatutRecommandation.APPROUVEE,
    "refuser": StatutRecommandation.REFUSEE,
}


class DemandeDecision(BaseModel):
    """Corps de `POST /v1/recommendations/{id}/decision` (EF-REC-02)."""

    decision: Literal["approuver", "refuser"]
    motif: str | None = None


@router.get("/v1/applications/{app_id}/recommendations", response_model=list[Recommandation])
def lister_recommandations(
    app_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> list[Recommandation]:
    """Recommandations d'une application (Annexe B)."""
    application = application_controlee(app_id, utilisateur, store, audit)
    return store.lister_recommandations(application.tenant_id, app_id=app_id)


def recommandation_controlee(
    reco_id: str,
    utilisateur: Utilisateur,
    store: Store,
    audit: AuditSink,
    action: str = "lecture",
) -> Recommandation:
    """Localise une recommandation et applique le cloisonnement."""
    return localiser(
        store, utilisateur, reco_id, "recommandation", store.obtenir_recommandation, audit, action
    )


@router.post("/v1/recommendations/{reco_id}/decision", response_model=Recommandation)
def decider_recommandation(
    reco_id: str,
    demande: DemandeDecision,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> Recommandation:
    """Approuve ou refuse une recommandation (ENF-IA-02 : décision humaine)."""
    exiger_role(utilisateur, *ROLES_DECISION)
    reco = recommandation_controlee(reco_id, utilisateur, store, audit, "decision")
    if reco.status != StatutRecommandation.EN_ATTENTE_APPROBATION:
        raise EtatIncompatibleError(
            "Cette recommandation a déjà été tranchée",
            recommandation_id=reco.id,
            statut=reco.status.value,
        )
    statut = STATUTS_DECISION[demande.decision]
    mise_a_jour = reco.model_copy(
        update={
            "status": statut,
            "approvedBy": utilisateur.email or utilisateur.id,
            "approvalNotes": demande.motif,
        }
    )
    store.enregistrer_recommandation(mise_a_jour)
    journaliser(
        audit,
        utilisateur,
        "decision_recommandation",
        "recommandation",
        reco.id,
        {
            "decision": demande.decision,
            "motif": demande.motif,
            "statut": statut.value,
            "application_id": reco.applicationId,
        },
        tenant_id=reco.tenantId,
    )
    return mise_a_jour
