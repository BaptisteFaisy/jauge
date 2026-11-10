"""Moteur de calcul déterministe : coûts, agrégation, statistiques."""

from __future__ import annotations

from jauge.engine.attribution import agreger_requetes, rapprocher_facture
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.engine.stats import (
    IntervalleTaux,
    baisse_pourcentage,
    economie_mensuelle,
    intervalle_wilson,
    percentile,
    taille_echantillon_minimale,
)

__all__ = [
    "IntervalleTaux",
    "TableTarifs",
    "agreger_requetes",
    "baisse_pourcentage",
    "calculer_cout_requete",
    "economie_mensuelle",
    "intervalle_wilson",
    "percentile",
    "rapprocher_facture",
    "taille_echantillon_minimale",
]
