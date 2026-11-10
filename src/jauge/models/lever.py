"""Modèles pour la détection des leviers d'économie (Annexe A, EF-LEV-01..06)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TypeLevier(StrEnum):
    """Les huit familles de leviers répertoriées en Annexe A."""

    MODELE_SURDIMENSIONNE = "modele_surdimensionne"
    CACHE_PROMPTS_ABSENT = "cache_prompts_absent"
    TRAITEMENT_DIFFERABLE = "traitement_differable"
    SORTIES_TROP_LONGUES = "sorties_trop_longues"
    REQUETES_REPETEES = "requetes_repetees"
    ERREURS_ET_RELANCES = "erreurs_et_relances"
    PRIORITY_SANS_BESOIN = "priority_sans_besoin"
    DISTILLATION = "distillation"


class StatutLevier(StrEnum):
    """Cycle de vie d'un levier détecté."""

    DETECTE = "detecte"
    EN_COURS_DE_PREUVE = "en_cours_de_preuve"
    PROUVE = "prouve"
    APPLIQUE = "applique"
    REJETE = "rejete"


class Levier(BaseModel):
    """Levier d'économie détecté sur les agrégats d'une application (§14)."""

    id: str
    tenant_id: str
    application_id: str
    type_levier: TypeLevier
    statut: StatutLevier = StatutLevier.DETECTE
    indices: list[str] = Field(default_factory=list)
    action_proposee: str
    preuve_exigee: str
    economie_mensuelle_estimee_usd: float = Field(ge=0.0)
    confiance: float = Field(default=0.8, ge=0.0, le=1.0)
    candidats_suggeres: list[str] = Field(default_factory=list)
