"""Tests de la détection des changements de prix (piste n° 10, EF-ATT-04).

La détection compare deux **instantanés** de la table : le prix en vigueur de
chaque triplet à une date. Trois propriétés sont vérifiées : l'instantané prend
bien la ligne la plus récente déjà en vigueur, une variation nulle n'est pas
rendue, et la convention de signe est celle du socle (positive quand le prix
baisse).

Aucun réseau, aucun AWS : la table est construite en mémoire.
"""

from __future__ import annotations

from datetime import date

from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION
from tests.pricing_data.conftest import (
    DATE_BAISSE,
    DATE_INITIALE,
    PRIX_APRES,
    PRIX_INITIAUX,
    PRIX_MOYEN,
    tarif,
)

from jauge.models.pricing import ServiceTier, TarifModele
from jauge.pricing_data.catalogue import CatalogueTarifs
from jauge.pricing_data.surveillance import (
    LigneApplicable,
    PrixParType,
    detecter_variations,
    prix_par_type,
    snapshot,
    variations_entre_dates,
    variations_entre_versions,
)

CLE_PETIT = (PETIT_MODELE, REGION, ServiceTier.STANDARD)
CLE_GRAND = (GRAND_MODELE, REGION, ServiceTier.STANDARD)
CLE_MOYEN = (MOYEN_MODELE, REGION, ServiceTier.STANDARD)
VEILLE_BAISSE = date(2026, 5, 31)


def test_instantane_prend_la_ligne_la_plus_recente_en_vigueur(
    catalogue: CatalogueTarifs,
) -> None:
    """À la date d'effet, l'instantané rend le **nouveau** prix, pas le premier.

    C'est le point sensible du module : les historiques sont triés du plus ancien
    au plus récent, et prendre la première ligne valable masquerait tout
    changement de prix (aucune variation ne serait jamais détectée).
    """
    apres = snapshot(catalogue, DATE_BAISSE)

    assert set(apres) == {CLE_PETIT, CLE_GRAND, CLE_MOYEN}
    assert apres[CLE_PETIT].date_effet == DATE_BAISSE
    assert apres[CLE_PETIT].prix.entree == PRIX_APRES[PETIT_MODELE][0]
    assert apres[CLE_GRAND].prix.sortie == PRIX_APRES[GRAND_MODELE][1]


def test_instantane_avant_la_baisse_rend_les_prix_initiaux(
    catalogue: CatalogueTarifs,
) -> None:
    """La veille de la date d'effet, les anciens prix sont encore en vigueur."""
    avant = snapshot(catalogue, VEILLE_BAISSE)

    assert avant[CLE_PETIT].date_effet == DATE_INITIALE
    assert avant[CLE_PETIT].prix.entree == PRIX_INITIAUX[PETIT_MODELE][0]
    assert avant[CLE_MOYEN].prix.entree == PRIX_MOYEN[0]


def test_instantane_sans_date_prend_la_premiere_ligne(
    catalogue: CatalogueTarifs,
) -> None:
    """Sans date, l'instantané part de la première ligne connue de chaque triplet."""
    premier = snapshot(catalogue, None)

    assert {cle: ligne.date_effet for cle, ligne in premier.items()} == {
        CLE_PETIT: DATE_INITIALE,
        CLE_GRAND: DATE_INITIALE,
        CLE_MOYEN: DATE_INITIALE,
    }


def test_instantane_vide_avant_toute_date_effet(catalogue: CatalogueTarifs) -> None:
    """Avant la première date d'effet, aucun prix n'est en vigueur."""
    assert snapshot(catalogue, date(2025, 12, 31)) == {}


def test_prix_par_type_reflete_les_quatre_types_de_jetons() -> None:
    """La conversion d'une ligne de tarif garde les quatre prix (dont le cache)."""
    ligne = TarifModele(
        modele_id=PETIT_MODELE,
        region=REGION,
        niveau=ServiceTier.STANDARD,
        date_effet=DATE_INITIALE,
        prix_entree_1k=0.0008,
        prix_sortie_1k=0.004,
        prix_cache_ecriture_1k=0.001,
        prix_cache_lecture_1k=0.00008,
    )

    prix = prix_par_type(ligne)

    assert (prix.entree, prix.sortie) == (0.0008, 0.004)
    assert (prix.cache_ecriture, prix.cache_lecture) == (0.001, 0.00008)


def test_aucune_variation_quand_les_prix_n_ont_pas_bouge(
    catalogue: CatalogueTarifs,
) -> None:
    """Entre deux dates sans changement de prix, la détection ne rend rien."""
    assert variations_entre_dates(catalogue, DATE_INITIALE, VEILLE_BAISSE) == []


def test_variations_entre_deux_dates(catalogue: CatalogueTarifs) -> None:
    """Baisse sur le petit modèle, hausse sur le grand, moyen modèle inchangé."""
    variations = variations_entre_dates(catalogue, DATE_INITIALE, DATE_BAISSE)

    assert {v.modele_id for v in variations} == {PETIT_MODELE, GRAND_MODELE}
    par_modele = {v.modele_id: v for v in variations}
    baisse = par_modele[PETIT_MODELE]
    hausse = par_modele[GRAND_MODELE]
    assert baisse.sens == "baisse" and hausse.sens == "hausse"
    # Convention du socle : positive quand le prix baisse.
    assert baisse.variation_entree_pourcentage == 25.0
    assert hausse.variation_entree_pourcentage == -20.0
    assert baisse.variation_sortie_pourcentage == 25.0


def test_variation_porte_les_deux_dates_et_les_tarifs(
    catalogue: CatalogueTarifs,
) -> None:
    """Une variation est datée des deux côtés et reconstructible en tarifs."""
    variation = next(
        v
        for v in variations_entre_dates(catalogue, DATE_INITIALE, DATE_BAISSE)
        if v.modele_id == PETIT_MODELE
    )

    assert variation.date_effet == DATE_BAISSE
    assert variation.date_effet_avant == DATE_INITIALE
    assert variation.niveau == ServiceTier.STANDARD and variation.region == REGION
    assert variation.tarif_avant().prix_entree_1k == PRIX_INITIAUX[PETIT_MODELE][0]
    assert variation.tarif_apres().prix_entree_1k == PRIX_APRES[PETIT_MODELE][0]
    assert variation.tarif_apres().date_effet == DATE_BAISSE


def test_triplet_absent_d_un_cote_n_est_pas_compare() -> None:
    """Un modèle présent dans une seule des deux tables ne produit pas de variation."""
    ancien = CatalogueTarifs(lignes_table=[tarif(PETIT_MODELE, DATE_INITIALE, 0.001, 0.005)])
    complet = CatalogueTarifs(
        lignes_table=[
            tarif(PETIT_MODELE, DATE_INITIALE, 0.001, 0.005),
            tarif(GRAND_MODELE, DATE_INITIALE, 0.015, 0.075),
        ]
    )

    variations = variations_entre_versions(ancien, complet, DATE_BAISSE)

    assert variations == []


def test_variations_entre_deux_versions_de_table() -> None:
    """Deux versions de la table, comparées à une date : la baisse est détectée."""
    avant = CatalogueTarifs(
        lignes_table=[tarif(PETIT_MODELE, DATE_INITIALE, *PRIX_INITIAUX[PETIT_MODELE])]
    )
    apres = CatalogueTarifs(
        lignes_table=[
            tarif(PETIT_MODELE, DATE_INITIALE, *PRIX_INITIAUX[PETIT_MODELE]),
            tarif(PETIT_MODELE, DATE_BAISSE, *PRIX_APRES[PETIT_MODELE]),
        ]
    )

    variations = variations_entre_versions(avant, apres, DATE_BAISSE)

    assert len(variations) == 1
    assert variations[0].sens == "baisse"
    assert variations[0].variation_entree_pourcentage == 25.0
    # À la date initiale, la seconde version n'a pas encore pris effet.
    assert variations_entre_versions(avant, apres, DATE_INITIALE) == []


def test_detecter_variations_est_une_fonction_pure(catalogue: CatalogueTarifs) -> None:
    """Un instantané modifié à la main produit exactement la variation attendue."""
    avant = snapshot(catalogue, DATE_INITIALE)
    apres = snapshot(catalogue, DATE_INITIALE)
    ligne = apres[CLE_MOYEN]
    apres[CLE_MOYEN] = LigneApplicable(
        date_effet=DATE_BAISSE,
        prix=PrixParType(entree=ligne.prix.entree / 2, sortie=ligne.prix.sortie),
    )

    variations = detecter_variations(avant, apres)

    assert len(variations) == 1
    assert variations[0].modele_id == MOYEN_MODELE
    assert variations[0].sens == "baisse"
    assert variations[0].variation_entree_pourcentage == 50.0
    assert variations[0].variation_sortie_pourcentage == 0.0
