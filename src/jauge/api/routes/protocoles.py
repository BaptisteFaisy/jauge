"""Protocole d'évaluation pré-enregistré et scellé (§13.1, §13.5 — piste n° 1).

Le protocole est figé **avant** le moindre rejeu : c'est ce qui sépare « on a
mesuré » de « on s'est engagé sur une méthode, puis on a mesuré ». La route le
construit à partir de l'**application du client** — ses seuils, pas ceux de
l'évaluateur — et le **scelle** : l'empreinte devient opposable et le protocole
devient immuable.

Le protocole rendu est la preuve : le client le conserve et le joint à sa
campagne ; la campagne n'en garde que l'empreinte, que
`GET /v1/campagnes/{id}/protocole` restitue.

Aucun texte de requête ni de réponse ne transite ici (ENF-CONF-01) : ce sont des
identifiants, des seuils, des graines et des empreintes.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from jauge.api.audit import journaliser
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.routes.applications import application_controlee
from jauge.api.routes.campaigns import campagne_controlee
from jauge.api.tenancy import exiger_role
from jauge.evaluation.protocole import (
    PlanEchantillonnage,
    ProtocoleEvaluation,
    construire_protocole,
    resume_pre_enregistrement,
    sceller,
)

router = APIRouter(tags=["protocoles"])

#: Rôles habilités à pré-enregistrer une méthode d'évaluation.
ROLES_PRENREGISTREMENT = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.ANALYSTE_FINOPS,
)


class DemandeProtocole(BaseModel):
    """Corps de `POST /v1/applications/{id}/protocoles`."""

    modeles_candidats: list[str] = Field(default_factory=list)
    modele_juge_id: str = Field(min_length=1)
    taille_echantillon: int | None = Field(default=None, ge=10, le=2000)
    fenetre_jours: int | None = Field(default=None, ge=1, le=365)
    graines: list[int] = Field(default_factory=list)
    strates: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    marge_non_inferiorite: float | None = Field(default=None, ge=0.0, le=1.0)
    scelle_par: str | None = Field(default=None, max_length=120)


@router.post(
    "/v1/applications/{app_id}/protocoles",
    response_model=ProtocoleEvaluation,
    status_code=status.HTTP_201_CREATED,
)
def pre_enregistrer_protocole(
    app_id: str,
    demande: DemandeProtocole,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> ProtocoleEvaluation:
    """Construit et scelle le protocole d'évaluation d'une application."""
    exiger_role(utilisateur, *ROLES_PRENREGISTREMENT)
    application = application_controlee(
        app_id, utilisateur, store, audit, "preregistration_protocole"
    )
    plan = PlanEchantillonnage(
        taille=demande.taille_echantillon or 200,
        fenetre_jours=demande.fenetre_jours or 14,
        graines=demande.graines,
        strates=demande.strates,
        exclusions=demande.exclusions,
    )
    protocole = construire_protocole(
        application=application,
        modeles_candidats=demande.modeles_candidats,
        modele_juge_id=demande.modele_juge_id,
        plan=plan,
        **(
            {"marge_non_inferiorite": demande.marge_non_inferiorite}
            if demande.marge_non_inferiorite is not None
            else {}
        ),
    )
    scelle = sceller(protocole, scelle_par=demande.scelle_par or utilisateur.id)
    journaliser(
        audit,
        utilisateur,
        "protocole_scelle",
        "protocole",
        application.id,
        {
            "empreinte": scelle.scelle.empreinte,
            "version": scelle.version,
            "scelle_par": scelle.scelle.scelle_par,
            "modele_juge_id": scelle.modele_juge_id,
        },
        tenant_id=application.tenant_id,
    )
    return scelle


@router.get("/v1/campagnes/{campagne_id}/protocole")
def lire_protocole_campagne(
    campagne_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> dict[str, object]:
    """Empreintes du protocole et de l'échantillon d'une campagne (aucun texte)."""
    campagne = campagne_controlee(campagne_id, utilisateur, store, audit)
    return resume_pre_enregistrement(campagne)
