"""Coûts par application et rapprochement facture (§15, EF-ATT-05..07)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.routes.applications import application_controlee
from jauge.api.tenancy import verifier_acces_tenant
from jauge.common.errors import IntrouvableError
from jauge.models.cost import AgregatQuotidien, RapprochementJour

router = APIRouter(tags=["coûts"])

DepDateDebut = Annotated[date | None, Query(description="Premier jour inclus (AAAA-MM-JJ)")]
DepDateFin = Annotated[date | None, Query(description="Dernier jour inclus (AAAA-MM-JJ)")]


class CoutParJour(BaseModel):
    """Ligne quotidienne agrégée pour un modèle donné."""

    jour: date
    modele_id: str
    nombre_requetes: int
    cout_recalcule_usd: float


class CoutApplication(BaseModel):
    """Réponse de `GET /v1/applications/{id}/costs`."""

    tenant_id: str
    application_id: str
    periode_debut: date
    periode_fin: date
    cout_total_usd: float
    requetes_total: int
    par_jour: list[CoutParJour] = Field(default_factory=list)
    par_modele: dict[str, float] = Field(default_factory=dict)
    agregats: list[AgregatQuotidien] = Field(default_factory=list)


def _periode(debut: date | None, fin: date | None) -> tuple[date, date]:
    """Période par défaut : les trente derniers jours."""
    fin_reelle = fin or date.today()
    debut_reel = debut or (fin_reelle - timedelta(days=30))
    return debut_reel, fin_reelle


@router.get("/v1/applications/{app_id}/costs", response_model=CoutApplication)
def couts_application(
    app_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
    debut: DepDateDebut = None,
    fin: DepDateFin = None,
) -> CoutApplication:
    """Coûts par jour et par modèle d'une application.

    Écart connu : les agrégats du socle ne portent pas le type de requête ;
    la ventilation par type arrivera avec le module d'attribution (M1).
    """
    application = application_controlee(app_id, utilisateur, store, audit)
    debut_reel, fin_reelle = _periode(debut, fin)
    agregats = store.lister_agregats(
        application.tenant_id, app_id=app_id, debut=debut_reel, fin=fin_reelle
    )
    par_modele: dict[str, float] = {}
    for agregat in agregats:
        par_modele[agregat.modele_id] = round(
            par_modele.get(agregat.modele_id, 0.0) + agregat.cout_recalcule_usd, 4
        )
    return CoutApplication(
        tenant_id=application.tenant_id,
        application_id=app_id,
        periode_debut=debut_reel,
        periode_fin=fin_reelle,
        cout_total_usd=round(sum(a.cout_recalcule_usd for a in agregats), 4),
        requetes_total=sum(a.nombre_requetes for a in agregats),
        par_jour=[
            CoutParJour(
                jour=a.jour,
                modele_id=a.modele_id,
                nombre_requetes=a.nombre_requetes,
                cout_recalcule_usd=a.cout_recalcule_usd,
            )
            for a in agregats
        ],
        par_modele=par_modele,
        agregats=agregats,
    )


@router.get("/v1/tenants/{tenant_id}/reconciliation", response_model=list[RapprochementJour])
def rapprochement(
    tenant_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> list[RapprochementJour]:
    """Rapprochement quotidien entre coût recalculé et facture AWS (EF-ATT-06)."""
    verifier_acces_tenant(utilisateur, tenant_id, audit, "lecture_rapprochement", "tenant")
    if store.obtenir_tenant(tenant_id) is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)
    return store.lister_rapprochements(tenant_id)
