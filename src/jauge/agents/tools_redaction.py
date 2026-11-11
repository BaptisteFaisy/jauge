"""Outils du rédacteur (§12.1, §8.5, Annexe B, §12.3).

Deux outils seulement, et une séparation stricte :

- `get_recommendation_inputs` fournit les **valeurs calculées** (qualité, coût,
  latence, plan de bascule) et la liste des nombres qu'un texte peut citer ;
- `save_recommendation` compose la recommandation avec ces valeurs, valide le
  schéma, puis exige une **validation humaine** avant l'enregistrement : le
  premier appel ouvre la demande et s'arrête là (§12.3, ENF-IA-02).
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils, sortie
from jauge.agents.guardrails import exiger_validation_humaine, interdire_action_agent
from jauge.agents.schemas import (
    RedactionRecommandation,
    composer_recommandation,
    valider_recommandation,
    valider_sortie,
)
from jauge.agents.sdk import creer_outil
from jauge.common.errors import ApprobationRequiseError


def outil_entrees(ctx: ContexteOutils) -> Any:
    """`get_recommendation_inputs` : valeurs calculées de l'Annexe B."""

    def get_recommendation_inputs(campagne_id: str) -> dict:
        """Valeurs calculées de la recommandation (blocs de l'Annexe B)."""
        entrees = ctx.services.entrees.valeurs(ctx.tenant_id, campagne_id)
        return sortie(
            ctx,
            {
                "campagne_id": campagne_id,
                "application_id": entrees.application_id,
                "modele_actuel": entrees.modele_actuel,
                "modele_cible": entrees.modele_cible,
                "taille_echantillon": entrees.taille_echantillon,
                "quality": entrees.quality.model_dump(),
                "cost": entrees.cost.model_dump(),
                "latency": entrees.latency.model_dump(),
                "plan_bascule": list(entrees.plan_bascule),
                "nombres_de_contexte": list(entrees.nombres_contexte),
                "valeurs_autorisees_dans_les_textes": sorted(entrees.valeurs_autorisees()),
            },
        )

    return creer_outil(
        get_recommendation_inputs,
        "get_recommendation_inputs",
        "Valeurs déjà calculées d'une recommandation (qualité, coût, latence, plan de "
        "bascule) et liste des nombres qu'un texte peut citer. À recopier tel quel : aucun "
        "chiffre ne doit être recalculé.",
    )


def _preparer(
    ctx: ContexteOutils, campagne_id: str, risques: list[str], statut: str
) -> tuple[Any, Any]:
    """Compose et valide la recommandation : chiffres du code, textes de l'agent."""
    entrees = ctx.services.entrees.valeurs(ctx.tenant_id, campagne_id)
    interdire_action_agent(str(statut))
    redaction = valider_sortie(RedactionRecommandation, {"risks": list(risques), "status": statut})
    tenant = ctx.services.referentiel.tenant(ctx.tenant_id)
    recommandation = composer_recommandation(entrees, redaction)
    valider_recommandation(recommandation, tenant.modeles_autorises)
    return entrees, recommandation


def _interruption(ctx: ContexteOutils, campagne_id: str, erreur: ApprobationRequiseError) -> dict:
    """Charge utile renvoyée quand l'enregistrement est interrompu par l'humain."""
    demande_id = erreur.details.get("demande_id")
    if ctx.session is not None and demande_id is not None:
        ctx.session.attendre_validation(str(demande_id))
    return {
        "statut": "validation_humaine_requise",
        "campagne_id": campagne_id,
        "demande_id": demande_id,
        "message": erreur.message,
    }


def _resultat(entrees: Any, recommandation: Any, recommandation_id: str) -> dict:
    """Charge utile renvoyée après enregistrement réussi."""
    return {
        "statut": "enregistree",
        "recommandation_id": recommandation_id,
        "campagne_id": entrees.campagne_id,
        "application_id": entrees.application_id,
        "modele_cible": entrees.modele_cible,
        "statut_recommandation": recommandation.status.value,
        "economie_mensuelle_attendue_usd": entrees.cost.expectedMonthlySaving,
    }


def _exiger_accord(ctx: ContexteOutils, entrees: Any, campagne_id: str, demande: str | None):
    """Ouvre ou contrôle la validation humaine (§12.3).

    Sans identifiant de demande, une demande est créée et l'appel s'arrête là.
    Avec un identifiant non approuvé, le refus est propagé : un agent ne
    contourne pas l'interruption (ENF-IA-02).
    """
    try:
        exiger_validation_humaine(
            ctx.services.validation,
            ctx.tenant_id,
            entrees.application_id,
            campagne_id,
            entrees.modele_cible,
            demande,
        )
    except ApprobationRequiseError as erreur:
        if demande is not None:
            raise
        return _interruption(ctx, campagne_id, erreur)
    return None


def outil_enregistrement(ctx: ContexteOutils) -> Any:
    """`save_recommendation` : rédaction validée + validation humaine."""

    def save_recommendation(
        campagne_id: str,
        risques: list[str],
        statut: str = "PENDING_APPROVAL",
        validation_humaine_id: str | None = None,
    ) -> dict:
        """Compose, valide et enregistre la recommandation, après validation humaine."""
        entrees, recommandation = _preparer(ctx, campagne_id, risques, statut)
        interruption = _exiger_accord(ctx, entrees, campagne_id, validation_humaine_id)
        if interruption is not None:
            return sortie(ctx, interruption)
        with ctx.traceur.tracer(
            "redacteur", "enregistrement_recommandation", tenant_id=ctx.tenant_id
        ):
            recommandation_id = ctx.services.enregistrement.enregistrer(
                ctx.tenant_id, recommandation
            )
        if ctx.session is not None:
            ctx.session.cloturer(recommandation_id)
        return sortie(ctx, _resultat(entrees, recommandation, recommandation_id))

    return creer_outil(
        save_recommendation,
        "save_recommendation",
        "Rédige puis enregistre la recommandation de l'Annexe B : les blocs chiffrés "
        "viennent des valeurs calculées, l'agent ne fournit que les risques (texte) et le "
        "statut. Un premier appel sans validation_humaine_id ouvre une demande de "
        "validation humaine ; l'enregistrement n'a lieu qu'après approbation.",
    )
