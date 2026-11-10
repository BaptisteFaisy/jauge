"""Modèles de données pour les agrégats de coût et le rapprochement facture.

Répond aux exigences :
- EF-ATT-05 : agrégation par application, équipe, environnement, modèle, jour.
- EF-ATT-06 : rapprochement coût recalculé / coût facturé, calcul de l'écart.
- EF-ATT-07 : signalement de la part non attribuée et des causes.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class AgregatQuotidien(BaseModel):
    """Agrégat d'un jour pour une application et un modèle (§14)."""

    tenant_id: str
    application_id: str
    equipe: str
    environnement: str
    modele_id: str
    jour: date
    nombre_requetes: int = Field(ge=0)
    jetons_entree_total: int = Field(ge=0)
    jetons_sortie_total: int = Field(ge=0)
    jetons_cache_ecriture_total: int = Field(default=0, ge=0)
    jetons_cache_lecture_total: int = Field(default=0, ge=0)
    cout_recalcule_usd: float = Field(ge=0.0)
    latence_p50_ms: int = Field(default=0, ge=0)
    latence_p95_ms: int = Field(default=0, ge=0)


class RapprochementJour(BaseModel):
    """Comparaison quotidienne entre coût recalculé et facture (EF-ATT-06)."""

    tenant_id: str
    jour: date
    cout_recalcule_usd: float = Field(ge=0.0)
    cout_facture_usd: float = Field(ge=0.0)
    ecart_usd: float
    ecart_pourcentage: float
    part_non_attribuee_pourcentage: float = Field(ge=0.0, le=100.0)
    causes_non_attribuees: list[str] = Field(default_factory=list)
    conforme: bool = True  # True si écart < 3% (cible de production §4)


class SyntheseCout(BaseModel):
    """Vue consolidée pour le tableau de bord client (EF-RAP-01)."""

    tenant_id: str
    periode_debut: date
    periode_fin: date
    cout_total_usd: float
    cout_par_application: dict[str, float]
    cout_par_equipe: dict[str, float]
    cout_par_modele: dict[str, float]
    couverture_attribution_pourcentage: float
    ecart_rapprochement_moyen_pourcentage: float
