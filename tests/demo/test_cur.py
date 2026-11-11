"""Tests de cohérence entre le trafic, la facture CUR 2.0 et le rapprochement.

Le rapprochement du §8.2 doit sortir « conforme » : l'écart entre le coût
recalculé depuis les journaux et le montant facturé reste sous 3 %.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from demo.cur import facture_par_jour, generer_lignes_cur, lignes_en_json
from demo.profils import GRAINE_DEFAUT, TENANT_A
from demo.seed import charger_socle_demo, comptes_demo, construire_agregats
from demo.traffic import generer_trafic

from jauge.engine.attribution import rapprocher_facture
from jauge.engine.calculator import TableTarifs

JOURS_TEST = 3


@pytest.fixture(scope="module")
def contexte() -> dict:
    """Trafic, tarifs et lignes CUR de trois jours (générés une seule fois)."""
    socle = charger_socle_demo()
    table = TableTarifs(socle.tarifs)
    records = generer_trafic(graine=GRAINE_DEFAUT, jours=JOURS_TEST)
    lignes = generer_lignes_cur(records, table, GRAINE_DEFAUT, comptes_demo())
    agregats = construire_agregats(records, socle, table)
    return {
        "socle": socle,
        "table": table,
        "records": records,
        "lignes": lignes,
        "agregats": agregats,
    }


def test_colonnes_cur(contexte: dict) -> None:
    """Les lignes CUR portent les colonnes attendues du CUR 2.0."""
    colonnes = set(contexte["lignes"][0].vers_dictionnaire())
    attendues = {
        "bill_id",
        "payer_account_id",
        "line_item_usage_start_date",
        "line_item_usage_end_date",
        "product_code",
        "product_family",
        "line_item_usage_type",
        "line_item_operation",
        "resource_id",
        "line_item_usage_amount",
        "line_item_unblended_cost",
        "region",
        "service_tier",
        "model_id",
        "tag_application",
        "tag_team",
        "tag_environment",
    }
    assert colonnes == attendues
    premiere = contexte["lignes"][0]
    assert premiere.product_code == "AmazonBedrock"
    assert premiere.line_item_usage_start_date.endswith("Z")
    assert isinstance(json.loads(lignes_en_json(contexte["lignes"])[0]), dict)


def test_facture_par_compte(contexte: dict) -> None:
    """Chaque client a sa propre facture, cloisonnée par compte AWS."""
    facture_a = facture_par_jour(contexte["lignes"], comptes_demo()[TENANT_A])
    assert facture_a, "le client A doit avoir des lignes facturées"
    assert all(montant > 0 for montant in facture_a.values())
    comptes = {ligne.payer_account_id for ligne in contexte["lignes"]}
    assert comptes == set(comptes_demo().values())


def test_ecart_rapprochement_sous_3_pourcent(contexte: dict) -> None:
    """Le rapprochement quotidien du client A reste conforme (écart < 3 %)."""
    agregats = [a for a in contexte["agregats"] if a.tenant_id == TENANT_A]
    recalcul: dict[date, float] = {}
    for agregat in agregats:
        recalcul[agregat.jour] = recalcul.get(agregat.jour, 0.0) + agregat.cout_recalcule_usd
    facture = facture_par_jour(contexte["lignes"], comptes_demo()[TENANT_A])
    assert recalcul, "des agrégats sont attendus"
    for jour, montant in sorted(recalcul.items()):
        rapprochement = rapprocher_facture(
            tenant_id=TENANT_A,
            jour=jour,
            cout_recalcule=montant,
            cout_facture=facture[jour],
        )
        assert rapprochement.conforme, f"écart {rapprochement.ecart_pourcentage} % au {jour}"
        assert rapprochement.ecart_pourcentage < 3.0


def test_volume_facture_suit_le_trafic(contexte: dict) -> None:
    """Les quantités facturées correspondent aux jetons du trafic."""
    total_trafic = sum(r.jetons_entree for r in contexte["records"] if r.tenant_id == TENANT_A)
    total_facture = sum(
        ligne.line_item_usage_amount
        for ligne in contexte["lignes"]
        if ligne.payer_account_id == comptes_demo()[TENANT_A]
        and "Input Tokens" in ligne.line_item_usage_type
    )
    assert total_facture == total_trafic


def test_cur_deterministe(contexte: dict) -> None:
    """Deux générations avec la même graine donnent les mêmes lignes."""
    autres = generer_lignes_cur(
        contexte["records"], contexte["table"], GRAINE_DEFAUT, comptes_demo()
    )
    assert [ligne.vers_dictionnaire() for ligne in autres] == [
        ligne.vers_dictionnaire() for ligne in contexte["lignes"]
    ]
