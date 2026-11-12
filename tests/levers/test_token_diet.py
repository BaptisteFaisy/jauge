"""Tests de la règle « diète de jetons » — gaspillage de jetons d'entrée.

Cas nominal (prompt système long et répété), cas limites (prompt déjà sobre,
préfixe marginal), économie vérifiée **à la main**, requalification en jetons
de cache lus, classement avec les règles existantes et absence de collision
d'identifiant avec la règle EF-LEV-04 (type de repli).
"""

from __future__ import annotations

import pytest
from tests.levers.conftest import VOLUME_MENSUEL

from jauge.levers.base import Regle
from jauge.levers.cache import RegleCachePromptsAbsent
from jauge.levers.detector import DetectorLeviers, classer_par_economie
from jauge.levers.long_outputs import RegleSortiesTropLongues
from jauge.levers.token_diet import (
    ACTION_PROPOSEE,
    MARQUEUR_LOCAL,
    PART_COMPRESSIBLE,
    PREUVE_EXIGEE,
    SEUIL_LONGUEUR_PREFIXE,
    SEUIL_PART_PREFIXE,
    RegleDieteJetons,
    estimer_diet_jetons,
)
from jauge.models.cost import AgregatQuotidien
from jauge.models.lever import TypeLevier

# Prompt système long (2 048 jetons) répété à l'identique sur 80 % des appels,
# pour une entrée moyenne de 2 400 jetons : le préfixe en occupe 85 %.
PROMPT_GONFLE = {
    "longueur_entree_moyenne": 2400.0,
    "longueur_entree_p95": 2600,
    "part_prefixe_long": 0.8,
    "longueur_prefixe_long": 2048,
    "factor_reutilisation_prefixe": 8.0,
}


def test_la_regle_respecte_le_protocole_des_regles() -> None:
    """La diète de jetons est une règle au sens du protocole `Regle`."""
    assert isinstance(RegleDieteJetons(), Regle)


def test_detecte_un_prompt_systeme_gonfle(fabriquer_contexte) -> None:
    """Préfixe de 2 048 jetons sur 80 % des requêtes, 85 % de l'entrée moyenne."""
    contexte = fabriquer_contexte(profil=PROMPT_GONFLE)

    levier = RegleDieteJetons().detecter(contexte)

    assert levier is not None
    assert levier.action_proposee == ACTION_PROPOSEE
    assert levier.preuve_exigee == PREUVE_EXIGEE
    assert levier.preuve_exigee == "Campagne de rejeu sur variantes de prompt"
    assert any("2 048" in indice for indice in levier.indices)
    assert any("80 %" in indice for indice in levier.indices)
    assert levier.id.startswith("lv-")


def test_economie_estimee_verifiee_a_la_main(fabriquer_contexte) -> None:
    """Économie calculée à la main sur les tarifs du socle (grand modèle).

    jetons évités = 2 048 × 0,35 × 0,8               = 573,44 jetons/requête
    gain/requête  = 573,44 × 0,015 $/1k ÷ 1 000      = 0,0086016 $
    économie/mois = 0,0086016 × 3 000 requêtes       = 25,80 $
    """
    contexte = fabriquer_contexte(profil=PROMPT_GONFLE)

    levier = RegleDieteJetons().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(25.80, abs=0.01)
    assert estimer_diet_jetons(contexte) == pytest.approx(25.80, abs=0.01)


def test_economie_requalifiee_en_jetons_de_cache_lus(fabriquer_contexte, agregats_30j) -> None:
    """Cache actif : les jetons évités étaient facturés au prix du cache.

    prix applicable = 0,01875/8 + 0,0015 × 7/8        = 0,00365625 $/1k
    gain/requête    = 573,44 × 0,00365625 ÷ 1 000     = 0,0020967 $
    économie/mois   = 0,0020967 × 3 000               = 6,29 $
    """
    avec_cache = tuple(
        agregat.model_copy(update={"jetons_cache_lecture_total": 100_000})
        for agregat in agregats_30j
    )
    contexte = fabriquer_contexte(profil=PROMPT_GONFLE, agregats=avec_cache)

    economie = estimer_diet_jetons(contexte)

    assert economie == pytest.approx(6.29, abs=0.01)
    assert isinstance(avec_cache[0], AgregatQuotidien)


def test_aucun_levier_sur_un_prompt_deja_sobre(fabriquer_contexte) -> None:
    """Prompt sobre : préfixe court (256 jetons) sur 30 % des requêtes."""
    contexte = fabriquer_contexte(
        profil={
            "longueur_entree_moyenne": 900.0,
            "longueur_entree_p95": 1100,
            "part_prefixe_long": 0.3,
            "longueur_prefixe_long": 256,
        }
    )

    assert RegleDieteJetons().detecter(contexte) is None


def test_aucun_levier_si_le_prefixe_est_marginal(fabriquer_contexte) -> None:
    """Cas limite : préfixe long mais noyé dans une entrée bien plus longue."""
    contexte = fabriquer_contexte(profil={**PROMPT_GONFLE, "longueur_entree_moyenne": 9000.0})

    assert RegleDieteJetons().detecter(contexte) is None


@pytest.mark.parametrize(
    ("part", "longueur"),
    [
        (SEUIL_PART_PREFIXE - 0.01, SEUIL_LONGUEUR_PREFIXE),
        (0.9, SEUIL_LONGUEUR_PREFIXE - 1),
    ],
)
def test_seuils_de_detection(fabriquer_contexte, part: float, longueur: int) -> None:
    """Sous le seuil de part ou de longueur, aucun levier n'est produit."""
    contexte = fabriquer_contexte(
        profil={**PROMPT_GONFLE, "part_prefixe_long": part, "longueur_prefixe_long": longueur}
    )

    assert RegleDieteJetons().detecter(contexte) is None


def test_classement_coherent_avec_les_regles_existantes(fabriquer_contexte) -> None:
    """Levier cache (55,76 $) devant la diète (25,80 $) : économie décroissante."""
    contexte = fabriquer_contexte(profil=PROMPT_GONFLE)
    regles = (RegleCachePromptsAbsent(), RegleDieteJetons())

    leviers = DetectorLeviers(regles).detecter(contexte)

    assert len(leviers) == 2
    assert [levier.type_levier for levier in leviers] == [
        TypeLevier.CACHE_PROMPTS_ABSENT,
        RegleDieteJetons.type_levier,
    ]
    economies = [levier.economie_mensuelle_estimee_usd for levier in leviers]
    assert economies == sorted(economies, reverse=True)
    assert classer_par_economie(list(reversed(leviers))) == leviers


def test_identifiant_distinct_de_la_regle_sorties_trop_longues(fabriquer_contexte) -> None:
    """La diète de jetons a son **propre** type de levier (aucune collision).

    Elle était auparavant reclassée « sorties trop longues » faute de valeur dans
    `TypeLevier` ; la valeur `DIETE_JETONS` a été ajoutée au socle, donc les deux
    leviers sont maintenant réellement distincts.
    """
    contexte = fabriquer_contexte(
        profil={
            **PROMPT_GONFLE,
            "longueur_sortie_mediane": 45,
            "longueur_sortie_p95": 3800,
            "part_requetes_sortie_longue": 0.2,
            "limite_sortie_modele": 4096,
        }
    )

    diet = RegleDieteJetons().detecter(contexte)
    sorties = RegleSortiesTropLongues().detecter(contexte)

    assert diet is not None and sorties is not None
    assert diet.type_levier is TypeLevier.DIETE_JETONS
    assert diet.type_levier is not sorties.type_levier
    assert diet.id != sorties.id
    assert diet.id != sorties.id
    assert MARQUEUR_LOCAL == "diete_jetons"
    assert PART_COMPRESSIBLE == 0.35
    assert VOLUME_MENSUEL == 3000
