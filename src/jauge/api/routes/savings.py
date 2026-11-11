"""Économies attendues et constatées (`GET /v1/tenants/{id}/savings`) — EF-VER-01..02."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.tenancy import verifier_acces_tenant
from jauge.common.errors import IntrouvableError
from jauge.models.recommendation import VerificationEconomie

router = APIRouter(tags=["économies"])


class EconomiesClient(BaseModel):
    """Réponse de `GET /v1/tenants/{id}/savings`."""

    tenant_id: str
    economie_attendue_usd: float
    economie_constatee_usd: float
    ecart_pourcentage: float
    verifications: list[VerificationEconomie] = Field(default_factory=list)


@router.get("/v1/tenants/{tenant_id}/savings", response_model=EconomiesClient)
def economies(
    tenant_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> EconomiesClient:
    """Somme les vérifications à 30 jours du client, à volume constant."""
    verifier_acces_tenant(utilisateur, tenant_id, audit, "lecture_economies", "tenant")
    if store.obtenir_tenant(tenant_id) is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)
    verifications = store.lister_verifications(tenant_id)
    attendue = round(sum(v.economie_attendue_usd for v in verifications), 2)
    constatee = round(sum(v.economie_constatee_usd for v in verifications), 2)
    ecart = round((constatee - attendue) / attendue * 100.0, 2) if attendue > 0 else 0.0
    return EconomiesClient(
        tenant_id=tenant_id,
        economie_attendue_usd=attendue,
        economie_constatee_usd=constatee,
        ecart_pourcentage=ecart,
        verifications=verifications,
    )
