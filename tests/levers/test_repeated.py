"""Tests de la règle EF-LEV-05 (S) — requêtes identiques répétées.

Cas nominal, cas limite (seuil de part) et absence du signal.
"""

from __future__ import annotations

import pytest

from jauge.levers.repeated import RegleRequetesRepetees
from jauge.models.lever import TypeLevier


def test_detecte_des_requetes_identiques(fabriquer_contexte) -> None:
    """30 % du volume est strictement identique à une requête antérieure."""
    contexte = fabriquer_contexte(profil={"part_requetes_identiques": 0.3})

    levier = RegleRequetesRepetees().detecter(contexte)

    assert levier is not None
    assert levier.type_levier is TypeLevier.REQUETES_REPETEES
    assert levier.action_proposee == "Cache applicatif"
    assert levier.preuve_exigee == "Comptage"


def test_economie_du_cache_applicatif_calculee_a_la_main(fabriquer_contexte) -> None:
    """Économie d'un cache applicatif sur les tarifs du socle.

    coût moyen observé = 0,006750 $/requête
    gain par requête   = 30 % × 0,006750 = 0,002025 $
    économie = 0,002025 × 3 000 requêtes = 6,08 $/mois
    """
    contexte = fabriquer_contexte(profil={"part_requetes_identiques": 0.3})

    levier = RegleRequetesRepetees().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(6.08, abs=0.02)


def test_pas_de_levier_sous_le_seuil(fabriquer_contexte) -> None:
    """Cas limite : 19 % de requêtes identiques, sous le seuil de 20 %."""
    contexte = fabriquer_contexte(profil={"part_requetes_identiques": 0.19})

    assert RegleRequetesRepetees().detecter(contexte) is None


def test_pas_de_levier_sans_requete_identique(fabriquer_contexte) -> None:
    """Absence du signal : toutes les requêtes sont distinctes."""
    assert RegleRequetesRepetees().detecter(fabriquer_contexte()) is None


def test_pas_de_levier_sans_cout_observe(fabriquer_contexte) -> None:
    """Absence du signal : aucun agrégat, donc aucun coût à éviter."""
    contexte = fabriquer_contexte(profil={"part_requetes_identiques": 0.3}, agregats=())

    assert RegleRequetesRepetees().detecter(contexte) is None
