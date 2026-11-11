"""Schémas de sortie structurée des agents, validés avant enregistrement (§12.3).

Deux garanties :

- **rien d'informe n'est enregistré** : chaque sortie d'agent passe par un
  modèle pydantic strict (`extra="forbid"`), et toute sortie non conforme lève
  `jauge.common.errors.ValidationError` ;
- **aucun chiffre inventé** (ENF-IA-01) : la recommandation est composée par le
  code à partir des valeurs calculées (`EntreesRecommandation`) ; l'agent ne
  fournit que `risks` et `status`, et tout nombre cité dans un texte doit
  figurer parmi les valeurs autorisées.

Format de la recommandation : Annexe B du cahier des charges (§8.5).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from jauge.agents.ports import EntreesRecommandation
from jauge.common.errors import ValidationError
from jauge.common.ids import aleatoire
from jauge.engine.stats import economie_mensuelle
from jauge.models.recommendation import Recommandation, StatutRecommandation

PLAN_BASCULE_ATTENDU = [10, 50, 100]
"""Paliers imposés par l'Annexe C et le §8.6."""

STATUTS_REDIGEABLES = frozenset(
    {StatutRecommandation.EN_ATTENTE_APPROBATION, StatutRecommandation.REFUSEE}
)
"""Statuts qu'un agent a le droit de proposer : jamais `APPROVED`, jamais `APPLIED`."""

_LONGUEUR_MAX_TEXTE = 300
_MOTIF_NOMBRE = re.compile(r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


def _texte_propre(valeur: str) -> str:
    """Refuse un texte vide, trop long ou porteur de caractères de contrôle."""
    propre = valeur.strip()
    if not propre:
        raise ValueError("texte vide")
    if len(propre) > _LONGUEUR_MAX_TEXTE:
        raise ValueError(f"texte de plus de {_LONGUEUR_MAX_TEXTE} caractères")
    if any(ord(caractere) < 32 and caractere not in "\n\t" for caractere in propre):
        raise ValueError("caractère de contrôle dans le texte")
    return propre


class AnalyseCouts(BaseModel):
    """Sortie de l'analyste des coûts (§12.1) : constats et leviers, pas de calcul."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=1, max_length=64)
    periode_jours: int = Field(ge=1, le=365)
    cout_total_usd: float = Field(ge=0.0)
    constats: list[str] = Field(min_length=1, max_length=6)
    leviers_prioritaires: list[str] = Field(default_factory=list, max_length=5)
    reponse: str = Field(default="", max_length=1200)

    @field_validator("constats")
    @classmethod
    def _constats_propres(cls, valeurs: list[str]) -> list[str]:
        return [_texte_propre(valeur) for valeur in valeurs]


class PlanRejeu(BaseModel):
    """Sortie du planificateur de rejeu (§8.4, EF-REJ-01/EF-EVA-02)."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=1, max_length=64)
    modeles_candidats: list[str] = Field(min_length=1, max_length=5)
    taille_echantillon: int = Field(ge=10, le=2000)
    controles_deterministes: list[str] = Field(default_factory=list, max_length=10)
    justification: str = Field(min_length=1, max_length=800)

    @field_validator("modeles_candidats")
    @classmethod
    def _candidats_uniques(cls, valeurs: list[str]) -> list[str]:
        if len(set(valeurs)) != len(valeurs):
            raise ValueError("candidat présent deux fois")
        return valeurs


class LectureResultats(BaseModel):
    """Sortie du lecteur de résultats (§12.1) : lecture, pas de recalcul."""

    model_config = ConfigDict(extra="forbid")

    campagne_id: str = Field(min_length=1, max_length=64)
    candidat_retenu: str | None = Field(default=None, max_length=64)
    constats: list[str] = Field(min_length=1, max_length=6)
    familles_en_echec: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("constats")
    @classmethod
    def _constats_propres(cls, valeurs: list[str]) -> list[str]:
        return [_texte_propre(valeur) for valeur in valeurs]


class RedactionRecommandation(BaseModel):
    """Ce que l'agent rédacteur a le droit de produire : des textes et un statut.

    Les blocs chiffrés (`quality`, `cost`, `latency`), les identifiants et le
    plan de bascule viennent du code (`EntreesRecommandation`). Un agent qui
    tenterait d'écrire `APPROVED` ou `APPLIED` est rejeté.
    """

    model_config = ConfigDict(extra="forbid")

    risks: list[str] = Field(default_factory=list, max_length=5)
    status: StatutRecommandation = StatutRecommandation.EN_ATTENTE_APPROBATION

    @field_validator("risks")
    @classmethod
    def _risques_propres(cls, valeurs: list[str]) -> list[str]:
        return [_texte_propre(valeur) for valeur in valeurs]

    @field_validator("status")
    @classmethod
    def _statut_autorise(cls, valeur: StatutRecommandation) -> StatutRecommandation:
        if valeur not in STATUTS_REDIGEABLES:
            raise ValueError(
                f"un agent ne peut pas écrire le statut {valeur.value} "
                "(approbation et bascule sont humaines, ENF-IA-02)"
            )
        return valeur


def valider_sortie(modele: type[BaseModel], donnees: object) -> BaseModel:
    """Valide une sortie d'agent ; convertit l'erreur pydantic en erreur métier."""
    try:
        return modele.model_validate(donnees)
    except PydanticValidationError as erreur:
        raise ValidationError(
            f"Sortie non conforme au schéma {modele.__name__}",
            schema=modele.__name__,
            erreurs=erreur.errors(include_url=False),
        ) from erreur


def nombres_cites(texte: str) -> list[float]:
    """Nombres présents dans un texte (virgule décimale, espaces de milliers)."""
    nombres: list[float] = []
    for motif in _MOTIF_NOMBRE.findall(texte):
        nettoye = motif.replace("\u00a0", "").replace(" ", "").replace(",", ".")
        nombres.append(float(nettoye))
    return nombres


def verifier_chiffres_autorises(texte: str, autorisees: set[float]) -> None:
    """Refuse un texte qui cite un nombre absent des valeurs calculées (ENF-IA-01).

    Les taux sont acceptés en fraction (0,975) comme en pourcentage (97,5).
    """
    tolere = set(autorisees) | {round(valeur * 100.0, 6) for valeur in autorisees}
    tolere.update({round(valeur, 2) for valeur in list(tolere)})
    for nombre in nombres_cites(texte):
        if not any(abs(nombre - valeur) <= 0.01 for valeur in tolere):
            raise ValidationError(
                "Un texte d'agent cite un nombre qui n'a pas été calculé par le code",
                nombre=nombre,
                texte=texte,
            )


def composer_recommandation(
    entrees: EntreesRecommandation,
    redaction: RedactionRecommandation,
    recommandation_id: str | None = None,
) -> Recommandation:
    """Assemble la recommandation de l'Annexe B : chiffres du code, textes de l'agent."""
    for risque in redaction.risks:
        verifier_chiffres_autorises(risque, entrees.valeurs_autorisees())
    return Recommandation(
        id=recommandation_id or aleatoire("recommendation"),
        tenantId=entrees.tenant_id,
        applicationId=entrees.application_id,
        currentModel=entrees.modele_actuel,
        targetModel=entrees.modele_cible,
        campaignId=entrees.campagne_id,
        sampleSize=entrees.taille_echantillon,
        quality=entrees.quality,
        cost=entrees.cost,
        latency=entrees.latency,
        risks=list(redaction.risks),
        rolloutPlan=list(entrees.plan_bascule),
        status=redaction.status,
    )


def valider_recommandation(
    recommandation: Recommandation, modeles_autorises: list[str] | None = None
) -> Recommandation:
    """Contrôles finaux avant enregistrement (Annexe B, §12.3, ENF-IA-01).

    L'économie attendue est recalculée avec `jauge.engine.stats` : si un modèle
    a produit un chiffre, l'écart est détecté ici et la recommandation est
    rejetée.
    """
    if recommandation.currentModel == recommandation.targetModel:
        raise ValidationError("Le modèle cible est identique au modèle actuel")
    if recommandation.rolloutPlan != PLAN_BASCULE_ATTENDU:
        raise ValidationError(
            "Plan de bascule non conforme à l'Annexe C",
            attendu=PLAN_BASCULE_ATTENDU,
            recu=recommandation.rolloutPlan,
        )
    if recommandation.status not in STATUTS_REDIGEABLES:
        raise ValidationError(
            "Une recommandation enregistrée doit être en attente d'approbation",
            statut=recommandation.status.value,
        )
    if recommandation.cost.currency != "USD":
        raise ValidationError("Devise non conforme", devise=recommandation.cost.currency)
    if modeles_autorises and recommandation.targetModel not in modeles_autorises:
        raise ValidationError(
            "Modèle cible hors de la liste autorisée du client",
            modele=recommandation.targetModel,
        )
    attendu = economie_mensuelle(
        recommandation.cost.costPerRequestBefore,
        recommandation.cost.costPerRequestAfter,
        recommandation.cost.monthlyRequests,
    )
    if abs(attendu - recommandation.cost.expectedMonthlySaving) > 0.01:
        raise ValidationError(
            "Économie mensuelle incohérente avec les coûts par requête",
            attendu=attendu,
            recu=recommandation.cost.expectedMonthlySaving,
        )
    if recommandation.quality.confidenceIntervalLow > recommandation.quality.equivalenceRate:
        raise ValidationError("Borne basse d'intervalle supérieure au taux observé")
    return recommandation
