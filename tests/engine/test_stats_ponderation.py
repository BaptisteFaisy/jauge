"""Tests des statistiques pondérées (Top 10 n° 4 — qualité pondérée par le coût).

Vérifie `moyenne_ponderee`, `effectif_effectif` (Kish) et
`intervalle_wilson_pondere` ajoutés à `jauge.engine.stats`. La propriété clé :
à poids égaux, le Wilson pondéré retombe **exactement** sur `intervalle_wilson`
— la pondération par le volume ne doit pas faire dériver la lecture du §13.4.
"""

from __future__ import annotations

import pytest

from jauge.common.errors import ValidationError
from jauge.engine.stats import (
    effectif_effectif,
    intervalle_wilson,
    intervalle_wilson_pondere,
    moyenne_ponderee,
)


def test_moyenne_ponderee_calculee_a_la_main() -> None:
    """(1,0 × 10 + 0,5 × 90) / 100 = 0,55 : la dépense pilote le résultat."""
    assert moyenne_ponderee([1.0, 0.5], [10.0, 90.0]) == pytest.approx(0.55)


def test_moyenne_ponderee_poids_egaux_vaut_moyenne_simple() -> None:
    """À poids égaux, la moyenne pondérée est la moyenne arithmétique."""
    assert moyenne_ponderee([0.9, 0.8, 0.7], [1.0, 1.0, 1.0]) == pytest.approx(0.8)


def test_moyenne_ponderee_sans_matiere_rend_none() -> None:
    """Poids total nul : `None`, jamais 0.0 (aucun chiffre inventé, ENF-IA-01)."""
    assert moyenne_ponderee([0.9, 0.8], [0.0, 0.0]) is None
    assert moyenne_ponderee([], []) is None


def test_moyenne_ponderee_entrees_invalides() -> None:
    """Longueurs différentes ou poids négatif : refus explicite."""
    with pytest.raises(ValidationError):
        moyenne_ponderee([1.0, 2.0], [1.0])
    with pytest.raises(ValidationError):
        moyenne_ponderee([1.0, 2.0], [1.0, -2.0])


def test_effectif_effectif_poids_egaux() -> None:
    """Poids tous égaux : l'effectif effectif de Kish vaut n exactement."""
    assert effectif_effectif([1.0] * 200) == pytest.approx(200.0)


def test_effectif_effectif_depense_concentree() -> None:
    """Une dépense concentrée réduit l'effectif effectif : (Σw)²/Σw²."""
    # 2 requêtes : poids 1 et 99 → 100² / (1 + 9801) ≈ 1,02
    assert effectif_effectif([1.0, 99.0]) == pytest.approx(10000.0 / 9802.0)


def test_effectif_effectif_cas_limites() -> None:
    """Liste vide ou poids nuls : 0.0 ; poids négatif : refus."""
    assert effectif_effectif([]) == 0.0
    assert effectif_effectif([0.0, 0.0]) == 0.0
    with pytest.raises(ValidationError):
        effectif_effectif([1.0, -1.0])


def test_wilson_pondere_poids_egaux_identique_a_l_original() -> None:
    """Propriété de cohérence : poids égaux ⇒ exactement `intervalle_wilson`."""
    pondere = intervalle_wilson_pondere(195.0, [1.0] * 200)
    original = intervalle_wilson(195, 200)
    assert pondere.taux == original.taux
    assert pondere.bas == original.bas
    assert pondere.haut == original.haut
    assert pondere.n == original.n


def test_wilson_pondere_cas_manuel() -> None:
    """5 succès de poids 1, 5 échecs de poids 9 : taux = 5/50 = 0,1."""
    poids = [1.0] * 5 + [9.0] * 5
    resultat = intervalle_wilson_pondere(5.0, poids)
    assert resultat.taux == pytest.approx(0.1)
    # Effectif effectif : 50² / (5×1 + 5×81) = 2500/410 ≈ 6,1
    assert resultat.n == round(2500.0 / 410.0)
    assert 0.0 <= resultat.bas <= resultat.taux <= resultat.haut <= 1.0


def test_wilson_pondere_sans_poids_rend_intervalle_vide() -> None:
    """Poids total nul : intervalle vide (0, 0, 0), pas de division par zéro."""
    resultat = intervalle_wilson_pondere(0.0, [0.0, 0.0])
    assert (resultat.taux, resultat.bas, resultat.haut, resultat.n) == (0.0, 0.0, 0.0, 0)


def test_wilson_pondere_bornes_dans_zero_un() -> None:
    """Bornes toujours dans [0, 1], même à taux extrêmes et poids hétérogènes."""
    for succes, poids in [
        (0.0, [3.0, 7.0]),
        (10.0, [3.0, 7.0]),
        (0.3, [0.3]),
        (12.0, [12.0]),
    ]:
        resultat = intervalle_wilson_pondere(succes, poids)
        assert 0.0 <= resultat.bas <= resultat.haut <= 1.0
