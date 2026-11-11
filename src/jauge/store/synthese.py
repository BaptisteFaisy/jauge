"""Synthèse de coût d'une période — calcul unique partagé par les stores.

Extraite de `MemoryStore` pour qu'il n'existe **qu'une** implémentation (règle
user 2026-10-24 : zéro duplication) : le store DynamoDB et le store mémoire
rendent exactement les mêmes chiffres à partir des mêmes agrégats.

Tous les nombres sont calculés ici, par le code (ENF-IA-01).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date

from jauge.models.cost import AgregatQuotidien, RapprochementJour, SyntheseCout

#: Identifiant d'application des appels non étiquetés (§8.1, EF-ATT-03/07).
APPLICATION_NON_ATTRIBUEE = "non-attribue"


def synthese(
    tenant_id: str,
    agregats: Iterable[AgregatQuotidien],
    rapprochements: Iterable[RapprochementJour],
    applications_connues: Sequence[str],
    debut: date,
    fin: date,
) -> SyntheseCout:
    """Consolide une période : totaux par application, équipe et modèle.

    - `cout_total_usd` : somme des coûts recalculés depuis les journaux ;
    - `couverture_attribution_pourcentage` : part de la dépense rattachée à une
      application déclarée du client (la dépense `non-attribue` en est exclue,
      EF-ATT-07) ;
    - `ecart_rapprochement_moyen_pourcentage` : moyenne des écarts quotidiens
      avec la facture, telle qu'affichée au tableau de bord (EF-ATT-06).
    """
    par_app: dict[str, float] = defaultdict(float)
    par_equipe: dict[str, float] = defaultdict(float)
    par_modele: dict[str, float] = defaultdict(float)
    total = 0.0
    for agregat in agregats:
        par_app[agregat.application_id] += agregat.cout_recalcule_usd
        par_equipe[agregat.equipe] += agregat.cout_recalcule_usd
        par_modele[agregat.modele_id] += agregat.cout_recalcule_usd
        total += agregat.cout_recalcule_usd

    jours = [r for r in rapprochements if r.tenant_id == tenant_id]
    ecart_moyen = sum(r.ecart_pourcentage for r in jours) / len(jours) if jours else 0.0

    attribue = sum(
        cout
        for app, cout in par_app.items()
        if app in set(applications_connues) and app != APPLICATION_NON_ATTRIBUEE
    )
    couverture = (attribue / total * 100.0) if total > 0 else 0.0

    return SyntheseCout(
        tenant_id=tenant_id,
        periode_debut=debut,
        periode_fin=fin,
        cout_total_usd=round(total, 4),
        cout_par_application=dict(par_app),
        cout_par_equipe=dict(par_equipe),
        cout_par_modele=dict(par_modele),
        couverture_attribution_pourcentage=round(couverture, 2),
        ecart_rapprochement_moyen_pourcentage=round(ecart_moyen, 2),
    )
