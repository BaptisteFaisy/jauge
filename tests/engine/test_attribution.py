"""Tests d'agrégation et de rapprochement (EF-ATT-05, EF-ATT-06, EF-ATT-07)."""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, REGION

from jauge.engine.attribution import agreger_requetes, rapprocher_facture
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.models.tenant import Application, Tenant


def test_agregation_dune_journee(tenant: Tenant, app_classement: Application, tarifs: list) -> None:
    """Trois requêtes d'un jour → un agrégat avec coûts et latences consolidés."""
    table = TableTarifs(tarifs)
    tarif = table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 6, 1))
    lignes = []
    for entree, sortie in [(800, 60), (1200, 40), (1000, 50)]:
        metriques = MetriquesRequete(jetons_entree=entree, jetons_sortie=sortie)
        lignes.append((metriques, calculer_cout_requete(tarif, metriques)))

    agregat = agreger_requetes(tenant, app_classement, date(2026, 6, 1), lignes, [400, 900, 600])

    assert agregat.nombre_requetes == 3
    assert agregat.jetons_entree_total == 3000
    assert agregat.jetons_sortie_total == 150
    assert agregat.equipe == "support"
    assert agregat.latence_p50_ms == 600
    assert agregat.latence_p95_ms == 900
    assert agregat.cout_recalcule_usd > 0


def test_rapprochement_conforme(tenant: Tenant) -> None:
    """Un écart de 2 % est conforme (cible < 3 % au §4)."""
    r = rapprocher_facture(tenant.id, date(2026, 6, 1), cout_recalcule=1020.0, cout_facture=1000.0)
    assert r.conforme is True
    assert r.ecart_pourcentage == pytest.approx(2.0)


def test_rapprochement_non_conforme(tenant: Tenant) -> None:
    """Un écart de 8 % sort de la cible : l'alerte doit remonter au portail."""
    r = rapprocher_facture(tenant.id, date(2026, 6, 1), cout_recalcule=920.0, cout_facture=1000.0)
    assert r.conforme is False
    assert r.ecart_usd == pytest.approx(-80.0)


def test_part_non_attribuee_et_causes(tenant: Tenant) -> None:
    """La dépense non attribuée et ses causes sont affichées (EF-ATT-07)."""
    r = rapprocher_facture(
        tenant.id,
        date(2026, 6, 1),
        cout_recalcule=1000.0,
        cout_facture=1000.0,
        depense_non_attribuee=40.0,
        causes=["appels sans étiquette", "point d'accès non journalisé"],
    )
    assert r.part_non_attribuee_pourcentage == pytest.approx(4.0)
    assert len(r.causes_non_attribuees) == 2


def test_rapprochement_sans_facture(tenant: Tenant) -> None:
    """Facture à 0 : pas de division par zéro, écart en % nul."""
    r = rapprocher_facture(tenant.id, date(2026, 6, 1), cout_recalcule=0.0, cout_facture=0.0)
    assert r.ecart_pourcentage == 0.0
    assert r.conforme is True
