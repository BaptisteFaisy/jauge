"""Tests de l'horloge métier unique (`jauge.common.temps`).

Deux propriétés à tenir : par défaut le domaine voit la date réelle (aucune
régression du comportement de production), et une date figée s'applique à
**tout** le domaine — sinon deux points d'API divergent sur « aujourd'hui ».
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jauge.common.temps import aujourdhui, figer_date

AUTRE_JOUR = date(2030, 1, 1)


@pytest.fixture(autouse=True)
def horloge_retablie() -> None:
    """Rend la date réelle après chaque test, quoi qu'il arrive."""
    yield
    figer_date(None)


def test_sans_figement_c_est_la_date_reelle() -> None:
    """Le plan de contrôle en production n'appelle jamais `figer_date`."""
    assert aujourdhui() == date.today()


def test_le_figement_s_applique() -> None:
    """Une date figée est rendue telle quelle, sans calcul."""
    figer_date(AUTRE_JOUR)
    assert aujourdhui() == AUTRE_JOUR


def test_figement_a_none_retablit_la_date_reelle() -> None:
    """`None` lève le figement : pas de fuite de date dans le reste du process."""
    figer_date(AUTRE_JOUR)
    figer_date(None)
    assert aujourdhui() == date.today()


def test_le_figement_est_global_aux_deux_appels() -> None:
    """Deux lectures dans le même figement rendent la même date (état unique)."""
    figer_date(AUTRE_JOUR)
    assert aujourdhui() == aujourdhui() == AUTRE_JOUR


def test_le_domaine_n_appelle_plus_jamais_date_today_directement() -> None:
    """Garde structurelle : `date.today()` ne vit que dans l'horloge.

    La dérive calendaire de l'instantané du portail venait de points d'API
    calés chacun sur leur propre `date.today()` : un figement partiel ne protège
    pas. Ce test refuse la résurgence d'un second compteur de dates.
    """
    source = Path(__file__).resolve().parents[2] / "src" / "jauge"
    horloge = source / "common" / "temps.py"
    fautifs = [
        fichier.relative_to(source)
        for fichier in sorted(source.rglob("*.py"))
        if fichier != horloge and "date.today()" in fichier.read_text(encoding="utf-8")
    ]
    assert not fautifs, (
        "ces fichiers lisent la date système au lieu de jauge.common.temps."
        "aujourdhui() : " + ", ".join(str(f) for f in fautifs)
    )
