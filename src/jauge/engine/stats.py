"""Statistiques déterministes : intervalles de confiance, percentiles, décision.

Utilisé par l'évaluation (EF-EVA-03), la règle de décision (§13.4) et les
rapports. Aucun LLM n'intervient ici (ENF-IA-01).

L'intervalle de confiance retenu est l'intervalle de Wilson : il reste stable
pour les petits échantillons et les taux proches de 1, contrairement à
l'approximation normale — le cas typique d'un candidat à 97 % sur 200 requêtes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# z pour un intervalle bilatéral à 95 % (table normale, valeur usuelle).
Z_95 = 1.959963984540054


@dataclass(frozen=True)
class IntervalleTaux:
    """Taux observé et bornes de confiance (Wilson)."""

    taux: float
    bas: float
    haut: float
    n: int


def intervalle_wilson(succes: int, n: int, z: float = Z_95) -> IntervalleTaux:
    """Intervalle de Wilson pour une proportion observée sur `n` essais."""
    if n <= 0:
        return IntervalleTaux(taux=0.0, bas=0.0, haut=0.0, n=0)
    p = succes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    marge = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return IntervalleTaux(
        taux=round(p, 6),
        bas=round(max(0.0, centre - marge), 6),
        haut=round(min(1.0, centre + marge), 6),
        n=n,
    )


def percentile(valeurs: list[float] | list[int], p: float) -> float:
    """Percentile par interpolation linéaire (p en %, 0–100)."""
    if not valeurs:
        return 0.0
    trie = sorted(valeurs)
    if len(trie) == 1:
        return float(trie[0])
    rang = (len(trie) - 1) * (p / 100.0)
    bas = math.floor(rang)
    haut = math.ceil(rang)
    if bas == haut:
        return float(trie[int(rang)])
    return float(trie[bas] + (trie[haut] - trie[bas]) * (rang - bas))


def taille_echantillon_minimale(
    taux_cible: float, marge: float = 0.03, confiance: float = Z_95
) -> int:
    """Taille d'échantillon pour estimer un taux à ±`marge` près.

    Sert à justifier la taille de 200 requêtes (§13.1) et à dimensionner une
    campagne plus large quand le responsable la demande (§8.5).
    """
    if marge <= 0:
        return 0
    p = min(max(taux_cible, 0.01), 0.99)
    n = (confiance**2 * p * (1 - p)) / (marge**2)
    return max(1, math.ceil(n))


def economie_mensuelle(
    cout_par_requete_avant: float, cout_par_requete_apres: float, volume_mensuel: int
) -> float:
    """Économie mensuelle attendue (§4 : à volume constant)."""
    return round((cout_par_requete_avant - cout_par_requete_apres) * volume_mensuel, 2)


def baisse_pourcentage(avant: float, apres: float) -> float:
    """Baisse relative en % (indicateur principal du §4)."""
    if avant <= 0:
        return 0.0
    return round((avant - apres) / avant * 100.0, 2)
