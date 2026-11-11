"""Tests des formules d'économie estimée (EF-LEV-06, Annexe A).

Chaque valeur attendue est calculée **à la main** dans le test, à partir des
tarifs du socle (grand modèle, eu-west-1, Standard) et d'un volume de
3 000 requêtes/mois. Ces nombres ne viennent d'aucun modèle de langage
(ENF-IA-01).
"""

from __future__ import annotations

import pytest
from tests.levers.conftest import HEURES_NUIT, HEURES_NUIT_NON_INTERACTIVES

from jauge.levers import estimate

PREFIXE = {
    "part_prefixe_long": 0.8,
    "longueur_prefixe_long": 2048,
    "factor_reutilisation_prefixe": 8.0,
}
TACHE_SIMPLE = {"requetes_par_type_tache": {"classification": 2400, "extraction": 600}}
NUIT = {
    "requetes_par_heure": HEURES_NUIT,
    "requetes_non_interactive_par_heure": HEURES_NUIT_NON_INTERACTIVES,
}
SORTIES_LONGUES = {
    "longueur_sortie_mediane": 50,
    "longueur_sortie_p95": 3900,
    "part_requetes_sortie_longue": 0.25,
    "limite_sortie_modele": 4096,
}
PRIORITY = {
    "requetes_par_niveau": {"priority": 3000},
    "requetes_non_interactive_par_niveau": {"priority": 2850},
}


def test_cout_actuel_par_requete(contexte) -> None:
    """Coût moyen observé = (200 × 0,015 + 50 × 0,075) / 1 000 = 0,006750 $."""
    assert estimate.cout_actuel_par_requete(contexte) == pytest.approx(0.00675, abs=1e-9)


def test_economie_modele_surdimensionne(fabriquer_contexte) -> None:
    """(0,006750 − 0,000360) × 3 000 = 19,17 $/mois."""
    contexte = fabriquer_contexte(profil=TACHE_SIMPLE)

    assert estimate.estimer_modele_surdimensionne(contexte) == pytest.approx(19.17, abs=0.02)


def test_economie_cache_prompts(fabriquer_contexte) -> None:
    """0,8 × (0,030720 − 0,007488) × 3 000 = 55,76 $/mois."""
    contexte = fabriquer_contexte(profil=PREFIXE)

    assert estimate.estimer_cache_prompts(contexte) == pytest.approx(55.76, abs=0.05)


def test_economie_traitement_differable(fabriquer_contexte) -> None:
    """(0,006750 − 0,003375) × 2 700 = 9,11 $/mois."""
    contexte = fabriquer_contexte(profil=NUIT)

    assert estimate.estimer_traitement_differable(contexte) == pytest.approx(9.11, abs=0.02)


def test_economie_sorties_trop_longues(fabriquer_contexte) -> None:
    """0,25 × 3 850 × 0,075/1k × 3 000 = 216,56 $/mois."""
    contexte = fabriquer_contexte(profil=SORTIES_LONGUES)

    assert estimate.estimer_sorties_trop_longues(contexte) == pytest.approx(216.56, abs=0.05)


def test_economie_requetes_repetees(fabriquer_contexte) -> None:
    """0,3 × 0,006750 × 3 000 = 6,08 $/mois."""
    contexte = fabriquer_contexte(profil={"part_requetes_identiques": 0.3})

    assert estimate.estimer_requetes_repetees(contexte) == pytest.approx(6.08, abs=0.02)


def test_economie_erreurs_et_relances(fabriquer_contexte) -> None:
    """(60 / 30 × 30) relances × 0,006750 $ = 0,41 $/mois."""
    contexte = fabriquer_contexte(
        profil={"nombre_erreurs": 120, "nombre_relances_apres_erreur": 60}
    )

    assert estimate.estimer_erreurs_et_relances(contexte) == pytest.approx(0.41, abs=0.01)


def test_economie_priority_sans_besoin(fabriquer_contexte) -> None:
    """(0,013500 − 0,006750) × 2 850 = 19,24 $/mois."""
    contexte = fabriquer_contexte(profil=PRIORITY)

    assert estimate.estimer_priority_sans_besoin(contexte) == pytest.approx(19.24, abs=0.02)


def test_estimations_nulles_sans_signal(contexte) -> None:
    """Profil neutre : toutes les estimations valent zéro, aucune n'est inventée."""
    assert estimate.estimer_modele_surdimensionne(contexte) == 0.0
    assert estimate.estimer_cache_prompts(contexte) == 0.0
    assert estimate.estimer_traitement_differable(contexte) == 0.0
    assert estimate.estimer_sorties_trop_longues(contexte) == 0.0
    assert estimate.estimer_requetes_repetees(contexte) == 0.0
    assert estimate.estimer_erreurs_et_relances(contexte) == 0.0
    assert estimate.estimer_priority_sans_besoin(contexte) == 0.0


def test_estimations_nulles_sans_tarif(fabriquer_contexte) -> None:
    """Modèle non tarifé : aucune estimation ne peut être produite."""
    contexte = fabriquer_contexte(
        profil={**PREFIXE, **TACHE_SIMPLE, **NUIT, **SORTIES_LONGUES, **PRIORITY},
        modele_actuel="modele-hors-table",
    )

    assert estimate.estimer_cache_prompts(contexte) == 0.0
    assert estimate.estimer_traitement_differable(contexte) == 0.0
    assert estimate.estimer_sorties_trop_longues(contexte) == 0.0
    assert estimate.estimer_priority_sans_besoin(contexte) == 0.0


def test_volume_mensuel_extrapole(contexte) -> None:
    """Sans volume paramétré, 3 000 requêtes sur 30 jours → 3 000 requêtes/mois."""
    from dataclasses import replace

    contexte_sans_volume = replace(
        contexte, parametres=replace(contexte.parametres, volume_mensuel_requetes=0)
    )

    assert estimate.volume_mensuel(contexte_sans_volume) == 3000


def test_candidats_autorises_excluent_le_modele_courant(contexte) -> None:
    """Les candidats viennent des modèles autorisés, hors modèle courant."""
    candidats = estimate.candidats_autorises(contexte)

    assert contexte.modele_actuel not in candidats
    assert set(candidats) <= set(contexte.modeles_autorises)
