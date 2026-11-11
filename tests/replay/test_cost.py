"""Tests de l'estimation de coût et du plafond de campagne (§17.1, ENF-COUT-01)."""

from __future__ import annotations

import pytest
from tests.replay.constantes import (
    DATE_EFFET,
    MODELE_GRAND,
    MODELE_JUGE,
    MODELE_MOYEN,
    MODELE_PETIT,
    REGION_REJEU,
    tarif,
)

from jauge.common.errors import PlafondDepasseError, TarifInconnuError, ValidationError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.replay.cost import (
    HypothesesCout,
    estimer_cout_campagne,
    estimer_et_verifier,
    verifier_plafond,
)

HYPOTHESES = HypothesesCout(jetons_entree_moyens=800, jetons_sortie_moyens=300)


def _estimer(table_tarifs: TableTarifs, **surcharges):
    """Estimation de démonstration : 200 requêtes, deux candidats."""
    parametres = {
        "tarifs": table_tarifs,
        "region": REGION_REJEU,
        "a_la_date": DATE_EFFET,
        "modele_actuel": MODELE_GRAND,
        "candidats": [MODELE_MOYEN, MODELE_PETIT],
        "taille_echantillon": 200,
        "hypotheses": HYPOTHESES,
        "modele_juge_id": MODELE_JUGE,
    }
    parametres.update(surcharges)
    return estimer_cout_campagne(**parametres)


def test_nombre_d_executions_et_de_jugements(table_tarifs: TableTarifs) -> None:
    """§17.1 : taille × (2 + candidats) exécutions, taille × (candidats + 1) jugements."""
    estimation = _estimer(table_tarifs)
    assert estimation.executions_rejeu == 200 * (2 + 2)
    assert estimation.jugements == 200 * (2 + 1)
    assert estimation.nombre_candidats == 2


def test_cout_du_rejeu_recalcule_a_la_main(table_tarifs: TableTarifs) -> None:
    """Le coût du rejeu se recompose exactement depuis la table des tarifs Flex."""
    estimation = _estimer(table_tarifs)
    attendu = 0.0
    for modele, executions in ((MODELE_GRAND, 2), (MODELE_MOYEN, 1), (MODELE_PETIT, 1)):
        tarif_modele = table_tarifs.trouver(modele, REGION_REJEU, ServiceTier.FLEX, DATE_EFFET)
        cout = calculer_cout_requete(
            tarif_modele, MetriquesRequete(jetons_entree=800, jetons_sortie=300)
        ).cout_total
        attendu += executions * 200 * cout
    assert estimation.cout_rejeu_usd == pytest.approx(attendu, rel=1e-6)


def test_cout_de_l_evaluation_recalcule_a_la_main(table_tarifs: TableTarifs) -> None:
    """Le coût des jugements suit le tarif Standard du modèle juge."""
    estimation = _estimer(table_tarifs)
    tarif_du_juge = table_tarifs.trouver(
        MODELE_JUGE, REGION_REJEU, ServiceTier.STANDARD, DATE_EFFET
    )
    cout_jugement = calculer_cout_requete(
        tarif_du_juge, MetriquesRequete(jetons_entree=1200, jetons_sortie=400)
    ).cout_total
    assert estimation.cout_evaluation_usd == pytest.approx(600 * cout_jugement, rel=1e-6)
    assert estimation.cout_total_usd == pytest.approx(
        estimation.cout_rejeu_usd + estimation.cout_evaluation_usd, rel=1e-9
    )


def test_plafond_depasse_refuse_la_campagne(table_tarifs: TableTarifs) -> None:
    """ENF-COUT-01 : au-dessus du plafond du client, la campagne est refusée."""
    estimation = _estimer(table_tarifs)
    with pytest.raises(PlafondDepasseError) as capture:
        verifier_plafond(estimation, estimation.cout_total_usd / 2)
    assert capture.value.code == "campaign_cost_cap_exceeded"
    assert capture.value.details["plafond_usd"] == pytest.approx(estimation.cout_total_usd / 2)
    assert capture.value.details["depassement_usd"] > 0


def test_plafond_respecte(table_tarifs: TableTarifs) -> None:
    """Sous le plafond, l'estimation est rendue telle quelle, avec la marge restante."""
    estimation = _estimer(table_tarifs, plafond_usd=50.0)
    assert estimation.sous_plafond is True
    assert verifier_plafond(estimation, 50.0) is estimation
    assert estimation.marge_usd() == pytest.approx(50.0 - estimation.cout_total_usd)


def test_estimer_et_verifier_leve_avant_tout_appel(table_tarifs: TableTarifs) -> None:
    """La porte d'entrée unique refuse la campagne sans avoir rien dépensé."""
    with pytest.raises(PlafondDepasseError):
        estimer_et_verifier(
            tarifs=table_tarifs,
            region=REGION_REJEU,
            a_la_date=DATE_EFFET,
            modele_actuel=MODELE_GRAND,
            candidats=[MODELE_MOYEN, MODELE_PETIT],
            taille_echantillon=200,
            hypotheses=HYPOTHESES,
            modele_juge_id=MODELE_JUGE,
            plafond_usd=0.001,
        )


def test_taille_invalide_refusee(table_tarifs: TableTarifs) -> None:
    """Une taille d'échantillon nulle est refusée avant tout calcul de coût."""
    with pytest.raises(ValidationError):
        _estimer(table_tarifs, taille_echantillon=0)


def test_tarif_inconnu_signale(table_tarifs: TableTarifs) -> None:
    """Un modèle absent de la table ne coûte pas zéro en silence."""
    with pytest.raises(TarifInconnuError):
        _estimer(table_tarifs, candidats=["modele-absent"])


def test_tarif_du_juge_fourni_hors_table(table_tarifs: TableTarifs) -> None:
    """Le tarif du juge peut être fourni quand la table ne le couvre pas."""
    estimation = _estimer(
        table_tarifs,
        modele_juge_id="juge-hors-table",
        tarif_juge=tarif("juge-hors-table", 0.003, 0.015, ServiceTier.STANDARD),
    )
    assert estimation.cout_evaluation_usd > 0


def test_hypotheses_negatives_refusees() -> None:
    """Une hypothèse de jetons négative est une donnée invalide, pas un coût négatif."""
    with pytest.raises(ValidationError):
        HypothesesCout(jetons_entree_moyens=-1, jetons_sortie_moyens=10)


def test_sans_candidat(table_tarifs: TableTarifs) -> None:
    """Sans candidat, seule la double exécution de la référence est facturée."""
    estimation = _estimer(table_tarifs, candidats=[])
    assert estimation.executions_rejeu == 200 * 2
    assert estimation.jugements == 200
    assert estimation.cout_rejeu_usd > 0
