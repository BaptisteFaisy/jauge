"""Tests de la règle EF-LEV-05 (S) — erreurs suivies de relances.

Cas nominal, cas limite (part des erreurs relancées) et absence du signal.
"""

from __future__ import annotations

import pytest

from jauge.levers.errors_retries import RegleErreursEtRelances
from jauge.models.lever import TypeLevier

ERREURS = {"nombre_erreurs": 120, "nombre_relances_apres_erreur": 60}


def test_detecte_des_erreurs_suivies_de_relances(fabriquer_contexte) -> None:
    """120 erreurs sur 30 jours, dont 60 relancées à l'identique."""
    contexte = fabriquer_contexte(profil=ERREURS)

    levier = RegleErreursEtRelances().detecter(contexte)

    assert levier is not None
    assert levier.type_levier is TypeLevier.ERREURS_ET_RELANCES
    assert levier.action_proposee == "Corriger la cause"
    assert levier.preuve_exigee == "Comptage"


def test_economie_des_relances_calculee_a_la_main(fabriquer_contexte) -> None:
    """Économie de la correction de la cause.

    relances mensuelles = 60 relances / 30 jours × 30 jours = 60
    économie = 60 × 0,006750 $/requête = 0,41 $/mois
    """
    contexte = fabriquer_contexte(profil=ERREURS)

    levier = RegleErreursEtRelances().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(0.41, abs=0.01)


def test_pas_de_levier_sous_le_seuil_de_relance(fabriquer_contexte) -> None:
    """Cas limite : 19 % des erreurs relancées, sous le seuil de 20 %."""
    contexte = fabriquer_contexte(
        profil={"nombre_erreurs": 100, "nombre_relances_apres_erreur": 19}
    )

    assert RegleErreursEtRelances().detecter(contexte) is None


def test_pas_de_levier_sans_erreur(fabriquer_contexte) -> None:
    """Absence du signal : aucune erreur dans la période."""
    assert RegleErreursEtRelances().detecter(fabriquer_contexte()) is None


def test_pas_de_levier_sans_jour_observe(fabriquer_contexte) -> None:
    """Absence du signal : des erreurs mais aucun agrégat pour les rapporter au mois."""
    contexte = fabriquer_contexte(profil=ERREURS, agregats=())

    assert RegleErreursEtRelances().detecter(contexte) is None
