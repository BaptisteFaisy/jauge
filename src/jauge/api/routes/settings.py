"""Paramètres d'administration (`GET/PATCH /v1/settings`) — EF-ADM-01.

Réservé à l'administrateur de plateforme : table des tarifs, modèles candidats
autorisés par client, seuils, taille d'échantillon, paliers de bascule.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body

from jauge.api.audit import journaliser
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepAudit, DepReglages, DepUtilisateur
from jauge.api.reglages import ParametresAdmin, appliquer_patch
from jauge.api.tenancy import exiger_role

router = APIRouter(tags=["administration"])

CorpsPatch = Annotated[
    dict[str, Any], Body(description="Mise à jour partielle des paramètres d'administration")
]


@router.get("/v1/settings", response_model=ParametresAdmin)
def lire_parametres(
    utilisateur: DepUtilisateur,
    reglages: DepReglages,
) -> ParametresAdmin:
    """Rend les paramètres courants (lecture ouverte aux rôles MSP)."""
    exiger_role(
        utilisateur,
        RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
        RoleUtilisateur.ANALYSTE_FINOPS,
        RoleUtilisateur.RESPONSABLE_COMPTE,
    )
    return reglages.obtenir()


@router.patch("/v1/settings", response_model=ParametresAdmin)
def modifier_parametres(
    patch: CorpsPatch,
    utilisateur: DepUtilisateur,
    reglages: DepReglages,
    audit: DepAudit,
) -> ParametresAdmin:
    """Applique une mise à jour partielle, validée avant enregistrement."""
    exiger_role(utilisateur, RoleUtilisateur.ADMINISTRATEUR_PLATEFORME)
    courants = reglages.obtenir()
    nouveaux = appliquer_patch(courants, patch)
    reglages.remplacer(nouveaux)
    journaliser(
        audit,
        utilisateur,
        "modification_parametres",
        "parametres",
        "settings",
        {"cles_modifiees": sorted(patch.keys())},
    )
    return nouveaux
