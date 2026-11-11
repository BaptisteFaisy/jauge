"""Tests de la règle EF-LEV-02 — cache de prompts absent.

Cas nominal, cas limites (seuil de part, longueur minimale) et absence du
signal (cache déjà actif).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from jauge.levers.cache import RegleCachePromptsAbsent
from jauge.models.lever import TypeLevier

PREFIXE = {
    "part_prefixe_long": 0.8,
    "longueur_prefixe_long": 2048,
    "factor_reutilisation_prefixe": 8.0,
}


def test_detecte_un_cache_de_prompts_absent(fabriquer_contexte) -> None:
    """Préfixe de 2 048 jetons sur 80 % des requêtes, aucun jeton lu en cache."""
    contexte = fabriquer_contexte(profil=PREFIXE)

    levier = RegleCachePromptsAbsent().detecter(contexte)

    assert levier is not None
    assert levier.type_levier is TypeLevier.CACHE_PROMPTS_ABSENT
    assert levier.action_proposee == "Activer le cache de prompts"
    assert levier.preuve_exigee == "Calcul sur les jetons"
    assert any("2 048" in indice for indice in levier.indices)
    assert levier.candidats_suggeres == []


def test_economie_du_cache_calculee_a_la_main(fabriquer_contexte) -> None:
    """Économie du cache sur les tarifs du socle (grand modèle, Standard).

    préfixe P = 2 048 jetons, R = 8 requêtes par entrée de cache
    sans cache = 2 048 × 0,015/1k                      = 0,030720 $/requête
    avec cache = (2 048/8) × 0,01875/1k                = 0,004800 $  (écriture)
               + 2 048 × 7/8 × 0,0015/1k               = 0,002688 $  (lecture)
                                                       = 0,007488 $
    gain par requête = 0,030720 − 0,007488 = 0,023232 $
    économie = 0,8 × 0,023232 × 3 000 requêtes = 55,76 $/mois
    """
    contexte = fabriquer_contexte(profil=PREFIXE)

    levier = RegleCachePromptsAbsent().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(55.76, abs=0.05)


def test_pas_de_levier_sous_le_seuil_de_part(fabriquer_contexte) -> None:
    """Cas limite : le préfixe ne couvre que 49 % des requêtes."""
    contexte = fabriquer_contexte(profil={**PREFIXE, "part_prefixe_long": 0.49})

    assert RegleCachePromptsAbsent().detecter(contexte) is None


def test_pas_de_levier_si_prefixe_trop_court(fabriquer_contexte) -> None:
    """Cas limite : préfixe de 1 023 jetons, sous le minimum de 1 024."""
    contexte = fabriquer_contexte(profil={**PREFIXE, "longueur_prefixe_long": 1023})

    assert RegleCachePromptsAbsent().detecter(contexte) is None


def test_pas_de_levier_si_le_cache_est_deja_actif(fabriquer_contexte) -> None:
    """Absence du signal : des jetons sont déjà lus en cache."""
    contexte = fabriquer_contexte(profil=PREFIXE)
    agregats = tuple(
        agregat.model_copy(update={"jetons_cache_lecture_total": 2048})
        for agregat in contexte.agregats
    )
    contexte_avec_cache = replace(contexte, agregats=agregats)

    assert RegleCachePromptsAbsent().detecter(contexte_avec_cache) is None


def test_pas_de_levier_sans_prefixe_commun(fabriquer_contexte) -> None:
    """Absence du signal : aucun préfixe partagé dans le profil."""
    assert RegleCachePromptsAbsent().detecter(fabriquer_contexte()) is None
