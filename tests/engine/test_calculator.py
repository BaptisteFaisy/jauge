"""Tests du calcul de coût (Annexe D, EF-ATT-04)."""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, PETIT_MODELE, REGION

from jauge.common.errors import TarifInconnuError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier


@pytest.fixture
def table(tarifs: list) -> TableTarifs:
    return TableTarifs(tarifs)


def test_cout_sans_cache(table: TableTarifs) -> None:
    """1 000 jetons en entrée + 500 en sortie au tarif Standard."""
    tarif = table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 6, 1))
    metriques = MetriquesRequete(jetons_entree=1000, jetons_sortie=500, latence_ms=800)
    cout = calculer_cout_requete(tarif, metriques)
    assert cout.cout_entree == pytest.approx(0.015)
    assert cout.cout_sortie == pytest.approx(0.0375)
    assert cout.cout_total == pytest.approx(0.0525)


def test_cout_avec_cache(table: TableTarifs) -> None:
    """Les jetons lus en cache coûtent 10 fois moins que l'entrée."""
    tarif = table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 6, 1))
    metriques = MetriquesRequete(
        jetons_entree=200, jetons_sortie=100, jetons_cache_lecture=4000, jetons_cache_ecriture=0
    )
    cout = calculer_cout_requete(tarif, metriques)
    assert cout.cout_cache_lecture == pytest.approx(4000 / 1000 * 0.0015)
    assert cout.cout_total == pytest.approx(0.003 + 0.0075 + 0.006)


def test_niveau_flex_moins_cher(table: TableTarifs) -> None:
    """Le niveau Flex divise le coût par deux (remise vérifiée §3.2)."""
    metriques = MetriquesRequete(jetons_entree=1000, jetons_sortie=1000)
    std = calculer_cout_requete(
        table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 6, 1)), metriques
    )
    flex = calculer_cout_requete(
        table.trouver(GRAND_MODELE, REGION, ServiceTier.FLEX, date(2026, 6, 1)), metriques
    )
    assert flex.cout_total == pytest.approx(std.cout_total / 2)


def test_repli_sur_standard_si_niveau_absent(table: TableTarifs) -> None:
    """Un modèle sans ligne Priority retombe sur Standard plutôt que de planter."""
    tarif = table.trouver(PETIT_MODELE, REGION, ServiceTier.PRIORITY, date(2026, 6, 1))
    assert tarif.niveau == ServiceTier.STANDARD


def test_tarif_inconnu_leve_erreur(table: TableTarifs) -> None:
    """Modèle inconnu → TarifInconnuError (jamais un coût à 0 en silence)."""
    with pytest.raises(TarifInconnuError):
        table.trouver("modele.inexistant", REGION, ServiceTier.STANDARD, date(2026, 6, 1))


def test_tarif_date_effet(table: TableTarifs) -> None:
    """Une demande antérieure à toute date d'effet ne trouve pas de tarif."""
    with pytest.raises(TarifInconnuError):
        table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2025, 1, 1))
