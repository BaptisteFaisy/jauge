"""Tests du rapprochement avec la facture CUR 2.0 (EF-ATT-06, EF-ATT-08).

Le rapprochement est la preuve que l'attribution est juste : coût recalculé
depuis les journaux contre coût facturé dans CUR 2.0. Cible de production : écart
inférieur à 3 % (§4). La part non attribuée et ses causes sont affichées à côté
de l'écart (EF-ATT-07).
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.attribution.conftest import CUR, JOUR, JOURNAUX

from jauge.attribution.aggregate import AgregateurQuotidien, ResultatAgregation
from jauge.attribution.logs import lire_journaux
from jauge.attribution.reconcile import (
    TYPE_LIGNE_USAGE,
    FactureJour,
    LigneCur,
    StatistiquesCur,
    facture_par_jour,
    iterer_lignes_cur,
    lire_cur,
    lire_fichier_cur,
    parser_ligne_cur,
    rapprocher_journee,
    rapprocher_periode,
)
from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.models.tenant import Application, Tenant

#: Ligne CUR 2.0 de référence, en colonnes longues d'export.
LIGNE_CUR = {
    "line_item_usage_start_date": "2026-11-09T00:00:00Z",
    "line_item_line_item_type": "Usage",
    "line_item_product_code": "AmazonBedrock",
    "line_item_unblended_cost": "0.0487",
    "resource_tags_user_application": "ticket-classifier",
    "line_item_currency_code": "USD",
}


@pytest.fixture
def resultat(tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]):
    """Agrégat du journal de référence (coût recalculé de la journée)."""
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)
    return agregateur.agreger(lire_journaux(JOURNAUX))


def test_lecture_de_l_export_cur() -> None:
    """L'export de référence est lu : seules les lignes de consommation Bedrock restent."""
    stats = StatistiquesCur()
    lignes = list(lire_fichier_cur(CUR, stats))

    assert stats.lues == 7
    assert stats.retenues == 5
    assert stats.ignorees_type == 2  # une taxe, un service hors Bedrock
    assert {ligne.jour for ligne in lignes} == {JOUR, date(2026, 11, 8)}


def test_ligne_cur_de_reference_est_convertie() -> None:
    """Date, montant, étiquette et type de ligne sont extraits."""
    ligne = parser_ligne_cur(LIGNE_CUR)

    assert ligne.jour == JOUR
    assert ligne.montant_usd == pytest.approx(0.0487)
    assert ligne.etiquette == "ticket-classifier"
    assert ligne.type_ligne == TYPE_LIGNE_USAGE
    assert ligne.attribuee is True


@pytest.mark.parametrize(
    "variante",
    [
        {"line_item_unblended_cost": "1.25"},
        {"unblended_cost": "1.25"},
        {"cost": "1.25"},
        {"montant": "1.25"},
    ],
)
def test_variantes_du_nom_de_montant(variante: dict) -> None:
    """Les orthographes plausibles du montant facturé sont acceptées."""
    brut = {cle: valeur for cle, valeur in LIGNE_CUR.items() if cle != "line_item_unblended_cost"}

    assert parser_ligne_cur({**brut, **variante}).montant_usd == pytest.approx(1.25)


@pytest.mark.parametrize(
    "variante",
    [
        {"line_item_usage_start_date": "2026-11-09"},
        {"usage_date": "2026-11-09"},
        {"date": "2026-11-09"},
        {"jour": "2026-11-09"},
    ],
)
def test_variantes_du_nom_de_date(variante: dict) -> None:
    """Les orthographes plausibles de la date d'usage sont acceptées."""
    brut = {cle: valeur for cle, valeur in LIGNE_CUR.items() if cle != "line_item_usage_start_date"}

    assert parser_ligne_cur({**brut, **variante}).jour == JOUR


def test_etiquettes_vides_comptent_comme_non_attribuees() -> None:
    """`-`, chaîne vide ou `non-attribue` : la ligne n'est pas attribuée."""
    for valeur in ("-", "", "non-attribue", "None"):
        ligne = parser_ligne_cur({**LIGNE_CUR, "resource_tags_user_application": valeur})
        assert ligne.attribuee is False, valeur


def test_ligne_sans_date_refusee() -> None:
    """Une ligne CUR non datable ne peut pas entrer dans un rapprochement quotidien."""
    brut = {cle: valeur for cle, valeur in LIGNE_CUR.items() if cle != "line_item_usage_start_date"}

    with pytest.raises(ValidationError, match="date d'usage"):
        parser_ligne_cur(brut)


def test_ligne_sans_montant_refusee() -> None:
    """Une ligne CUR sans montant exploitable est refusée explicitement."""
    brut = {cle: valeur for cle, valeur in LIGNE_CUR.items() if cle != "line_item_unblended_cost"}

    with pytest.raises(ValidationError, match="montant"):
        parser_ligne_cur(brut)


def test_lignes_illisibles_comptees_sans_interruption() -> None:
    """Une ligne illisible n'interrompt pas la lecture : elle est comptée."""
    stats = StatistiquesCur()
    source = [{}, {"line_item_line_item_type": "Usage"}, LIGNE_CUR]

    lignes = list(iterer_lignes_cur(source, stats))

    assert len(lignes) == 1
    assert stats.ignorees_illisibles == 2
    assert set(stats.motifs) == {"ligne CUR sans date d'usage exploitable"}


def test_taxes_et_credits_ignores() -> None:
    """Taxes et crédits ne mesurent pas la consommation : ils sont écartés."""
    stats = StatistiquesCur()
    source = [
        {**LIGNE_CUR, "line_item_line_item_type": "Tax"},
        {**LIGNE_CUR, "line_item_line_item_type": "Credit"},
        LIGNE_CUR,
    ]

    lignes = list(iterer_lignes_cur(source, stats))

    assert len(lignes) == 1
    assert stats.ignorees_type == 2


def test_service_hors_bedrock_ignore() -> None:
    """Un service hors Bedrock (S3, Lambda…) est écarté du rapprochement."""
    stats = StatistiquesCur()
    source = [{**LIGNE_CUR, "line_item_product_code": "AmazonS3"}, LIGNE_CUR]

    lignes = list(iterer_lignes_cur(source, stats))

    assert len(lignes) == 1
    assert stats.ignorees_type == 1


def test_facture_par_jour_et_par_etiquette() -> None:
    """La facture est consolidée par jour, avec la part sans étiquette."""
    lignes = list(lire_fichier_cur(CUR))

    factures = facture_par_jour(lignes)

    jour = factures[JOUR]
    assert jour.cout_total_usd == pytest.approx(0.136)
    assert jour.cout_par_etiquette["ticket-classifier"] == pytest.approx(0.095)
    assert jour.cout_non_attribue_usd == pytest.approx(0.004)
    assert jour.part_non_attribuee_pourcentage == pytest.approx(2.94)
    assert factures[date(2026, 11, 8)].cout_total_usd == pytest.approx(0.20)


def test_rapprochement_conforme(resultat: ResultatAgregation, tenant: Tenant) -> None:
    """Un écart sous 3 % est conforme (cible §4) et l'écart est signé."""
    facture = FactureJour(
        jour=JOUR,
        cout_total_usd=resultat.cout_recalcule_usd * 1.02,
        cout_par_etiquette={"ticket-classifier": resultat.cout_recalcule_usd * 1.02},
        cout_non_attribue_usd=0.0,
    )

    rapprochement = rapprocher_journee(resultat, facture, tenant.id)

    assert rapprochement.conforme is True
    assert rapprochement.ecart_pourcentage == pytest.approx(1.96, abs=0.02)
    assert rapprochement.ecart_usd < 0  # recalculé sous la facture
    assert rapprochement.jour == JOUR
    assert rapprochement.tenant_id == tenant.id


def test_rapprochement_non_conforme(resultat: ResultatAgregation, tenant: Tenant) -> None:
    """Un écart de 64 % (fixture réelle) sort de la cible et doit alerter."""
    lignes = list(lire_fichier_cur(CUR))
    facture = facture_par_jour(lignes)[JOUR]

    rapprochement = rapprocher_journee(resultat, facture, tenant.id)

    assert rapprochement.conforme is False
    assert rapprochement.cout_recalcule_usd == pytest.approx(resultat.cout_recalcule_usd)
    assert rapprochement.cout_facture_usd == pytest.approx(0.136)
    assert rapprochement.ecart_pourcentage > 3.0


def test_part_non_attribuee_et_causes_dans_le_rapprochement(
    resultat: ResultatAgregation, tenant: Tenant
) -> None:
    """La part non attribuée et ses causes accompagnent l'écart (EF-ATT-07)."""
    facture = FactureJour(
        jour=JOUR,
        cout_total_usd=0.10,
        cout_par_etiquette={"ticket-classifier": 0.096, "non-attribue": 0.004},
        cout_non_attribue_usd=0.004,
    )

    rapprochement = rapprocher_journee(resultat, facture, tenant.id)

    assert rapprochement.part_non_attribuee_pourcentage == pytest.approx(4.0)
    assert rapprochement.causes_non_attribuees  # au moins une cause nommée
    assert any("étiquette" in cause for cause in rapprochement.causes_non_attribuees)


def test_part_non_attribuee_bornee_par_la_facture(
    resultat: ResultatAgregation, tenant: Tenant
) -> None:
    """La part non attribuée ne peut pas dépasser la facture du jour."""
    facture = FactureJour(
        jour=JOUR,
        cout_total_usd=0.001,
        cout_par_etiquette={"non-attribue": 0.001},
        cout_non_attribue_usd=0.001,
    )

    rapprochement = rapprocher_journee(resultat, facture, tenant.id)

    assert rapprochement.part_non_attribuee_pourcentage <= 100.0


def test_rapprochement_periode(resultat: ResultatAgregation, tenant: Tenant) -> None:
    """Un rapprochement par jour facturé ; un jour sans agrégat est sauté."""
    factures = facture_par_jour(lire_fichier_cur(CUR))

    rapprochements = rapprocher_periode([resultat], factures.values(), tenant.id)

    assert [r.jour for r in rapprochements] == [JOUR]
    assert all(r.tenant_id == tenant.id for r in rapprochements)


def test_facture_sans_journal_ne_produit_pas_de_rapprochement(
    resultat: ResultatAgregation, tenant: Tenant
) -> None:
    """Un jour facturé sans agrégat n'est pas rapproché (écart de 100 % trompeur)."""
    factures = facture_par_jour(lire_fichier_cur(CUR))
    sans_journal = [
        FactureJour(
            jour=date(2026, 1, 1),
            cout_total_usd=1.0,
            cout_par_etiquette={},
            cout_non_attribue_usd=0.0,
        )
    ]

    assert rapprocher_periode([resultat], sans_journal, tenant.id) == []
    assert len(factures) == 2


def test_lignes_cur_en_flux() -> None:
    """La lecture CUR est un itérateur : rien n'est chargé avant consommation."""
    flux = lire_cur(CUR.open(encoding="utf-8"))

    assert iter(flux) is flux
    assert next(flux).jour == JOUR


def test_ligne_cur_sans_service_est_retenue() -> None:
    """Un export déjà restreint (pas de colonne service) n'est pas rejeté."""
    brut = {cle: valeur for cle, valeur in LIGNE_CUR.items() if cle != "line_item_product_code"}

    lignes = list(iterer_lignes_cur([brut], service=None))

    assert len(lignes) == 1
    assert lignes[0].attribuee is True


def test_etiquette_imbriquee_dans_les_ressources() -> None:
    """Une étiquette rangée dans un objet de ressources reste lisible."""
    brut = {
        **{
            cle: valeur
            for cle, valeur in LIGNE_CUR.items()
            if cle != "resource_tags_user_application"
        },
        "resource_tags": {"user:application": "resume-nocturne"},
    }

    assert parser_ligne_cur(brut).etiquette == "resume-nocturne"


def test_montant_negatif_accepte() -> None:
    """Un montant négatif (crédit de consommation) est conservé tel quel."""
    ligne = parser_ligne_cur({**LIGNE_CUR, "line_item_unblended_cost": "-0.5"})

    assert ligne.montant_usd == pytest.approx(-0.5)


def test_ligne_cur_type_est_une_chaine_normalisee() -> None:
    """Le type de ligne est comparé en minuscules, quelle que soit la casse."""
    ligne = parser_ligne_cur({**LIGNE_CUR, "line_item_line_item_type": "USAGE"})

    assert ligne.type_ligne == TYPE_LIGNE_USAGE


def test_ligne_cur_objet() -> None:
    """`LigneCur` est immuable et expose son caractère attribué."""
    ligne = LigneCur(jour=JOUR, montant_usd=1.0, etiquette=None)

    assert ligne.attribuee is False
    with pytest.raises(AttributeError):
        ligne.montant_usd = 2.0  # type: ignore[misc]
