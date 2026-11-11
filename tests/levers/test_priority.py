"""Tests de la règle EF-LEV-05 (S) — niveau Priority sans besoin.

Cas nominal, cas limites (part du trafic Priority, part non interactive) et
absence du signal.
"""

from __future__ import annotations

import pytest

from jauge.levers.priority import ReglePrioritySansBesoin
from jauge.models.lever import TypeLevier

PRIORITY = {
    "requetes_par_niveau": {"priority": 3000},
    "requetes_non_interactive_par_niveau": {"priority": 2850},
}


def test_detecte_un_priority_sans_besoin(fabriquer_contexte) -> None:
    """Tout le trafic est en Priority et 95 % de ces appels n'attendent personne."""
    contexte = fabriquer_contexte(profil=PRIORITY)

    levier = ReglePrioritySansBesoin().detecter(contexte)

    assert levier is not None
    assert levier.type_levier is TypeLevier.PRIORITY_SANS_BESOIN
    assert levier.action_proposee == "Niveau Standard"
    assert levier.preuve_exigee == "Accord du responsable"


def test_economie_du_retour_en_standard_calculee_a_la_main(fabriquer_contexte) -> None:
    """Économie du retour au niveau Standard.

    coût Priority = 200 × 0,03/1k + 50 × 0,15/1k = 0,013500 $/requête
    coût Standard = 200 × 0,015/1k + 50 × 0,075/1k = 0,006750 $/requête
    volume éligible = 3 000 × 100 % × 95 % = 2 850 requêtes
    économie = (0,013500 − 0,006750) × 2 850 = 19,24 $/mois
    """
    contexte = fabriquer_contexte(profil=PRIORITY)

    levier = ReglePrioritySansBesoin().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(19.24, abs=0.02)


def test_pas_de_levier_sous_le_seuil_priority(fabriquer_contexte) -> None:
    """Cas limite : 10 % du trafic en Priority, sous le seuil de 20 %."""
    contexte = fabriquer_contexte(
        profil={
            "requetes_par_niveau": {"priority": 300, "standard": 2700},
            "requetes_non_interactive_par_niveau": {"priority": 300},
        }
    )

    assert ReglePrioritySansBesoin().detecter(contexte) is None


def test_pas_de_levier_si_appels_interactifs(fabriquer_contexte) -> None:
    """Cas limite : trafic Priority majoritairement interactif (50 %)."""
    contexte = fabriquer_contexte(
        profil={
            "requetes_par_niveau": {"priority": 3000},
            "requetes_non_interactive_par_niveau": {"priority": 1500},
        }
    )

    assert ReglePrioritySansBesoin().detecter(contexte) is None


def test_pas_de_levier_sans_priority(fabriquer_contexte) -> None:
    """Absence du signal : tout le trafic est en Standard."""
    assert ReglePrioritySansBesoin().detecter(fabriquer_contexte()) is None


def test_pas_de_levier_sans_tarif_priority(fabriquer_contexte) -> None:
    """Absence du signal : le modèle n'a aucune ligne tarifaire Priority."""
    contexte = fabriquer_contexte(profil=PRIORITY, modele_actuel="modele-sans-tarif-priority")

    assert ReglePrioritySansBesoin().detecter(contexte) is None
