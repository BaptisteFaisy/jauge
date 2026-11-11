"""Outils du lecteur de résultats (§12.1, §13.3, EF-EVA-03/05).

Les résultats renvoyés sont ceux du plan de contrôle : taux d'équivalence,
intervalle de confiance, échecs déterministes, coût par requête et latences.
Les familles en échec sont des étiquettes de catégorie, jamais des extraits de
requête (ENF-CONF-01).
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils, sortie
from jauge.agents.sdk import creer_outil


def outil_resultats(ctx: ContexteOutils) -> Any:
    """`get_replay_results` : résultats consolidés par candidat."""

    def get_replay_results(campagne_id: str) -> dict:
        """Résultats consolidés par candidat : taux, intervalle, coût, latence."""
        resultats = ctx.services.resultats.resultats(ctx.tenant_id, campagne_id)
        return sortie(
            ctx,
            {
                "campagne_id": campagne_id,
                "resultats": [
                    {
                        "modele_id": resultat.modele_id,
                        "est_reference": resultat.est_reference,
                        "nombre_requetes_rejouees": resultat.nombre_requetes_rejouees,
                        "taux_equivalence": resultat.taux_equivalence,
                        "intervalle_confiance_bas": resultat.intervalle_confiance_bas,
                        "intervalle_confiance_haut": resultat.intervalle_confiance_haut,
                        "auto_accord_modele_actuel": resultat.auto_accord_modele_actuel,
                        "echecs_deterministes": resultat.echecs_deterministes,
                        "accord_juge_humain": resultat.accord_juge_humain,
                        "cout_par_requete_usd": resultat.cout_par_requete_usd,
                        "latence_p50_ms": resultat.latence_p50_ms,
                        "latence_p95_ms": resultat.latence_p95_ms,
                        "familles_en_echec": list(resultat.familles_en_echec),
                        "conforme_seuils": resultat.conforme_seuils,
                    }
                    for resultat in resultats
                ],
            },
        )

    return creer_outil(
        get_replay_results,
        "get_replay_results",
        "Résultats consolidés d'une campagne par candidat : taux d'équivalence, intervalle "
        "de confiance, échecs déterministes, coût par requête, latences p50/p95 et respect "
        "des seuils. Aucun texte de requête.",
    )


def outil_clusters(ctx: ContexteOutils) -> Any:
    """`get_failure_clusters` : familles où un candidat échoue."""

    def get_failure_clusters(campagne_id: str) -> dict:
        """Familles de requêtes où un candidat échoue (étiquettes, pas de texte)."""
        clusters = ctx.services.resultats.familles_en_echec(ctx.tenant_id, campagne_id)
        return sortie(
            ctx,
            {
                "campagne_id": campagne_id,
                "clusters": [
                    {
                        "modele_id": cluster.modele_id,
                        "famille": cluster.famille,
                        "nombre_requetes": cluster.nombre_requetes,
                        "nombre_echecs": cluster.nombre_echecs,
                        "taux_echec": cluster.taux_echec,
                    }
                    for cluster in clusters
                ],
            },
        )

    return creer_outil(
        get_failure_clusters,
        "get_failure_clusters",
        "Familles de requêtes où un candidat échoue, avec le nombre d'échecs et le taux "
        "d'échec. Les familles sont des étiquettes de catégorie, jamais des extraits de "
        "requête.",
    )
