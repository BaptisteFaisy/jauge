"""Outils du planificateur de rejeu (§12.1, §8.4, EF-REJ-01, ENF-COUT-01).

Le planificateur choisit les candidats, la taille d'échantillon et les
contrôles ; il ne calcule rien : l'estimation de coût vient du service
d'estimation (niveau Flex), et le lancement est refusé si le plafond du client
est dépassé ou si un candidat sort de sa liste d'autorisation (§12.3).
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils, sortie
from jauge.agents.guardrails import verifier_candidats_autorises, verifier_plafond
from jauge.agents.sdk import creer_outil

TAILLE_ECHANTILLON_DEFAUT = 200
"""Taille de référence du §13.1 (200 requêtes, ±3 % à 95 %)."""


def outil_profil(ctx: ContexteOutils) -> Any:
    """`get_task_profile` : type de tâche, contrôles, seuils, volumes."""

    def get_task_profile(application_id: str) -> dict:
        """Profil de la tâche : type, contrôles déterministes, seuils, volumes."""
        profil = ctx.services.profils.profil_tache(ctx.tenant_id, application_id)
        return sortie(
            ctx,
            {
                "application_id": profil.application_id,
                "type_tache": profil.type_tache,
                "controles_deterministes": list(profil.controles_deterministes),
                "seuil_qualite": profil.seuil_qualite,
                "seuil_intervalle_bas": profil.seuil_intervalle_bas,
                "relecture_humaine": profil.relecture_humaine,
                "modele_actuel": profil.modele_actuel,
                "volume_mensuel_requetes": profil.volume_mensuel_requetes,
                "jetons_entree_moyens": profil.jetons_entree_moyens,
                "jetons_sortie_moyens": profil.jetons_sortie_moyens,
                "stratums": list(profil.stratums),
            },
        )

    return creer_outil(
        get_task_profile,
        "get_task_profile",
        "Profil d'une application : type de tâche, contrôles déterministes à appliquer, "
        "seuil de qualité et borne basse, volumes et jetons moyens. Sert à choisir la "
        "taille d'échantillon et les contrôles du rejeu.",
    )


def outil_candidats(ctx: ContexteOutils) -> Any:
    """`list_candidate_models` : candidats autorisés par le client."""

    def list_candidate_models(application_id: str) -> dict:
        """Candidats autorisés pour ce client (liste d'autorisation appliquée)."""
        application = ctx.services.referentiel.application(ctx.tenant_id, application_id)
        tenant = ctx.services.referentiel.tenant(ctx.tenant_id)
        autorises = set(tenant.modeles_autorises)
        candidats = [
            candidat
            for candidat in ctx.services.candidats.modeles_candidats(ctx.tenant_id, application_id)
            if candidat.autorise and candidat.modele_id in autorises
        ]
        return sortie(
            ctx,
            {
                "application_id": application_id,
                "modele_actuel": application.modele_actuel,
                "candidats": [
                    {
                        "modele_id": candidat.modele_id,
                        "famille": candidat.famille,
                        "fenetre_contexte": candidat.fenetre_contexte,
                        "niveau_service": candidat.niveau_service,
                        "tarif_connu": candidat.tarif_connu,
                    }
                    for candidat in candidats
                ],
            },
        )

    return creer_outil(
        list_candidate_models,
        "list_candidate_models",
        "Modèles candidats autorisés par le client pour une application, avec leur "
        "famille, leur fenêtre de contexte et la disponibilité de leur tarif. Les modèles "
        "hors liste d'autorisation ne sont jamais renvoyés.",
    )


def outil_estimation(ctx: ContexteOutils) -> Any:
    """`estimate_campaign_cost` : coût estimé et plafond."""

    def estimate_campaign_cost(
        application_id: str,
        modeles_candidats: list[str],
        taille_echantillon: int = TAILLE_ECHANTILLON_DEFAUT,
    ) -> dict:
        """Coût estimé d'une campagne (référence + candidats, niveau Flex)."""
        tenant = ctx.services.referentiel.tenant(ctx.tenant_id)
        verifier_candidats_autorises(tenant, modeles_candidats)
        estimation = ctx.services.estimation.estimer(
            ctx.tenant_id, application_id, modeles_candidats, taille_echantillon
        )
        return sortie(
            ctx,
            {
                "application_id": application_id,
                "devise": estimation.devise,
                "montant_estime_usd": estimation.montant_usd,
                "plafond_usd": estimation.plafond_usd,
                "dans_le_plafond": estimation.dans_le_plafond,
                "taille_echantillon": estimation.taille_echantillon,
                "par_modele_usd": estimation.par_modele_usd,
            },
        )

    return creer_outil(
        estimate_campaign_cost,
        "estimate_campaign_cost",
        "Estimation du coût d'une campagne de rejeu (modèle actuel + candidats, niveau "
        "Flex) et comparaison au plafond du client. Le montant est calculé par le code, "
        "jamais par le modèle.",
    )


def outil_rejeu(ctx: ContexteOutils) -> Any:
    """`start_replay` : lancement après contrôle du plafond."""

    def start_replay(
        application_id: str,
        modeles_candidats: list[str],
        taille_echantillon: int = TAILLE_ECHANTILLON_DEFAUT,
        graine: int = 42,
    ) -> dict:
        """Lance le rejeu après contrôle du plafond et de la liste autorisée."""
        tenant = ctx.services.referentiel.tenant(ctx.tenant_id)
        verifier_candidats_autorises(tenant, modeles_candidats)
        estimation = ctx.services.estimation.estimer(
            ctx.tenant_id, application_id, modeles_candidats, taille_echantillon
        )
        verifier_plafond(estimation)
        with ctx.traceur.tracer(
            "planificateur_rejeu",
            "tache_rejeu",
            tenant_id=ctx.tenant_id,
            campagne_id=ctx.campagne_id,
            application_id=application_id,
            taille_echantillon=taille_echantillon,
        ) as details:
            campagne_id = ctx.services.rejeu.lancer(
                ctx.tenant_id, application_id, modeles_candidats, taille_echantillon, graine
            )
            details["campagne_rejeu_id"] = campagne_id
        if ctx.session is not None:
            ctx.session.reprendre(campagne_id)
        return sortie(
            ctx,
            {
                "campagne_id": campagne_id,
                "application_id": application_id,
                "statut": "planifiee",
                "taille_echantillon": taille_echantillon,
                "graine": graine,
                "cout_estime_usd": estimation.montant_usd,
                "plafond_usd": estimation.plafond_usd,
            },
        )

    return creer_outil(
        start_replay,
        "start_replay",
        "Lance une campagne de rejeu sur un échantillon de requêtes, après contrôle du "
        "plafond du client et de la liste des modèles autorisés. Renvoie l'identifiant de "
        "campagne et le coût estimé.",
    )
