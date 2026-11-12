"""Tests du catalogue de tarifs historisé (piste n° 10, EF-ATT-04, §14, §23 Q3).

Le catalogue expose la table datée sans la réécrire : prix en vigueur à une date,
période de validité de chaque ligne, historique d'un triplet, empreinte stable.
La recherche du tarif applicable reste celle du socle (`TableTarifs.trouver`).

Aucun réseau, aucun AWS : la table est construite en mémoire, la table de
démonstration est lue sur disque, et l'adaptateur `SourceTarifsAws` n'est exercé
que sur ses fonctions pures de conversion (jamais sur son client `boto3`).
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION
from tests.pricing_data.conftest import (
    DATE_BAISSE,
    DATE_INITIALE,
    PRIX_APRES,
    PRIX_INITIAUX,
    tarif,
)

from jauge.models.pricing import ServiceTier, TarifModele
from jauge.pricing_data.catalogue import (
    CatalogueTarifs,
    PrixApplicatif,
    SourceTarifsAws,
    SourceTarifsLocale,
)

CLE_PETIT = (PETIT_MODELE, REGION, ServiceTier.STANDARD)
CLE_GRAND = (GRAND_MODELE, REGION, ServiceTier.STANDARD)
VEILLE_BAISSE = date(2026, 5, 31)


def test_cles_triees_et_sans_doublon(catalogue: CatalogueTarifs) -> None:
    """Un triplet (modèle, région, niveau) n'apparaît qu'une fois, trié."""
    cles = catalogue.cles()
    assert len(cles) == 3
    assert cles == sorted(cles)
    assert len(set(cles)) == len(cles)
    assert CLE_PETIT in cles and CLE_GRAND in cles


def test_historique_du_plus_ancien_au_plus_recent(catalogue: CatalogueTarifs) -> None:
    """Chaque ligne historique porte sa période de validité, borne de fin incluse."""
    historique = catalogue.historique(*CLE_PETIT)

    assert [ligne.date_effet for ligne in historique] == [DATE_INITIALE, DATE_BAISSE]
    # La première ligne s'arrête la **veille** de la suivante ; la courante est ouverte.
    assert historique[0].date_fin == VEILLE_BAISSE
    assert historique[1].date_fin is None
    assert historique[0].prix_entree_1k == PRIX_INITIAUX[PETIT_MODELE][0]
    assert historique[1].prix_entree_1k == PRIX_APRES[PETIT_MODELE][0]
    assert all(isinstance(ligne, PrixApplicatif) for ligne in historique)


def test_prix_en_vigueur_selon_la_date(catalogue: CatalogueTarifs) -> None:
    """Le prix applicable bascule exactement à la date d'effet."""
    assert catalogue.tarif_a(CLE_PETIT, DATE_INITIALE).prix_entree_1k == 0.0008
    assert catalogue.tarif_a(CLE_PETIT, VEILLE_BAISSE).prix_entree_1k == 0.0008
    assert catalogue.tarif_a(CLE_PETIT, DATE_BAISSE).prix_entree_1k == 0.0006
    assert catalogue.tarif_a(CLE_PETIT, date(2027, 1, 1)).prix_entree_1k == 0.0006


def test_aucun_prix_avant_la_premiere_date_effet(catalogue: CatalogueTarifs) -> None:
    """Avant toute date d'effet, il n'y a ni prix courant ni ligne applicable."""
    assert catalogue.tarif_a(CLE_PETIT, date(2025, 12, 31)) is None
    assert catalogue.tarifs_courants(date(2025, 12, 31)) == []


def test_catalogue_ne_replie_pas_sur_standard(catalogue: CatalogueTarifs) -> None:
    """Le catalogue **liste** des prix ; le repli Standard reste au calcul de coût.

    `tarif_a` ne connaît pas le niveau Flex absent de la table, alors que
    `tarif_applicable` (le socle) replie sur le Standard — les deux rôles sont
    distincts et le test les distingue.
    """
    cle_flex = (PETIT_MODELE, REGION, ServiceTier.FLEX)
    assert catalogue.tarif_a(cle_flex, DATE_BAISSE) is None
    assert (
        catalogue.tarif_applicable(PETIT_MODELE, REGION, ServiceTier.FLEX, DATE_BAISSE) is not None
    )


def test_tarifs_courants_filtres_par_region_et_niveau(catalogue: CatalogueTarifs) -> None:
    """Filtres de lecture : région inconnue → rien, niveau Standard → trois prix."""
    assert catalogue.tarifs_courants(DATE_BAISSE, region="us-east-1") == []
    courant = catalogue.tarifs_courants(DATE_BAISSE, niveau=ServiceTier.STANDARD)
    assert len(courant) == 3
    assert {prix.modele_id for prix in courant} == {GRAND_MODELE, PETIT_MODELE, MOYEN_MODELE}
    # Le prix rendu est bien celui de la nouvelle version.
    petit = next(prix for prix in courant if prix.modele_id == PETIT_MODELE)
    assert petit.prix_entree_1k == PRIX_APRES[PETIT_MODELE][0]
    assert petit.date_effet == DATE_BAISSE and petit.date_fin is None


def test_empreinte_stable_pour_une_table_identique() -> None:
    """Mêmes prix → même empreinte, quel que soit l'ordre des lignes (ENF-IA-01)."""
    lignes = [
        tarif(PETIT_MODELE, DATE_INITIALE, 0.0008, 0.004),
        tarif(GRAND_MODELE, DATE_INITIALE, 0.015, 0.075),
    ]
    premier = CatalogueTarifs(lignes_table=lignes)
    second = CatalogueTarifs(lignes_table=list(reversed(lignes)))

    assert premier.empreinte(DATE_INITIALE) == second.empreinte(DATE_INITIALE)
    assert len(premier.empreinte(DATE_INITIALE)) == 16  # empreinte courte du socle


def test_empreinte_sensible_aux_prix_et_a_la_date(catalogue: CatalogueTarifs) -> None:
    """Un prix qui change change l'empreinte : la preuve scellée le verrait."""
    assert catalogue.empreinte(DATE_INITIALE) != catalogue.empreinte(DATE_BAISSE)
    # Entre deux dates sans changement de prix, l'empreinte ne bouge pas.
    assert catalogue.empreinte(DATE_INITIALE) == catalogue.empreinte(date(2026, 5, 31))


def test_catalogue_depuis_un_port_de_source() -> None:
    """Le catalogue se construit depuis n'importe quel port `SourceTarifs`."""

    class SourceFictive:
        """Source de test : deux lignes datées, aucun réseau."""

        def tarifs(self) -> list[TarifModele]:
            return [tarif(MOYEN_MODELE, DATE_INITIALE, 0.003, 0.015)]

    catalogue = CatalogueTarifs.depuis_source(SourceFictive())
    assert catalogue.cles() == [(MOYEN_MODELE, REGION, ServiceTier.STANDARD)]
    assert catalogue.tarif_a(catalogue.cles()[0], DATE_BAISSE).prix_sortie_1k == 0.015


def test_source_locale_lit_la_table_de_demonstration() -> None:
    """L'adaptateur local lit la table datée du dépôt (`data/pricing/`)."""
    lignes = SourceTarifsLocale().tarifs()
    assert lignes
    assert all(ligne.date_effet <= date(2027, 1, 1) for ligne in lignes)
    catalogue = CatalogueTarifs.depuis_source(SourceTarifsLocale())
    assert catalogue.cles()
    assert len(catalogue.empreinte(date(2026, 11, 1))) == 16


def test_source_aws_ne_cree_pas_de_client_a_la_construction(monkeypatch) -> None:
    """Aucun appel AWS à l'import ni à la construction (règle du dépôt)."""
    import boto3

    def interdire(*args, **kwargs):
        raise AssertionError("boto3 ne doit jamais être appelé pendant les tests")

    monkeypatch.setattr(boto3, "client", interdire)
    source = SourceTarifsAws(region=REGION)
    assert source.SERVICE_CODE == "AmazonBedrock"

    # La construction ne touche pas AWS ; seul un appel explicite le ferait.
    with pytest.raises(AssertionError, match="jamais être appelé"):
        source.client()


def test_source_aws_convertit_un_price_list_sans_reseau() -> None:
    """Conversion pure d'un Price List fictif : niveaux, positions de prix, dates."""
    source = SourceTarifsAws(region=REGION)
    produits = [
        _produit("modele-a", "EUW1-InputTokens", "0.015"),
        _produit("modele-a", "EUW1-OutputTokens", "0.075"),
        _produit("modele-a", "EUW1-CacheWriteTokens", "0.01875"),
        _produit("modele-a", "EUW1-CacheReadTokens", "0.0015"),
        _produit("modele-a", "EUW1-Flex-InputTokens", "0.0075"),
        _produit("modele-a", "EUW1-Flex-OutputTokens", "0.0375"),
        _produit("modele-b", "EUW1-Units", "1.0"),  # unité inconnue : ignorée
    ]

    lignes = source._convertir(produits)

    par_niveau = {ligne.niveau: ligne for ligne in lignes}
    assert set(par_niveau) == {ServiceTier.STANDARD, ServiceTier.FLEX}
    standard = par_niveau[ServiceTier.STANDARD]
    assert (standard.prix_entree_1k, standard.prix_sortie_1k) == (0.015, 0.075)
    assert standard.prix_cache_ecriture_1k == 0.01875
    assert standard.prix_cache_lecture_1k == 0.0015
    assert par_niveau[ServiceTier.FLEX].prix_entree_1k == 0.0075
    # Le Price List ne date pas les prix : la date d'effet est la date de lecture.
    assert all(ligne.date_effet <= date.today() for ligne in lignes)
    assert all(ligne.region == REGION for ligne in lignes)


def _produit(modele: str, usage: str, montant: str) -> dict[str, object]:
    """Entrée de Price List au format Bedrock (structure réelle, valeurs fictives)."""
    return {
        "product": {"attributes": {"model": modele, "usagetype": usage}},
        "terms": {
            "OnDemand": {
                "priceDimensions": {"1": {"unit": "1K tokens", "pricePerUnit": {"USD": montant}}}
            }
        },
    }
