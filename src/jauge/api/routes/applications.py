"""Routes applications (`POST /v1/tenants/{id}/applications`, `GET /v1/applications/{id}`)."""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from jauge.api.audit import AuditSink, journaliser
from jauge.api.auth import RoleUtilisateur, Utilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.tenancy import exiger_role, localiser, verifier_acces_tenant
from jauge.common.errors import IntrouvableError
from jauge.common.ids import aleatoire
from jauge.models.tenant import Application
from jauge.store.protocol import Store

router = APIRouter(tags=["applications"])


class CreationApplication(BaseModel):
    """Corps de `POST /v1/tenants/{id}/applications`."""

    nom: str = Field(min_length=1)
    equipe: str = Field(min_length=1)
    environnement: str = "production"
    modele_actuel: str = Field(min_length=1)
    seuil_qualite: float = Field(default=0.95, ge=0.5, le=1.0)
    seuil_intervalle_bas: float = Field(default=0.90, ge=0.5, le=1.0)
    relecture_humaine_autorisee: bool = True
    passe_par_passerelle: bool = True
    identifiant_passerelle: str | None = None
    description_tache: str = "Classification ou extraction"


def application_controlee(
    app_id: str,
    utilisateur: Utilisateur,
    store: Store,
    audit: AuditSink,
    action: str = "lecture",
) -> Application:
    """Localise une application et applique le cloisonnement (helper partagé)."""
    return localiser(
        store,
        utilisateur,
        app_id,
        "application",
        store.obtenir_application,
        audit,
        action,
    )


@router.post(
    "/v1/tenants/{tenant_id}/applications",
    response_model=Application,
    status_code=status.HTTP_201_CREATED,
)
def creer_application(
    tenant_id: str,
    corps: CreationApplication,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> Application:
    """Rattache une application à un client existant."""
    exiger_role(utilisateur, RoleUtilisateur.ADMINISTRATEUR_PLATEFORME)
    verifier_acces_tenant(utilisateur, tenant_id, audit, "creation_application", "tenant")
    if store.obtenir_tenant(tenant_id) is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)
    application = Application(
        id=aleatoire("application"),
        tenant_id=tenant_id,
        nom=corps.nom,
        equipe=corps.equipe,
        environnement=corps.environnement,
        modele_actuel=corps.modele_actuel,
        seuil_qualite=corps.seuil_qualite,
        seuil_intervalle_bas=corps.seuil_intervalle_bas,
        relecture_humaine_autorisee=corps.relecture_humaine_autorisee,
        passe_par_passerelle=corps.passe_par_passerelle,
        identifiant_passerelle=corps.identifiant_passerelle,
        description_tache=corps.description_tache,
    )
    store.enregistrer_application(application)
    journaliser(
        audit,
        utilisateur,
        "creation_application",
        "application",
        application.id,
        {"nom": application.nom, "equipe": application.equipe},
        tenant_id=tenant_id,
    )
    return application


@router.get("/v1/tenants/{tenant_id}/applications", response_model=list[Application])
def lister_applications(
    tenant_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> list[Application]:
    """Liste les applications d'un client (cloisonnement appliqué)."""
    verifier_acces_tenant(utilisateur, tenant_id, audit, "lecture_applications", "tenant")
    if store.obtenir_tenant(tenant_id) is None:
        raise IntrouvableError("Client introuvable", tenant_id=tenant_id)
    return store.lister_applications(tenant_id)


@router.get("/v1/applications/{app_id}", response_model=Application)
def lire_application(
    app_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> Application:
    """Rend une application après contrôle de cloisonnement."""
    return application_controlee(app_id, utilisateur, store, audit)
