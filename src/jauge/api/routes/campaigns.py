"""Campagnes de rejeu (`POST /v1/applications/{id}/campaigns`, `GET /v1/campaigns/{id}`)."""

from __future__ import annotations

from fastapi import APIRouter, status

from jauge.api.audit import AuditSink, journaliser
from jauge.api.auth import RoleUtilisateur, Utilisateur
from jauge.api.deps import DepAudit, DepPortCampagnes, DepStore, DepUtilisateur
from jauge.api.killswitch import DepKillSwitch, garde_traitement
from jauge.api.ports import DemandeCampagne
from jauge.api.routes.applications import application_controlee
from jauge.api.tenancy import exiger_role, localiser
from jauge.common.errors import IntrouvableError
from jauge.models.campaign import Campagne, ResultatCandidat
from jauge.store.protocol import Store

router = APIRouter(tags=["campagnes"])

ROLES_LANCEMENT = (
    RoleUtilisateur.ADMINISTRATEUR_PLATEFORME,
    RoleUtilisateur.ANALYSTE_FINOPS,
)


@router.post(
    "/v1/applications/{app_id}/campaigns",
    response_model=Campagne,
    status_code=status.HTTP_201_CREATED,
)
def lancer_campagne(
    app_id: str,
    demande: DemandeCampagne,
    utilisateur: DepUtilisateur,
    store: DepStore,
    port: DepPortCampagnes,
    audit: DepAudit,
    kill_switch: DepKillSwitch,
) -> Campagne:
    """Lance une campagne de rejeu (déléguée au port M4, plafond client appliqué)."""
    exiger_role(utilisateur, *ROLES_LANCEMENT)
    application = application_controlee(app_id, utilisateur, store, audit, "lancement_campagne")
    # Kill-switch du client : s'il a coupé ses traitements, on refuse de lancer.
    garde_traitement(kill_switch, audit, utilisateur)(application.tenant_id, "lancement_campagne")
    tenant = store.obtenir_tenant(application.tenant_id)
    if tenant is None:  # pragma: no cover - garanti par application_controlee
        raise IntrouvableError("Client introuvable", tenant_id=application.tenant_id)
    campagne = port.lancer(tenant, application, demande)
    journaliser(
        audit,
        utilisateur,
        "campagne_lancee",
        "campagne",
        campagne.id,
        {
            "application_id": application.id,
            "modeles_candidats": campagne.modeles_candidats,
            "taille_echantillon": campagne.taille_echantillon,
            "cout_estime_usd": campagne.cout_estime_usd,
        },
        tenant_id=campagne.tenant_id,
        statut_http=201,
    )
    return campagne


def campagne_controlee(
    campagne_id: str, utilisateur: Utilisateur, store: Store, audit: AuditSink
) -> Campagne:
    """Localise une campagne et applique le cloisonnement."""
    return localiser(store, utilisateur, campagne_id, "campagne", store.obtenir_campagne, audit)


@router.get("/v1/campaigns/{campagne_id}", response_model=Campagne)
def lire_campagne(
    campagne_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> Campagne:
    """Rend l'état d'une campagne (statut, coûts, paramètres de rejeu)."""
    return campagne_controlee(campagne_id, utilisateur, store, audit)


@router.get("/v1/campaigns/{campagne_id}/results", response_model=list[ResultatCandidat])
def resultats_campagne(
    campagne_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> list[ResultatCandidat]:
    """Résultats consolidés par candidat (scores agrégés uniquement, ENF-CONF-01)."""
    campagne = campagne_controlee(campagne_id, utilisateur, store, audit)
    return campagne.resultats
