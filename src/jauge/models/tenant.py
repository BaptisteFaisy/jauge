"""Modèles de base : clients, applications et schéma d'attribution.

Répond aux exigences :
- EF-ATT-01 : modèle d'infrastructure pour le compte client (rôle, S3, tables).
- EF-ATT-03 : schéma d'attribution (dimensions, valeurs autorisées, défaut).
- ENF-CONF-01 : le plan de contrôle ne retient que des identifiants et métadonnées.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FormuleOffre(StrEnum):
    """Offres de service vendues par le MSP (§7.1)."""

    VISIBILITE = "visibilite"
    OPTIMISATION = "optimisation"
    OPTIMISATION_CONTINUE = "optimisation_continue"


class SchemaAttribution(BaseModel):
    """Schéma d'attribution fixé à la mise en service (EF-ATT-03)."""

    dimensions: list[str] = Field(default_factory=lambda: ["application", "team", "environment"])
    valeurs_equipes: list[str] = Field(default_factory=list)
    valeurs_environnements: list[str] = Field(
        default_factory=lambda: ["production", "staging", "dev"]
    )
    application_par_defaut: str = "non-attribue"


class Tenant(BaseModel):
    """Client final suivi par le MSP (§14)."""

    id: str
    nom: str
    compte_aws: str = Field(pattern=r"^\d{12}$")
    region_principale: str = "eu-west-1"
    role_arn: str
    external_id: str
    formule: FormuleOffre = FormuleOffre.OPTIMISATION
    plafond_campagne_usd: float = 50.0
    schema_attribution: SchemaAttribution = Field(default_factory=SchemaAttribution)
    modeles_autorises: list[str] = Field(default_factory=list)


class Application(BaseModel):
    """Application d'IA générative cliente couverte par Jauge (§14)."""

    id: str
    tenant_id: str
    nom: str
    equipe: str
    environnement: str = "production"
    modele_actuel: str
    seuil_qualite: float = Field(default=0.95, ge=0.5, le=1.0)
    seuil_intervalle_bas: float = Field(default=0.90, ge=0.5, le=1.0)
    relecture_humaine_autorisee: bool = True
    passe_par_passerelle: bool = True
    identifiant_passerelle: str | None = None
    description_tache: str = "Classification ou extraction"
