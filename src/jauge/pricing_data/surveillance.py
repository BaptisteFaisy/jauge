"""Surveillance des changements de prix — détection (piste n° 10, EF-ATT-04, §14).

La table des tarifs est un actif : un changement de prix chez AWS (ou une baisse
négociée sur un engagement) crée une **nouvelle économie à prouver**. Ce module
détecte les changements, de façon déterministe (ENF-IA-01 : aucun chiffre n'est
produit par un modèle) :

- comparer deux versions de la table (ou deux dates) et rendre les changements
  de prix, par triplet (modèle, région, niveau de service) et par type de jeton
  (entrée, sortie, écriture de cache, lecture de cache) ;
- une variation nulle n'est pas rendue : elle serait du bruit ;
- chaque prix porte sa **période de validité** (date d'effet → veille de la date
  d'effet suivante, bornes incluses), lue du catalogue sans la réécrire.

L'impact économique de ces variations et les preuves à rejouer sont calculés dans
`jauge.pricing_data.impact_prix` (même piste, autre responsabilité).

Outils du socle réutilisés (zéro duplication) : `jauge.pricing_data.catalogue`
pour l'historique daté, `jauge.engine.stats.baisse_pourcentage` pour la variation
relative.

Convention de signe : une variation en % est **positive quand le prix baisse**
(convention de `baisse_pourcentage` du socle) ; `sens` l'exprime en toutes lettres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from jauge.engine.stats import baisse_pourcentage
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.pricing_data.catalogue import CatalogueTarifs, CleTarif


class PrixParType(BaseModel):
    """Prix d'un triplet pour chaque type de jeton (USD pour 1 000 jetons)."""

    entree: float = Field(ge=0.0)
    sortie: float = Field(ge=0.0)
    cache_ecriture: float = Field(default=0.0, ge=0.0)
    cache_lecture: float = Field(default=0.0, ge=0.0)


class VariationPrix(BaseModel):
    """Changement de prix d'un triplet (modèle, région, niveau de service).

    Les variations en % sont positives quand le prix **baisse** (convention du
    socle) ; `sens` le dit en toutes lettres.
    """

    modele_id: str
    region: str
    niveau: ServiceTier
    date_effet: date
    date_effet_avant: date | None = None
    devise: str = "USD"
    prix_avant: PrixParType
    prix_apres: PrixParType
    variation_entree_pourcentage: float
    variation_sortie_pourcentage: float
    variation_cache_ecriture_pourcentage: float
    variation_cache_lecture_pourcentage: float
    sens: Literal["baisse", "hausse"]

    def tarif_avant(self) -> TarifModele:
        """Tarif correspondant au prix d'avant, pour le calcul de coût."""
        return self._tarif(self.prix_avant)

    def tarif_apres(self) -> TarifModele:
        """Tarif correspondant au prix d'après, pour le calcul de coût."""
        return self._tarif(self.prix_apres)

    def _tarif(self, prix: PrixParType) -> TarifModele:
        """Construit un `TarifModele` à partir des prix par type."""
        return TarifModele(
            modele_id=self.modele_id,
            region=self.region,
            niveau=self.niveau,
            date_effet=self.date_effet,
            prix_entree_1k=prix.entree,
            prix_sortie_1k=prix.sortie,
            prix_cache_ecriture_1k=prix.cache_ecriture,
            prix_cache_lecture_1k=prix.cache_lecture,
            devise=self.devise,
        )


@dataclass(frozen=True)
class LigneApplicable:
    """Prix en vigueur d'un triplet à une date donnée."""

    date_effet: date
    prix: PrixParType
    devise: str = "USD"


def prix_par_type(tarif: TarifModele) -> PrixParType:
    """Convertit une ligne de tarif en prix par type de jeton."""
    return PrixParType(
        entree=tarif.prix_entree_1k,
        sortie=tarif.prix_sortie_1k,
        cache_ecriture=tarif.prix_cache_ecriture_1k,
        cache_lecture=tarif.prix_cache_lecture_1k,
    )


def snapshot(catalogue: CatalogueTarifs, a_la_date: date | None) -> dict[CleTarif, LigneApplicable]:
    """Prix en vigueur de chaque triplet à une date (`None` : première ligne).

    Le catalogue expose le prix de chaque triplet ; on garde ici, pour chaque
    triplet, la ligne qui s'applique afin de connaître sa date d'effet. La
    recherche de la ligne en vigueur est **celle du catalogue** (`tarif_a`, la
    plus récente dont la date d'effet est passée) : aucune logique de datation
    n'est dupliquée ici.
    """
    applicable: dict[CleTarif, LigneApplicable] = {}
    for cle in catalogue.cles():
        tarif = _ligne_applicable(catalogue, cle, a_la_date)
        if tarif is not None:
            applicable[cle] = tarif
    return applicable


def _ligne_applicable(
    catalogue: CatalogueTarifs, cle: CleTarif, a_la_date: date | None
) -> LigneApplicable | None:
    """Ligne applicable d'un triplet : la plus récente avant la date, ou la première."""
    if a_la_date is None:
        historique = catalogue.historique(*cle)
        if not historique:
            return None
        prix = historique[0]
    else:
        prix = catalogue.tarif_a(cle, a_la_date)
    if prix is None:
        return None
    return LigneApplicable(
        date_effet=prix.date_effet,
        prix=PrixParType(
            entree=prix.prix_entree_1k,
            sortie=prix.prix_sortie_1k,
            cache_ecriture=prix.prix_cache_ecriture_1k,
            cache_lecture=prix.prix_cache_lecture_1k,
        ),
        devise=prix.devise,
    )


def detecter_variations(
    avant: dict[CleTarif, LigneApplicable],
    apres: dict[CleTarif, LigneApplicable],
) -> list[VariationPrix]:
    """Compare deux versions de la table et rend les changements de prix.

    Seuls les triplets présents des deux côtés sont comparés, et une variation
    dont les prix sont identiques n'est pas rendue (rien n'a changé).
    """
    variations: list[VariationPrix] = []
    for cle, ligne_avant in avant.items():
        ligne_apres = apres.get(cle)
        if ligne_apres is None:
            continue
        if ligne_avant.prix == ligne_apres.prix:
            continue
        variations.append(_variation(cle, ligne_avant, ligne_apres))
    return variations


def _variation(cle: CleTarif, avant: LigneApplicable, apres: LigneApplicable) -> VariationPrix:
    """Construit la variation entre deux lignes d'un même triplet."""
    variation_entree = baisse_pourcentage(avant.prix.entree, apres.prix.entree)
    variation_sortie = baisse_pourcentage(avant.prix.sortie, apres.prix.sortie)
    variation_cache_e = baisse_pourcentage(avant.prix.cache_ecriture, apres.prix.cache_ecriture)
    variation_cache_l = baisse_pourcentage(avant.prix.cache_lecture, apres.prix.cache_lecture)
    baisse = _baisse(avant.prix, apres.prix)
    return VariationPrix(
        modele_id=cle[0],
        region=cle[1],
        niveau=cle[2],
        date_effet=apres.date_effet,
        date_effet_avant=avant.date_effet,
        devise=apres.devise,
        prix_avant=avant.prix,
        prix_apres=apres.prix,
        variation_entree_pourcentage=variation_entree,
        variation_sortie_pourcentage=variation_sortie,
        variation_cache_ecriture_pourcentage=variation_cache_e,
        variation_cache_lecture_pourcentage=variation_cache_l,
        sens="baisse" if baisse else "hausse",
    )


def _baisse(avant: PrixParType, apres: PrixParType) -> bool:
    """Vrai si au moins un type de jeton voit son prix diminuer."""
    return (
        apres.entree < avant.entree
        or apres.sortie < avant.sortie
        or apres.cache_ecriture < avant.cache_ecriture
        or apres.cache_lecture < avant.cache_lecture
    )


def variations_entre_dates(
    catalogue: CatalogueTarifs, depuis: date | None, jusqu: date
) -> list[VariationPrix]:
    """Variations de prix entre deux dates de la table datée."""
    return detecter_variations(snapshot(catalogue, depuis), snapshot(catalogue, jusqu))


def variations_entre_versions(
    avant: CatalogueTarifs, apres: CatalogueTarifs, a_la_date: date
) -> list[VariationPrix]:
    """Variations de prix entre deux versions de la table, à une date donnée."""
    return detecter_variations(snapshot(avant, a_la_date), snapshot(apres, a_la_date))
