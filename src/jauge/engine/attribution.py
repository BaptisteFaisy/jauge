"""Agrégation quotidienne et rapprochement avec la facture AWS (EF-ATT-05..07)."""

from __future__ import annotations

from datetime import date

from jauge.models.cost import AgregatQuotidien, RapprochementJour
from jauge.models.pricing import CoutRequete, MetriquesRequete
from jauge.models.tenant import Application, Tenant


def agreger_requetes(
    tenant: Tenant,
    application: Application,
    jour: date,
    lignes: list[tuple[MetriquesRequete, CoutRequete]],
    latences_ms: list[int] | None = None,
) -> AgregatQuotidien:
    """Agrège une liste de requêtes d'un jour en un enregistrement consolidé."""
    nb = len(lignes)
    tot_in = sum(m.jetons_entree for m, _ in lignes)
    tot_out = sum(m.jetons_sortie for m, _ in lignes)
    tot_cw = sum(m.jetons_cache_ecriture for m, _ in lignes)
    tot_cr = sum(m.jetons_cache_lecture for m, _ in lignes)
    cout_tot = sum(c.cout_total for _, c in lignes)

    latences = sorted(latences_ms or [m.latence_ms for m, _ in lignes])
    p50 = latences[nb // 2] if latences else 0
    p95_idx = int(nb * 0.95) if latences else 0
    p95 = latences[min(p95_idx, nb - 1)] if latences else 0

    return AgregatQuotidien(
        tenant_id=tenant.id,
        application_id=application.id,
        equipe=application.equipe,
        environnement=application.environnement,
        modele_id=application.modele_actuel,
        jour=jour,
        nombre_requetes=nb,
        jetons_entree_total=tot_in,
        jetons_sortie_total=tot_out,
        jetons_cache_ecriture_total=tot_cw,
        jetons_cache_lecture_total=tot_cr,
        cout_recalcule_usd=round(cout_tot, 4),
        latence_p50_ms=p50,
        latence_p95_ms=p95,
    )


def rapprocher_facture(
    tenant_id: str,
    jour: date,
    cout_recalcule: float,
    cout_facture: float,
    depense_non_attribuee: float = 0.0,
    causes: list[str] | None = None,
) -> RapprochementJour:
    """Calcule l'écart entre le recalcul des journaux et la facture AWS."""
    ecart = cout_recalcule - cout_facture
    pct = (abs(ecart) / cout_facture * 100.0) if cout_facture > 0 else 0.0
    part_na = (depense_non_attribuee / cout_facture * 100.0) if cout_facture > 0 else 0.0
    conforme = pct <= 3.0  # Cible de production §4 : moins de 3 %

    return RapprochementJour(
        tenant_id=tenant_id,
        jour=jour,
        cout_recalcule_usd=round(cout_recalcule, 4),
        cout_facture_usd=round(cout_facture, 4),
        ecart_usd=round(ecart, 4),
        ecart_pourcentage=round(pct, 2),
        part_non_attribuee_pourcentage=round(part_na, 2),
        causes_non_attribuees=causes or [],
        conforme=conforme,
    )
