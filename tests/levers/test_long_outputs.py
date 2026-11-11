"""Tests de la règle EF-LEV-04 — sorties anormalement longues.

Cas nominal (sorties proches de la limite et très au-dessus de la médiane),
cas limites et absence du signal.
"""

from __future__ import annotations

import pytest
from tests.conftest import GRAND_MODELE, REGION
from tests.levers.conftest import JOUR_DEBUT

from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.levers.long_outputs import RegleSortiesTropLongues
from jauge.models.lever import TypeLevier
from jauge.models.pricing import MetriquesRequete, ServiceTier

# 100 requêtes par jour : 75 sorties de 50 jetons, 25 sorties de 3 900 jetons.
SORTIES_LONGUES = {
    "longueur_sortie_moyenne": 1012.5,
    "longueur_sortie_mediane": 50,
    "longueur_sortie_p95": 3900,
    "part_requetes_sortie_longue": 0.25,
    "limite_sortie_modele": 4096,
}


def _agregats_sorties_longues(table: TableTarifs, agregats):
    """Agrégats du scénario « sorties longues », coût calculé par le socle."""
    tarif = table.trouver(GRAND_MODELE, REGION, ServiceTier.STANDARD, JOUR_DEBUT)
    court = calculer_cout_requete(
        tarif, MetriquesRequete(jetons_entree=200, jetons_sortie=50)
    ).cout_total
    long_ = calculer_cout_requete(
        tarif, MetriquesRequete(jetons_entree=200, jetons_sortie=3900)
    ).cout_total
    cout_jour = round(75 * court + 25 * long_, 8)
    return tuple(
        agregat.model_copy(update={"jetons_sortie_total": 101_250, "cout_recalcule_usd": cout_jour})
        for agregat in agregats
    )


def test_detecte_des_sorties_trop_longues(fabriquer_contexte, table_tarifs, agregats_30j) -> None:
    """Sorties au p95 à 95 % de la limite, 78 fois la médiane de la tâche."""
    contexte = fabriquer_contexte(
        profil=SORTIES_LONGUES,
        agregats=_agregats_sorties_longues(table_tarifs, agregats_30j),
    )

    levier = RegleSortiesTropLongues().detecter(contexte)

    assert levier is not None
    assert levier.type_levier is TypeLevier.SORTIES_TROP_LONGUES
    assert levier.action_proposee == "Limite de jetons, consigne de concision"
    assert levier.preuve_exigee == "Rejeu avec la nouvelle limite"


def test_economie_des_sorties_longues_calculee_a_la_main(
    fabriquer_contexte, table_tarifs, agregats_30j
) -> None:
    """Économie sur les seuls jetons excédentaires.

    jetons excédentaires au p95 = 3 900 − 50 = 3 850
    gain par requête longue     = 3 850 × 0,075/1k = 0,288750 $
    économie = 25 % × 0,288750 × 3 000 requêtes = 216,56 $/mois
    """
    contexte = fabriquer_contexte(
        profil=SORTIES_LONGUES,
        agregats=_agregats_sorties_longues(table_tarifs, agregats_30j),
    )

    levier = RegleSortiesTropLongues().detecter(contexte)

    assert levier is not None
    assert levier.economie_mensuelle_estimee_usd == pytest.approx(216.56, abs=0.05)


def test_pas_de_levier_si_sorties_loin_de_la_limite(
    fabriquer_contexte, table_tarifs, agregats_30j
) -> None:
    """Cas limite : p95 à 3 600 jetons, sous 90 % de la limite de 4 096."""
    contexte = fabriquer_contexte(
        profil={**SORTIES_LONGUES, "longueur_sortie_p95": 3600},
        agregats=_agregats_sorties_longues(table_tarifs, agregats_30j),
    )

    assert RegleSortiesTropLongues().detecter(contexte) is None


def test_pas_de_levier_si_sorties_proches_de_la_mediane(
    fabriquer_contexte, table_tarifs, agregats_30j
) -> None:
    """Cas limite : médiane à 2 000 jetons, le p95 n'est plus aberrant."""
    contexte = fabriquer_contexte(
        profil={**SORTIES_LONGUES, "longueur_sortie_mediane": 2000},
        agregats=_agregats_sorties_longues(table_tarifs, agregats_30j),
    )

    assert RegleSortiesTropLongues().detecter(contexte) is None


def test_pas_de_levier_si_peu_de_requetes_longues(
    fabriquer_contexte, table_tarifs, agregats_30j
) -> None:
    """Cas limite : 5 % de requêtes longues, sous le seuil de 10 %."""
    contexte = fabriquer_contexte(
        profil={**SORTIES_LONGUES, "part_requetes_sortie_longue": 0.05},
        agregats=_agregats_sorties_longues(table_tarifs, agregats_30j),
    )

    assert RegleSortiesTropLongues().detecter(contexte) is None


def test_pas_de_levier_sans_limite_connue(fabriquer_contexte) -> None:
    """Absence du signal : la limite de sortie du modèle est inconnue."""
    contexte = fabriquer_contexte(profil={**SORTIES_LONGUES, "limite_sortie_modele": 0})

    assert RegleSortiesTropLongues().detecter(contexte) is None


def test_pas_de_levier_sur_un_profil_neutre(fabriquer_contexte) -> None:
    """Absence du signal : sorties courtes et homogènes."""
    assert RegleSortiesTropLongues().detecter(fabriquer_contexte()) is None
