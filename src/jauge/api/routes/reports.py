"""Rapports mensuels (`POST /v1/tenants/{id}/reports`, `GET /v1/reports/{id}`) — EF-RAP-03."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from jauge.api.audit import journaliser
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepAudit, DepPortRapports, DepStore, DepUtilisateur
from jauge.api.killswitch import DepKillSwitch, garde_traitement
from jauge.api.tenancy import exiger_role, verifier_acces_tenant
from jauge.common.errors import IntrouvableError, ValidationError

router = APIRouter(tags=["rapports"])

ROLES_RAPPORT = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.ANALYSTE_FINOPS,
    RoleUtilisateur.RESPONSABLE_COMPTE,
)


class DemandeRapport(BaseModel):
    """Corps de `POST /v1/tenants/{id}/reports`."""

    periode_debut: date | None = None
    periode_fin: date | None = None


@router.post(
    "/v1/tenants/{tenant_id}/reports",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
def generer_rapport(
    tenant_id: str,
    demande: DemandeRapport,
    utilisateur: DepUtilisateur,
    store: DepStore,
    port: DepPortRapports,
    audit: DepAudit,
    kill_switch: DepKillSwitch,
) -> dict[str, Any]:
    """Génère le rapport mensuel d'un client (port M9 ; démo locale si activée)."""
    exiger_role(utilisateur, *ROLES_RAPPORT)
    verifier_acces_tenant(utilisateur, tenant_id, audit, "generation_rapport", "tenant")
    garde_traitement(kill_switch, audit, utilisateur)(tenant_id, "generation_rapport")
    if store.obtenir_tenant(tenant_id) is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)
    fin = demande.periode_fin or date.today()
    debut = demande.periode_debut or (fin - timedelta(days=30))
    if debut > fin:
        raise ValidationError(
            "La période de début doit précéder la période de fin",
            periode_debut=str(debut),
            periode_fin=str(fin),
        )
    rapport = port.generer(tenant_id, debut, fin)
    journaliser(
        audit,
        utilisateur,
        "rapport_genere",
        "rapport",
        str(rapport.get("id", "")),
        {"periode_debut": str(debut), "periode_fin": str(fin)},
        tenant_id=tenant_id,
        statut_http=201,
    )
    return rapport


@router.get("/v1/reports/{rapport_id}", response_model=dict[str, Any])
def lire_rapport(
    rapport_id: str,
    utilisateur: DepUtilisateur,
    port: DepPortRapports,
    audit: DepAudit,
) -> dict[str, Any]:
    """Rend un rapport déjà généré, après contrôle de cloisonnement."""
    rapport = port.obtenir(rapport_id)
    if rapport is None:
        raise IntrouvableError("Rapport introuvable", rapport_id=rapport_id)
    tenant_id = str(rapport.get("tenant_id", ""))
    verifier_acces_tenant(utilisateur, tenant_id, audit, "lecture_rapport", "rapport", rapport_id)
    return rapport
