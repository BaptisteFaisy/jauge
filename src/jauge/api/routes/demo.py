"""Démonstration pour les juges (EF-POR-02) et consultation du journal d'audit (EF-ADM-02)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from jauge.api.audit import EvenementAudit
from jauge.api.auth import (
    RoleUtilisateur,
    Utilisateur,
    compte_demonstration_juges,
)
from jauge.api.deps import DepAudit, DepUtilisateur
from jauge.api.tenancy import exiger_role

router = APIRouter(tags=["démonstration"])


class IdentiteDemo(BaseModel):
    """Identité de l'appelant et compte de démonstration mis à disposition des juges."""

    utilisateur: Utilisateur
    compte_demo_juges: Utilisateur
    roles_disponibles: list[str]
    entetes_demo: dict[str, str]


@router.get("/v1/demo/identity", response_model=IdentiteDemo)
def identite_demo(utilisateur: DepUtilisateur) -> IdentiteDemo:
    """Expose le compte de démonstration et les en-têtes à utiliser (EF-POR-02).

    Un juge peut prendre n'importe quel rôle en envoyant `X-Jauge-Role` et
    n'importe quel client avec `X-Jauge-Tenant`, sans configuration préalable.
    """
    return IdentiteDemo(
        utilisateur=utilisateur,
        compte_demo_juges=compte_demonstration_juges(),
        roles_disponibles=[r.value for r in RoleUtilisateur],
        entetes_demo={
            "X-Jauge-Tenant": "t-demo-a",
            "X-Jauge-Role": RoleUtilisateur.ADMINISTRATEUR_PLATEFORME.value,
            "X-Jauge-User": "juge-hackathon",
        },
    )


@router.get("/v1/audit", response_model=list[EvenementAudit])
def lire_audit(
    utilisateur: DepUtilisateur,
    audit: DepAudit,
) -> list[EvenementAudit]:
    """Journal d'audit visible du client, filtré sur son périmètre (EF-ADM-02)."""
    if utilisateur.est_admin:
        return audit.lister()
    if not utilisateur.tenant_id:
        exiger_role(utilisateur, RoleUtilisateur.ADMINISTRATEUR_PLATEFORME)
    return audit.lister(tenant_id=utilisateur.tenant_id)
