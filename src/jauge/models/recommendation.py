"""Modèles pour recommandations, bascule par passerelle et vérification.

Répond aux exigences :
- EF-REC-01 : format normalisé de recommandation (Annexe B).
- EF-REC-02 : approbation explicite du responsable d'application.
- EF-BAS-01..03 : bascule par paliers et rollback automatique.
- EF-VER-01..02 : vérification à volume constant 30 jours après.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class StatutRecommandation(StrEnum):
    """Cycle de vie d'une recommandation (§14, Annexe B)."""

    EN_ATTENTE_APPROBATION = "PENDING_APPROVAL"
    APPROUVEE = "APPROVED"
    REFUSEE = "REJECTED"
    EN_COURS_DE_BASCULE = "ROLLING_OUT"
    APPLIQUEE = "APPLIED"
    ANNULEE = "ROLLED_BACK"


class MetriquesQualite(BaseModel):
    """Bloc `quality` de la recommandation (Annexe B)."""

    equivalenceRate: float = Field(ge=0.0, le=1.0)
    confidenceIntervalLow: float = Field(ge=0.0, le=1.0)
    selfAgreementOfCurrentModel: float = Field(ge=0.0, le=1.0)
    deterministicFailures: int = Field(default=0, ge=0)
    judgeHumanAgreement: float | None = Field(default=None, ge=0.0, le=1.0)


class MetriquesCout(BaseModel):
    """Bloc `cost` de la recommandation (Annexe B)."""

    costPerRequestBefore: float = Field(ge=0.0)
    costPerRequestAfter: float = Field(ge=0.0)
    monthlyRequests: int = Field(ge=0)
    expectedMonthlySaving: float = Field(ge=0.0)
    currency: str = "USD"


class MetriquesLatence(BaseModel):
    """Bloc `latency` de la recommandation (Annexe B)."""

    p50Before: int = Field(ge=0)
    p50After: int = Field(ge=0)
    p95Before: int = Field(ge=0)
    p95After: int = Field(ge=0)
    unit: str = "ms"


class Recommandation(BaseModel):
    """Recommandation formelle remise au client (Annexe B, §14)."""

    id: str
    tenantId: str
    applicationId: str
    currentModel: str
    targetModel: str
    campaignId: str
    sampleSize: int
    quality: MetriquesQualite
    cost: MetriquesCout
    latency: MetriquesLatence
    risks: list[str] = Field(default_factory=list)
    rolloutPlan: list[int] = Field(default_factory=lambda: [10, 50, 100])
    status: StatutRecommandation = StatutRecommandation.EN_ATTENTE_APPROBATION
    approvedBy: str | None = None
    approvalNotes: str | None = None


class BasculeProgressive(BaseModel):
    """État d'une bascule en cours par règle de passerelle (EF-BAS-01..03)."""

    id: str
    tenant_id: str
    recommandation_id: str
    application_id: str
    modele_cible: str
    regle_passerelle_id: str
    palier_actuel_pourcentage: int = 10
    qualite_en_ligne_mesuree: float = 1.0
    seuil_alerte: float = 0.90
    actif: bool = True
    annule_automatiquement: bool = False
    motif_annulation: str | None = None


class VerificationEconomie(BaseModel):
    """Vérification à 30 jours à volume constant (EF-VER-01)."""

    application_id: str
    recommandation_id: str
    cout_par_requete_avant: float
    cout_par_requete_apres: float
    volume_periode: int
    economie_constatee_usd: float
    economie_attendue_usd: float
    ecart_pourcentage: float
