"""Leviers d'économie détectés (`GET /v1/applications/{id}/levers`) — Annexe A."""

from __future__ import annotations

from fastapi import APIRouter

from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.routes.applications import application_controlee
from jauge.models.lever import Levier

router = APIRouter(tags=["leviers"])


@router.get("/v1/applications/{app_id}/levers", response_model=list[Levier])
def lister_leviers(
    app_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> list[Levier]:
    """Leviers détectés sur une application, du plus économiquement rentable au moins."""
    application = application_controlee(app_id, utilisateur, store, audit)
    leviers = store.lister_leviers(application.tenant_id, app_id=app_id)
    return sorted(leviers, key=lambda lv: lv.economie_mensuelle_estimee_usd, reverse=True)
