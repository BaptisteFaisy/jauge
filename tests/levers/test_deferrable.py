"""Tests de la règle EF-LEV-03 — traitement différable resté au niveau Standard.

Cas nominal (trafic nocturne non interactif), cas limites (seuil de trafic non
interactif, trafic déjà en Flex) et absence du signal.
"""

from __future__ import annotations

import pytest
from tests.levers.conftest import HEURES_JOUR, HEURES_NUIT, HEURES_NUIT_NON_INTERACTIVES

from jauge.levers.deferrable import RegleTraitementDifferable
from jauge.models.lever import TypeLevier

NUIT = {
    "requetes_par_heure": HEURES_NUIT,
    "requetes_non_interactive_par_heure": HEURES_NUIT_NON_INTERACTIVES,
}


def test_detecte_un_traitement_differable(fabriquer_contexte) -> None:
    """Trafic nocturne non interactif encore facturé en Standard."""
    contexte = fabriquer_contexte(profil=NUIT)

    levier = RegleTraitementDifferable().detecter(contexte)

    assert levier is not None
    assert levier.type_levier is TypeLevier.TRAITEMENT_DIFFERABLE
    assert levier.action_proposee == "Niveau Flex ou traitement par lots"
    assert levier.preuve_exigee == "Accord du responsable sur le délai"


def test_economie_du_niveau_flex_calculee_a_la_main(fabriquer_contexte) -> None:
    """Économie du passage au niveau Flex sur les tarifs du socle.

    trafic nocturne non interactif = 6 × 450 = 2 700 requêtes sur 3 000 (90 %)
    coût Standard = 200 × 0,015/1k + 50 × 0,075/1k = 0,006750 $/requête
    coût Flex     = 200 × 0,0075/1k + 50 × 0,0375/1k = 0,003375 $/requête
    économie = (0,006750 − 0,003375) × 2 700 requêtes = 9,11 $/mois
    """
    contexte = fabriquer_contexte(profil=NUIT)

    levier = RegleTraitementDifferable().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(9.11, abs=0.02)


def test_pas_de_levier_sous_le_seuil_non_interactif(fabriquer_contexte) -> None:
    """Cas limite : 79 % d'appels non interactifs, sous le seuil de 80 %."""
    non_interactif = tuple(int(valeur * 0.79) for valeur in HEURES_NUIT_NON_INTERACTIVES)
    contexte = fabriquer_contexte(
        profil={
            "requetes_par_heure": HEURES_NUIT,
            "requetes_non_interactive_par_heure": non_interactif,
        }
    )

    assert RegleTraitementDifferable().detecter(contexte) is None


def test_pas_de_levier_si_deja_en_flex(fabriquer_contexte) -> None:
    """Cas limite : le trafic est déjà majoritairement servi au niveau Flex.

    Les appels non interactifs sont bien présents : c'est le niveau de service
    déjà choisi qui empêche la détection, pas l'absence de signal.
    """
    contexte = fabriquer_contexte(
        profil={
            **NUIT,
            "requetes_par_niveau": {"flex": 2400, "standard": 600},
            "requetes_non_interactive_par_niveau": {"flex": 2400, "standard": 0},
        }
    )

    assert RegleTraitementDifferable().detecter(contexte) is None


def test_pas_de_levier_si_trafic_interactif_de_journee(fabriquer_contexte) -> None:
    """Absence du signal : trafic de journée, aucun appel non interactif."""
    contexte = fabriquer_contexte(profil={"requetes_par_heure": HEURES_JOUR})

    assert RegleTraitementDifferable().detecter(contexte) is None


def test_pas_de_levier_sans_tarif_flex(fabriquer_contexte) -> None:
    """Absence du signal : le modèle n'a aucune ligne tarifaire Flex."""
    contexte = fabriquer_contexte(profil=NUIT, modele_actuel="modele-sans-tarif-flex")

    assert RegleTraitementDifferable().detecter(contexte) is None
