"""Modèles de tarification Amazon Bedrock (Annexe D, EF-ATT-04).

Tous les prix sont exprimés en USD pour 1 000 jetons (tarification AWS usuelle).
La table des tarifs est datée : un changement crée une nouvelle ligne avec sa
date d'effet, sans recalculer les agrégats passés.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class ServiceTier(StrEnum):
    """Niveaux de service Bedrock (§3.2, Annexe D)."""

    STANDARD = "standard"
    FLEX = "flex"
    PRIORITY = "priority"
    RESERVED = "reserved"


class TarifModele(BaseModel):
    """Tarif d'un modèle pour une région, un niveau et une date d'effet."""

    modele_id: str
    region: str
    niveau: ServiceTier = ServiceTier.STANDARD
    date_effet: date
    prix_entree_1k: float = Field(ge=0.0)
    prix_sortie_1k: float = Field(ge=0.0)
    prix_cache_ecriture_1k: float = Field(default=0.0, ge=0.0)
    prix_cache_lecture_1k: float = Field(default=0.0, ge=0.0)
    devise: str = "USD"


class MetriquesRequete(BaseModel):
    """Jetons et latence d'une requête, lus dans les journaux d'invocation."""

    jetons_entree: int = Field(ge=0)
    jetons_sortie: int = Field(ge=0)
    jetons_cache_ecriture: int = Field(default=0, ge=0)
    jetons_cache_lecture: int = Field(default=0, ge=0)
    latence_ms: int = Field(default=0, ge=0)


class CoutRequete(BaseModel):
    """Décomposition du coût d'une requête calculé par le code (Annexe D)."""

    cout_entree: float
    cout_sortie: float
    cout_cache_ecriture: float
    cout_cache_lecture: float
    cout_total: float
    devise: str = "USD"
