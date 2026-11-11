"""Tests de la date d'effet d'une table datée (EF-ATT-04, Annexe D).

La règle du cahier des charges : un changement de tarif crée une nouvelle ligne
avec sa date d'effet ; les coûts passés ne sont pas recalculés. On vérifie donc
que le tarif applicable est le plus récent antérieur ou égal à la date demandée,
et que le coût d'une requête passée reste celui de l'ancien tarif.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from tests.conftest import GRAND_MODELE, REGION

from jauge.common.errors import TarifInconnuError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.pricing_data import charger_table

ANCIEN = 0.010  # tarif en vigueur jusqu'au 30 juin 2026 inclus
NOUVEAU = 0.020  # tarif en vigueur à partir du 1er juillet 2026


@pytest.fixture
def table_datee(tmp_path: Path) -> TableTarifs:
    """Table à deux dates d'effet pour le même (modèle, région, niveau)."""
    lignes = [
        {
            "modele_id": GRAND_MODELE,
            "region": REGION,
            "niveau": "standard",
            "date_effet": "2026-01-01",
            "prix_entree_1k": ANCIEN,
            "prix_sortie_1k": ANCIEN,
            "devise": "USD",
        },
        {
            "modele_id": GRAND_MODELE,
            "region": REGION,
            "niveau": "standard",
            "date_effet": "2026-07-01",
            "prix_entree_1k": NOUVEAU,
            "prix_sortie_1k": NOUVEAU,
            "devise": "USD",
        },
    ]
    chemin = tmp_path / "table-datee.json"
    chemin.write_text(
        json.dumps({"region": REGION, "devise": "USD", "tarifs": lignes}), encoding="utf-8"
    )
    return charger_table(chemin)


def test_tarif_avant_la_date_d_effet(table_datee: TableTarifs) -> None:
    """Au 30 juin 2026, c'est encore l'ancien tarif qui s'applique."""
    tarif = table_datee.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 6, 30))
    assert tarif.prix_entree_1k == pytest.approx(ANCIEN)
    assert tarif.date_effet == date(2026, 1, 1)


def test_tarif_a_la_date_d_effet(table_datee: TableTarifs) -> None:
    """Le jour même de la date d'effet, le nouveau tarif s'applique."""
    tarif = table_datee.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 7, 1))
    assert tarif.prix_entree_1k == pytest.approx(NOUVEAU)


def test_tarif_apres_la_date_d_effet(table_datee: TableTarifs) -> None:
    """Après le 1er juillet 2026, le nouveau tarif reste applicable."""
    tarif = table_datee.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2027, 3, 15))
    assert tarif.prix_entree_1k == pytest.approx(NOUVEAU)


def test_couts_passes_non_recalcules(table_datee: TableTarifs) -> None:
    """Une requête de juin garde son coût d'origine après le changement de tarif."""
    metriques = MetriquesRequete(jetons_entree=1000, jetons_sortie=0)
    tarif_juin = table_datee.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 6, 1))
    tarif_juillet = table_datee.trouver(
        GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2026, 7, 1)
    )
    cout_juin = calculer_cout_requete(tarif_juin, metriques)
    cout_juillet = calculer_cout_requete(tarif_juillet, metriques)
    assert cout_juin.cout_total == pytest.approx(ANCIEN)
    assert cout_juillet.cout_total == pytest.approx(NOUVEAU)
    # Recalculer juin avec le tarif de juillet est précisément ce qu'il ne faut pas faire.
    assert cout_juin.cout_total != pytest.approx(cout_juillet.cout_total)


def test_date_anterieure_a_toute_ligne_leve_tarif_inconnu(table_datee: TableTarifs) -> None:
    """Aucun tarif n'existe avant la première date d'effet : erreur, pas 0 en silence."""
    with pytest.raises(TarifInconnuError):
        table_datee.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, date(2025, 12, 31))


def test_repli_sur_standard_si_niveau_absent(table_datee: TableTarifs) -> None:
    """Un niveau sans ligne propre retombe sur le Standard (comportement du socle)."""
    tarif = table_datee.trouver(GRAND_MODELE, REGION, ServiceTier.FLEX, date(2026, 8, 1))
    assert tarif.niveau == ServiceTier.STANDARD
    assert tarif.prix_entree_1k == pytest.approx(NOUVEAU)
