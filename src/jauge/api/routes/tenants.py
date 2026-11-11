"""Routes clients (`POST /v1/tenants`, `GET /v1/tenants/{id}`) — §15, EF-ATT-03."""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from jauge.api.audit import journaliser
from jauge.api.auth import RoleUtilisateur
from jauge.api.deps import DepAudit, DepStore, DepUtilisateur
from jauge.api.tenancy import exiger_role, obtenir_tenant_controle
from jauge.common.errors import ValidationError
from jauge.common.ids import aleatoire
from jauge.models.tenant import FormuleOffre, SchemaAttribution, Tenant

router = APIRouter(tags=["clients"])


class CreationTenant(BaseModel):
    """Corps de `POST /v1/tenants` : identifiant et identifiant externe optionnels.

    `schema_attribution` porte le schéma fixé avec le client à la mise en
    service (EF-ATT-03) : dimensions d'attribution, valeurs autorisées et
    application par défaut pour les appels non étiquetés.
    """

    nom: str = Field(min_length=1)
    compte_aws: str = Field(pattern=r"^\d{12}$")
    role_arn: str = Field(min_length=1)
    region_principale: str = "eu-west-1"
    external_id: str | None = None
    formule: FormuleOffre = FormuleOffre.OPTIMISATION
    plafond_campagne_usd: float = Field(default=50.0, ge=0.0)
    modeles_autorises: list[str] = Field(default_factory=list)
    schema_attribution: SchemaAttribution = Field(default_factory=SchemaAttribution)


@router.post("/v1/tenants", response_model=Tenant, status_code=status.HTTP_201_CREATED)
def creer_tenant(
    corps: CreationTenant,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> Tenant:
    """Crée un client (réservé à l'administrateur de plateforme)."""
    exiger_role(utilisateur, RoleUtilisateur.ADMINISTRATEUR_PLATEFORME)
    tenant = Tenant(
        id=aleatoire("tenant"),
        nom=corps.nom,
        compte_aws=corps.compte_aws,
        region_principale=corps.region_principale,
        role_arn=corps.role_arn,
        external_id=corps.external_id or aleatoire("tenant"),
        formule=corps.formule,
        plafond_campagne_usd=corps.plafond_campagne_usd,
        modeles_autorises=corps.modeles_autorises,
        schema_attribution=corps.schema_attribution,
    )
    if store.obtenir_tenant(tenant.id) is not None:
        raise ValidationError("Un client porte déjà cet identifiant", tenant_id=tenant.id)
    store.enregistrer_tenant(tenant)
    journaliser(audit, utilisateur, "creation_tenant", "tenant", tenant.id, tenant_id=tenant.id)
    return tenant


@router.get("/v1/tenants", response_model=list[Tenant])
def lister_tenants(utilisateur: DepUtilisateur, store: DepStore) -> list[Tenant]:
    """Liste les clients visibles par l'appelant (cloisonnement appliqué)."""
    tous = store.lister_tenants()
    if utilisateur.est_admin or utilisateur.role == RoleUtilisateur.ANALYSTE_FINOPS:
        return tous
    autorises = set(utilisateur.tenants_autorises)
    if utilisateur.tenant_id:
        autorises.add(utilisateur.tenant_id)
    return [t for t in tous if t.id in autorises]


@router.get("/v1/tenants/{tenant_id}", response_model=Tenant)
def lire_tenant(
    tenant_id: str,
    utilisateur: DepUtilisateur,
    store: DepStore,
    audit: DepAudit,
) -> Tenant:
    """Rend un client après contrôle de cloisonnement."""
    return obtenir_tenant_controle(store, utilisateur, tenant_id, audit)
