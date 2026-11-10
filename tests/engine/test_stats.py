"""Tests des statistiques déterministes (EF-EVA-03, §13.4)."""

from __future__ import annotations

import pytest

from jauge.engine.stats import (
    baisse_pourcentage,
    economie_mensuelle,
    intervalle_wilson,
    percentile,
    taille_echantillon_minimale,
)


def test_wilson_cas_du_cahier_des_charges() -> None:
    """195/200 équivalents → borne basse au-dessus de 90 % (§13.4, Annexe B)."""
    resultat = intervalle_wilson(195, 200)
    assert resultat.taux == pytest.approx(0.975, abs=1e-3)
    assert resultat.bas == pytest.approx(0.943, abs=0.005)
    assert resultat.bas > 0.90


def test_wilson_petit_echantillon() -> None:
    """Sur 10 essais, l'intervalle reste large : la décision doit en tenir compte."""
    resultat = intervalle_wilson(9, 10)
    assert resultat.bas < 0.75
    assert resultat.haut > 0.95


def test_wilson_cas_limites() -> None:
    """n=0 ne divise pas par zéro ; 0/20 et 20/20 restent dans [0, 1]."""
    assert intervalle_wilson(0, 0).taux == 0.0
    assert intervalle_wilson(0, 20).bas == 0.0
    assert intervalle_wilson(20, 20).haut == 1.0


def test_percentile() -> None:
    """p50 et p95 sur une série simple (EF-REJ-04)."""
    serie = list(range(1, 101))
    assert percentile(serie, 50) == pytest.approx(50.5)
    assert percentile(serie, 95) == pytest.approx(95.05)
    assert percentile([], 50) == 0.0
    assert percentile([42], 95) == 42.0


def test_taille_echantillon_200() -> None:
    """200 requêtes ≈ ±3 % à 95 % de confiance : la taille par défaut se justifie."""
    n = taille_echantillon_minimale(0.95, marge=0.03)
    assert 150 <= n <= 250


def test_economie_et_baisse() -> None:
    """Économie mensuelle et baisse relative (§4, indicateur principal)."""
    assert economie_mensuelle(0.0041, 0.0009, 310_000) == pytest.approx(992.0)
    assert baisse_pourcentage(0.0041, 0.0009) == pytest.approx(78.05, abs=0.01)
    assert baisse_pourcentage(0.0, 1.0) == 0.0
