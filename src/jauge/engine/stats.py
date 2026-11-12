"""Statistiques déterministes : intervalles de confiance, percentiles, décision.

Utilisé par l'évaluation (EF-EVA-03), la règle de décision (§13.4) et les
rapports. Aucun LLM n'intervient ici (ENF-IA-01).

L'intervalle de confiance retenu est l'intervalle de Wilson : il reste stable
pour les petits échantillons et les taux proches de 1, contrairement à
l'approximation normale — le cas typique d'un candidat à 97 % sur 200 requêtes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from jauge.common.errors import ValidationError

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


def moyenne_ponderee(valeurs: Sequence[float], poids: Sequence[float]) -> float | None:
    """Moyenne pondérée : Σ(valeurᵢ × poidsᵢ) / Σ(poidsᵢ).

    Sert de socle à la qualité pondérée par la dépense (Top 10 n° 4) : avec
    `valeurs` = indicateurs par groupe (ex. taux d'équivalence d'une strate) et
    `poids` = dépense de chaque groupe, une strate qui pèse 40 % de la facture
    compte 40 % dans le résultat.

    Rend `None` quand le poids total est nul (pondération non calculable :
    aucune donnée de dépense) — jamais 0.0, qui serait un chiffre inventé.
    Les poids négatifs sont refusés : une dépense ne peut pas être négative.
    """
    if len(valeurs) != len(poids):
        raise ValidationError(
            "valeurs et poids doivent avoir la même longueur",
            valeurs=len(valeurs),
            poids=len(poids),
        )
    if any(p < 0 for p in poids):
        raise ValidationError("un poids négatif n'est pas admissible")
    total_poids = sum(poids)
    if total_poids <= 0:
        return None
    return sum(v * p for v, p in zip(valeurs, poids, strict=True)) / total_poids


def effectif_effectif(poids: Sequence[float]) -> float:
    """Effectif effectif de Kish d'un échantillon pondéré : (Σw)² / Σw².

    C'est le nombre de tirages **équi-probables** qui porte la même précision
    que l'échantillon pondéré. Il vaut exactement `n` quand tous les poids sont
    égaux (pondération par le volume), et chute dès qu'un petit nombre de
    requêtes concentre la dépense — c'est ce qui rend honnête l'intervalle de
    confiance d'un taux pondéré par le coût.
    """
    if any(p < 0 for p in poids):
        raise ValidationError("un poids négatif n'est pas admissible")
    total = sum(poids)
    carres = sum(p * p for p in poids)
    if total <= 0 or carres <= 0:
        return 0.0
    return (total * total) / carres


def intervalle_wilson_pondere(
    succes_pondere: float, poids: Sequence[float], z: float = Z_95
) -> IntervalleTaux:
    """Intervalle de Wilson d'une proportion **pondérée** (Top 10 n° 4).

    `succes_pondere` est Σ(wᵢ × xᵢ) où xᵢ ∈ {0, 1} est le verdict de la requête i
    et wᵢ son poids (dépense, volume, ou 1/n_strate pour l'équipondération).
    Le taux est p = Σ(wᵢxᵢ) / Σwᵢ et l'intervalle utilise l'effectif effectif de
    Kish (`effectif_effectif`) comme taille d'échantillon.

    Cohérence garantie : avec des poids tous égaux, l'effectif effectif vaut n et
    le résultat est **identique** à `intervalle_wilson(succes, n)` — vérifié par
    `tests/engine/test_stats_ponderation.py`. La formule de Wilson est la même
    que dans `intervalle_wilson`, généralisée à un effectif non entier.
    """
    total = sum(poids)
    n = effectif_effectif(poids)
    if total <= 0 or n <= 0:
        return IntervalleTaux(taux=0.0, bas=0.0, haut=0.0, n=0)
    p = succes_pondere / total
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    marge = (z / denom) * math.sqrt(max(0.0, p * (1 - p) / n + z2 / (4 * n * n)))
    return IntervalleTaux(
        taux=round(p, 6),
        bas=round(max(0.0, centre - marge), 6),
        haut=round(min(1.0, centre + marge), 6),
        n=int(round(n)),
    )
