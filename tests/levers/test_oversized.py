"""Tests de la règle EF-LEV-01 — modèle probablement surdimensionné.

Cas nominal, cas limite (seuil de tâche simple) et absence du signal.
"""

from __future__ import annotations

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE

from jauge.levers.base import ContexteLevier
from jauge.levers.oversized import RegleModeleSurdimensionne
from jauge.models.lever import TypeLevier

TACHE_SIMPLE = {"classification": 2400, "extraction": 600}


def test_detecte_un_modele_surdimensionne(fabriquer_contexte) -> None:
    """Tâche de classification, entrées et sorties courtes → levier détecté."""
    contexte = fabriquer_contexte(
        profil={
            "requetes_par_type_tache": TACHE_SIMPLE,
            "part_sortie_structuree": 1.0,
            "diversite_sorties": 0.02,
        }
    )

    levier = RegleModeleSurdimensionne().detecter(contexte)

    assert levier is not None
    assert levier.type_levier is TypeLevier.MODELE_SURDIMENSIONNE
    assert levier.action_proposee == "Rejeu sur des modèles plus petits"
    assert levier.preuve_exigee == "Campagne de rejeu"
    assert levier.tenant_id == contexte.tenant_id
    assert levier.application_id == contexte.application_id
    assert levier.indices
    assert 0.0 <= levier.confiance <= 1.0


def test_economie_du_modele_surdimensionne(fabriquer_contexte) -> None:
    """Économie calculée à la main sur les tarifs du socle.

    coût actuel   = 200 × 0,015/1k + 50 × 0,075/1k = 0,006750 $/requête
    coût candidat = 200 × 0,0008/1k + 50 × 0,004/1k = 0,000360 $/requête
    économie      = (0,006750 − 0,000360) × 3 000 requêtes × 100 % de tâche simple
                  = 19,17 $/mois
    """
    contexte = fabriquer_contexte(profil={"requetes_par_type_tache": TACHE_SIMPLE})

    levier = RegleModeleSurdimensionne().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(19.17, abs=0.02)
    assert levier.candidats_suggeres == [PETIT_MODELE, MOYEN_MODELE]


def test_candidats_limites_aux_modeles_autorises(fabriquer_contexte) -> None:
    """Les candidats suggérés viennent des modèles autorisés : jamais d'invention."""
    contexte = fabriquer_contexte(
        profil={"requetes_par_type_tache": TACHE_SIMPLE},
        modeles_autorises=(GRAND_MODELE, PETIT_MODELE),
    )

    levier = RegleModeleSurdimensionne().detecter(contexte)

    assert levier is not None
    assert levier.candidats_suggeres == [PETIT_MODELE]


def test_pas_de_levier_si_aucun_candidat_moins_cher(fabriquer_contexte) -> None:
    """Un seul modèle autorisé (le modèle courant) : rien à proposer."""
    contexte = fabriquer_contexte(
        profil={"requetes_par_type_tache": TACHE_SIMPLE},
        modeles_autorises=(GRAND_MODELE,),
    )

    assert RegleModeleSurdimensionne().detecter(contexte) is None


def test_pas_de_levier_sous_le_seuil_de_tache_simple(fabriquer_contexte) -> None:
    """Cas limite : 49 % de tâche simple, juste sous le seuil de 50 %."""
    contexte = fabriquer_contexte(
        profil={"requetes_par_type_tache": {"classification": 1470, "generation": 1530}}
    )

    assert RegleModeleSurdimensionne().detecter(contexte) is None


def test_pas_de_levier_si_tache_complexe(fabriquer_contexte) -> None:
    """Absence du signal : rédaction libre, entrées et sorties longues."""
    contexte = fabriquer_contexte(
        profil={
            "requetes_par_type_tache": {"generation": 3000},
            "longueur_entree_moyenne": 4000,
            "longueur_sortie_moyenne": 900,
        }
    )

    assert RegleModeleSurdimensionne().detecter(contexte) is None


def test_regle_respecte_le_protocole(contexte: ContexteLevier) -> None:
    """La règle expose son type de levier et la méthode `detecter` du protocole."""
    regle = RegleModeleSurdimensionne()

    assert regle.type_levier is TypeLevier.MODELE_SURDIMENSIONNE
    assert regle.detecter(contexte) is None  # profil neutre : aucun signal
