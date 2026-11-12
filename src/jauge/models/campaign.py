"""Modèles pour les campagnes de rejeu et l'évaluation (EF-REJ, EF-EVA, Annexe B).

ENF-CONF-01 : les textes de requête et de réponse ne figurent QUE dans
`PaireRejoueeDataPlane`, stockée exclusivement dans le compte client.
Le plan de contrôle n'en reçoit que des scores agrégés (`ResultatCandidat`).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class StatutCampagne(StrEnum):
    """Cycle de vie d'une campagne de rejeu (§8.4)."""

    CREEE = "creee"
    PLANIFIEE = "planifiee"
    REJEU_EN_COURS = "rejeu_en_cours"
    EVALUATION_EN_COURS = "evaluation_en_cours"
    TERMINEE = "terminee"
    ECHOUEE = "echouee"


class ParametresRejeu(BaseModel):
    """Paramètres d'inférence reproduits à l'identique (§13.2)."""

    temperature: float = 0.0
    max_tokens: int = 1024
    top_p: float = 1.0
    stop_sequences: list[str] = Field(default_factory=list)


class EchantillonRequete(BaseModel):
    """Métadonnées d'une requête sélectionnée pour l'échantillon (§13.1)."""

    requete_id: str
    application_id: str
    type_requete: str = "general"
    longueur_caracteres: int = Field(ge=0)
    jetons_entree_origine: int = Field(ge=0)
    stratum: str = "court"


class ResultatCandidat(BaseModel):
    """Résultat consolidé pour un candidat de la campagne (§14, Annexe B)."""

    modele_id: str
    est_reference: bool = False
    nombre_requetes_rejouees: int = Field(ge=0)
    taux_equivalence: float = Field(ge=0.0, le=1.0)
    intervalle_confiance_bas: float = Field(ge=0.0, le=1.0)
    intervalle_confiance_haut: float = Field(ge=0.0, le=1.0)
    auto_accord_modele_actuel: float = Field(default=1.0, ge=0.0, le=1.0)
    echecs_deterministes: int = Field(default=0, ge=0)
    accord_juge_humain: float | None = Field(default=None, ge=0.0, le=1.0)
    cout_par_requete_usd: float = Field(ge=0.0)
    latence_p50_ms: int = Field(default=0, ge=0)
    latence_p95_ms: int = Field(default=0, ge=0)
    familles_en_echec: list[str] = Field(default_factory=list)
    conforme_seuils: bool = False


class Campagne(BaseModel):
    """Campagne de rejeu sur une application (§14).

    Les deux empreintes de preuve (protocole pré-enregistré, échantillon figé)
    sont **additives** et optionnelles : les campagnes déjà enregistrées restent
    lisibles telles quelles, aucune migration n'est requise.
    """

    id: str
    tenant_id: str
    application_id: str
    modeles_candidats: list[str]
    taille_echantillon: int = Field(default=200, ge=10, le=2000)
    graine_aleatoire: int = 42
    modele_juge_id: str = "eu.anthropic.claude-3-5-sonnet-20241022-v2:0"
    statut: StatutCampagne = StatutCampagne.CREEE
    cout_estime_usd: float = Field(ge=0.0)
    cout_reel_usd: float = Field(default=0.0, ge=0.0)
    resultats: list[ResultatCandidat] = Field(default_factory=list)
    #: Empreinte SHA-256 du protocole scellé avant le rejeu (§13, piste n° 1).
    protocole_empreinte: str | None = None
    #: Empreinte SHA-256 de l'échantillon figé dès son tirage (§13.1).
    echantillon_empreinte: str | None = None


class PaireRejoueeDataPlane(BaseModel):
    """Paquet de rejeu vivant STRICTEMENT dans le compte client (ENF-CONF-01)."""

    campagne_id: str
    requete_id: str
    modele_candidat_id: str
    texte_requete: str
    reponse_reference: str
    reponse_candidate: str
    score_equivalence: float = Field(ge=0.0, le=1.0)
    explication_juge: str = ""
    controles_deterministes_ok: bool = True
