"""Outils de l'analyste des coûts (§12.1) : agrégats, leviers, rapprochement.

Aucun de ces outils ne renvoie de texte de requête : uniquement des agrégats,
des montants calculés par le code et des identifiants (ENF-CONF-01, ENF-IA-01).
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils, sortie
from jauge.agents.sdk import creer_outil


def outil_agregats(ctx: ContexteOutils) -> Any:
    """`query_cost_aggregates` : totaux, répartition et tendance."""

    def query_cost_aggregates(application_id: str, jours: int = 30) -> dict:
        """Agrégats de coût d'une application : total, par modèle, par équipe, tendance."""
        ctx.services.referentiel.application(ctx.tenant_id, application_id)
        debut, fin = ctx.periode(jours)
        synthese = ctx.services.agregats.synthese(ctx.tenant_id, application_id, debut, fin)
        agregats = ctx.services.agregats.agregats(ctx.tenant_id, application_id, debut, fin)
        par_jour: dict[str, float] = {}
        for agr in agregats:
            cle = agr.jour.isoformat()
            par_jour[cle] = round(par_jour.get(cle, 0.0) + agr.cout_recalcule_usd, 4)
        jours_tries = sorted(par_jour.items())
        moitie = len(jours_tries) // 2
        debut_somme = sum(cout for _, cout in jours_tries[:moitie])
        fin_somme = sum(cout for _, cout in jours_tries[moitie:])
        variation = (
            round((fin_somme - debut_somme) / debut_somme * 100.0, 2) if debut_somme > 0 else 0.0
        )
        return sortie(
            ctx,
            {
                "application_id": application_id,
                "periode_jours": jours,
                "devise": "USD",
                "cout_total_usd": synthese.cout_total_usd,
                "nombre_requetes": sum(agr.nombre_requetes for agr in agregats),
                "cout_par_modele_usd": synthese.cout_par_modele,
                "cout_par_equipe_usd": synthese.cout_par_equipe,
                "variation_seconde_moitie_pourcentage": variation,
                "jours_couverts": len(par_jour),
                "couverture_attribution_pourcentage": synthese.couverture_attribution_pourcentage,
            },
        )

    return creer_outil(
        query_cost_aggregates,
        "query_cost_aggregates",
        "Agrégats de coût d'une application sur une fenêtre glissante : total, "
        "répartition par modèle et par équipe, variation sur la seconde moitié de la "
        "période. Renvoie des agrégats et des identifiants uniquement.",
    )


def outil_leviers(ctx: ContexteOutils) -> Any:
    """`list_levers` : leviers détectés, classés par économie estimée."""

    def list_levers(application_id: str) -> dict:
        """Leviers d'économie détectés, avec leur économie mensuelle estimée."""
        ctx.services.referentiel.application(ctx.tenant_id, application_id)
        leviers = ctx.services.leviers.leviers(ctx.tenant_id, application_id)
        tries = sorted(leviers, key=lambda levier: levier.economie_mensuelle_estimee_usd)
        tries.reverse()
        return sortie(
            ctx,
            {
                "application_id": application_id,
                "leviers": [
                    {
                        "levier_id": levier.id,
                        "type": levier.type_levier.value,
                        "statut": levier.statut.value,
                        "economie_mensuelle_estimee_usd": levier.economie_mensuelle_estimee_usd,
                        "confiance": levier.confiance,
                        "candidats_suggeres": list(levier.candidats_suggeres),
                    }
                    for levier in tries
                ],
                "economie_totale_estimee_usd": round(
                    sum(levier.economie_mensuelle_estimee_usd for levier in tries), 2
                ),
            },
        )

    return creer_outil(
        list_levers,
        "list_levers",
        "Leviers d'économie détectés pour une application (modèle surdimensionné, "
        "cache absent, traitement différable…), classés par économie mensuelle estimée "
        "décroissante. Renvoie des identifiants et des montants calculés.",
    )


def outil_rapprochement(ctx: ContexteOutils) -> Any:
    """`reconcile_with_bill` : écart entre recalcul et facture."""

    def reconcile_with_bill(jours: int = 30) -> dict:
        """Écart entre le coût recalculé depuis les journaux et la facture AWS."""
        debut, fin = ctx.periode(jours)
        jours_rapproches = ctx.services.rapprochement.rapprochements(ctx.tenant_id, debut, fin)
        recalcul = round(sum(jour.cout_recalcule_usd for jour in jours_rapproches), 4)
        facture = round(sum(jour.cout_facture_usd for jour in jours_rapproches), 4)
        ecart = round(recalcul - facture, 4)
        ecart_pct = round(abs(ecart) / facture * 100.0, 2) if facture > 0 else 0.0
        part_non_attribuee = (
            round(
                sum(jour.part_non_attribuee_pourcentage for jour in jours_rapproches)
                / len(jours_rapproches),
                2,
            )
            if jours_rapproches
            else 0.0
        )
        return sortie(
            ctx,
            {
                "periode_jours": jours,
                "jours_rapproches": len(jours_rapproches),
                "devise": "USD",
                "cout_recalcule_usd": recalcul,
                "cout_facture_usd": facture,
                "ecart_usd": ecart,
                "ecart_pourcentage": ecart_pct,
                "conforme": ecart_pct <= 3.0,
                "part_non_attribuee_pourcentage": part_non_attribuee,
            },
        )

    return creer_outil(
        reconcile_with_bill,
        "reconcile_with_bill",
        "Rapprochement entre le coût recalculé depuis les journaux et la facture AWS sur "
        "une fenêtre glissante : écart en dollars et en pourcentage, conformité à la "
        "cible de 3 %. Renvoie des agrégats uniquement.",
    )
