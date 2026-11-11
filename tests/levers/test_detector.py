"""Tests du détecteur : exécution des sept règles et classement (EF-LEV-06)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.conftest import MOYEN_MODELE, PETIT_MODELE
from tests.levers.conftest import HEURES_NUIT, HEURES_NUIT_NON_INTERACTIVES

from jauge.levers import (
    DetectorLeviers,
    Regle,
    RegleCachePromptsAbsent,
    RegleRequetesRepetees,
    detecter_leviers,
)
from jauge.levers.detector import REGLES_DEFAUT, classer_par_economie
from jauge.models.lever import Levier, StatutLevier, TypeLevier

# Profil cumulant cinq signaux : tâche simple, préfixe long non caché, trafic
# nocturne non interactif, requêtes identiques et erreurs relancées.
PROFIL_MIXTE = {
    "requetes_par_type_tache": {"classification": 2400, "extraction": 600},
    "part_sortie_structuree": 1.0,
    "diversite_sorties": 0.02,
    "part_prefixe_long": 0.8,
    "longueur_prefixe_long": 2048,
    "factor_reutilisation_prefixe": 8.0,
    "requetes_par_heure": HEURES_NUIT,
    "requetes_non_interactive_par_heure": HEURES_NUIT_NON_INTERACTIVES,
    "nombre_erreurs": 120,
    "nombre_relances_apres_erreur": 60,
    "part_requetes_identiques": 0.3,
}


def test_sept_regles_par_defaut() -> None:
    """Le détecteur applique les sept règles de l'Annexe A (4 « M » + 3 « S »)."""
    assert len(REGLES_DEFAUT) == 7
    types = {regle.type_levier for regle in REGLES_DEFAUT}
    assert types == {
        TypeLevier.MODELE_SURDIMENSIONNE,
        TypeLevier.CACHE_PROMPTS_ABSENT,
        TypeLevier.TRAITEMENT_DIFFERABLE,
        TypeLevier.SORTIES_TROP_LONGUES,
        TypeLevier.REQUETES_REPETEES,
        TypeLevier.ERREURS_ET_RELANCES,
        TypeLevier.PRIORITY_SANS_BESOIN,
    }


def test_classement_par_economie_decroissante(fabriquer_contexte) -> None:
    """Cinq leviers détectés, classés du plus rentable au moins rentable."""
    leviers = detecter_leviers(fabriquer_contexte(profil=PROFIL_MIXTE))

    assert [levier.type_levier for levier in leviers] == [
        TypeLevier.CACHE_PROMPTS_ABSENT,  # 55,76 $/mois
        TypeLevier.MODELE_SURDIMENSIONNE,  # 19,17 $/mois
        TypeLevier.TRAITEMENT_DIFFERABLE,  #  9,11 $/mois
        TypeLevier.REQUETES_REPETEES,  #  6,08 $/mois
        TypeLevier.ERREURS_ET_RELANCES,  #  0,41 $/mois
    ]
    economies = [levier.economie_mensuelle_estimee_usd for levier in leviers]
    assert economies == sorted(economies, reverse=True)


def test_leviers_complets(fabriquer_contexte) -> None:
    """Chaque levier porte identité, indices, action, preuve, économie et confiance."""
    contexte = fabriquer_contexte(profil=PROFIL_MIXTE)

    for levier in detecter_leviers(contexte):
        assert isinstance(levier, Levier)
        assert levier.id.startswith("lv-")
        assert levier.tenant_id == contexte.tenant_id
        assert levier.application_id == contexte.application_id
        assert levier.statut is StatutLevier.DETECTE
        assert levier.indices
        assert levier.action_proposee
        assert levier.preuve_exigee
        assert levier.economie_mensuelle_estimee_usd >= 0.0
        assert 0.0 <= levier.confiance <= 1.0


def test_candidats_issus_des_modeles_autorises(fabriquer_contexte) -> None:
    """Les candidats suggérés sont des modèles autorisés du client."""
    contexte = fabriquer_contexte(profil=PROFIL_MIXTE)

    leviers = detecter_leviers(contexte)
    surdimensionne = next(
        levier for levier in leviers if levier.type_levier is TypeLevier.MODELE_SURDIMENSIONNE
    )

    assert surdimensionne.candidats_suggeres == [PETIT_MODELE, MOYEN_MODELE]
    assert set(surdimensionne.candidats_suggeres) <= set(contexte.modeles_autorises)


def test_profil_neutre_ne_produit_aucun_levier(contexte) -> None:
    """Absence de signal sur tous les axes : aucun levier n'est inventé."""
    assert detecter_leviers(contexte) == []


def test_detecteur_limite_a_certaines_regles(fabriquer_contexte) -> None:
    """Un détecteur restreint n'applique que les règles qu'on lui donne."""
    contexte = fabriquer_contexte(profil=PROFIL_MIXTE)
    detecteur = DetectorLeviers([RegleCachePromptsAbsent(), RegleRequetesRepetees()])

    leviers = detecteur.detecter(contexte)

    assert [levier.type_levier for levier in leviers] == [
        TypeLevier.CACHE_PROMPTS_ABSENT,
        TypeLevier.REQUETES_REPETEES,
    ]


def test_classement_stable_a_economie_egale() -> None:
    """À économie égale, l'ordre est alphabétique : le classement est reproductible."""

    def _levier(type_levier: TypeLevier) -> Levier:
        return Levier(
            id=f"lv-{type_levier.value}",
            tenant_id="t-demo-a",
            application_id="ticket-classifier",
            type_levier=type_levier,
            action_proposee="action",
            preuve_exigee="preuve",
            economie_mensuelle_estimee_usd=10.0,
        )

    classe = classer_par_economie(
        [_levier(TypeLevier.REQUETES_REPETEES), _levier(TypeLevier.CACHE_PROMPTS_ABSENT)]
    )

    assert [levier.type_levier for levier in classe] == [
        TypeLevier.CACHE_PROMPTS_ABSENT,
        TypeLevier.REQUETES_REPETEES,
    ]


def test_economie_totale_coherente_avec_les_regles(fabriquer_contexte) -> None:
    """L'économie totale est la somme des estimations, sans double comptage."""
    contexte = fabriquer_contexte(profil=PROFIL_MIXTE)

    leviers = detecter_leviers(contexte)
    total = sum(levier.economie_mensuelle_estimee_usd for levier in leviers)

    assert total == pytest.approx(55.76 + 19.17 + 9.11 + 6.08 + 0.41, abs=0.05)


def test_toutes_les_regles_respectent_le_protocole() -> None:
    """Chaque règle expose `type_levier` et `detecter` : contrat `Regle` respecté."""
    for regle in REGLES_DEFAUT:
        assert isinstance(regle, Regle), f"{type(regle).__name__} ne respecte pas le protocole"
        assert isinstance(regle.type_levier, TypeLevier)


def test_cloisonnement_des_leviers(fabriquer_contexte, contexte) -> None:
    """Cloisonnement (ENF-SEC-02) : un levier ne porte que le client analysé.

    Deux contextes de clients différents produisent des leviers distincts, et
    aucun levier ne référence l'autre client.
    """
    contexte_client_a = fabriquer_contexte(profil=PROFIL_MIXTE)
    contexte_client_b = replace(contexte_client_a, tenant_id="t-demo-b")

    leviers_a = detecter_leviers(contexte_client_a)
    leviers_b = detecter_leviers(contexte_client_b)

    assert {levier.tenant_id for levier in leviers_a} == {contexte.tenant_id}
    assert {levier.tenant_id for levier in leviers_b} == {"t-demo-b"}
    assert {levier.id for levier in leviers_a}.isdisjoint({levier.id for levier in leviers_b})
