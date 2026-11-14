"""Catalogue de tarifs historisé (EF-ATT-04, §14, §23 Q3 — piste n° 10).

La table des tarifs n'est pas une constante : c'est un **actif** que le client
peut relire, comparer dans le temps et auditer. Ce module expose la table
datée du dépôt (`data/pricing/`) comme un catalogue :

- le **prix courant** à une date donnée, par (modèle, région, niveau de service) ;
- l'**historique** d'un triplet (modèle, région, niveau), avec la **période de
  validité** de chaque ligne (date d'effet → veille de la date d'effet
  suivante, bornes incluses) ;
- l'**empreinte** de la table à une date : signature stable des prix en
  vigueur, comparable d'un environnement à l'autre (ENF-IA-01 : c'est du code
  qui la calcule).

Le module définit aussi le port `SourceTarifs` (§23 Q3 : « API de tarification ou
table tenue à la main ») et ses deux adaptateurs :

- `SourceTarifsLocale` : la table datée du dépôt (fichier JSON) ;
- `SourceTarifsAws` : l'API de tarification AWS (`boto3 pricing`), **écrit mais
  jamais appelé par les tests** — aucun réseau, aucun compte AWS (règle du
  dépôt). Il existe pour que la question du §23 Q3 soit tranchée sans
  dépendance à un compte : brancher l'adaptateur suffit, le code appelant ne
  change pas.

Aucun coût n'est recalculé ici : la formule reste celle de
`jauge.engine.calculator` et la lecture de la table reste celle de
`jauge.pricing_data.loader` (zéro duplication).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from jauge.common.ids import deterministic
from jauge.common.temps import aujourdhui as date_du_jour
from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.pricing_data.loader import charger_tarifs, lignes

#: Clé d'identification d'une ligne de tarif dans le catalogue.
CleTarif = tuple[str, str, ServiceTier]


class PrixApplicatif(BaseModel):
    """Prix en vigueur sur une période, avec les bornes de sa validité.

    `date_fin` est **inclusive** : elle vaut la veille de la date d'effet
    suivante pour une ligne historique, `None` pour la ligne en cours.
    """

    modele_id: str
    region: str
    niveau: ServiceTier
    date_effet: date
    date_fin: date | None = None
    prix_entree_1k: float = Field(ge=0.0)
    prix_sortie_1k: float = Field(ge=0.0)
    prix_cache_ecriture_1k: float = Field(default=0.0, ge=0.0)
    prix_cache_lecture_1k: float = Field(default=0.0, ge=0.0)
    devise: str = "USD"


class SourceTarifs(Protocol):
    """Port d'obtention de la table des tarifs datée (§23 Q3)."""

    def tarifs(self) -> list[TarifModele]:
        """Rend les lignes datées de la table des tarifs."""
        ...


class SourceTarifsLocale:
    """Adaptateur local : la table datée du dépôt (`data/pricing/`)."""

    def __init__(self, chemin: str | Path | None = None) -> None:
        """Prépare la lecture d'un fichier de tarifs (défaut : table du dépôt)."""
        self._chemin = Path(chemin) if chemin is not None else None

    def tarifs(self) -> list[TarifModele]:
        """Lit et valide le fichier de tarifs (lève `ValidationError` si invalide)."""
        if self._chemin is None:
            from jauge.pricing_data.seed import chemin_table_demo

            return charger_tarifs(chemin_table_demo())
        return charger_tarifs(self._chemin)


class SourceTarifsAws:
    """Adaptateur « API de tarification AWS » (`boto3 pricing`) — hors ligne.

    Cet adaptateur répond à la question du §23 Q3 (source des tarifs : API de
    tarification ou table tenue à la main) sans dépendre d'un compte AWS : le
    code appelant ne connaît que le port `SourceTarifs`.

    **Jamais appelé par les tests** (règle du dépôt : aucun réseau, aucun
    compte AWS). Deux limites assumées, à confirmer sur un compte réel :

    - le Price List nomme les modèles autrement que Jauge
      (`Claude Opus 4` contre `anthropic.claude-opus-4-…:0`) : l'identifiant
      Jauge est construit à partir du nom commercial, la correspondance reste
      à établir ;
    - le Price List ne dit pas à quelle date un prix a changé : la date
      d'effet retenue est la date de lecture, ce qui rend la datation
      approximative (un prix lu aujourd'hui est daté d'aujourd'hui).

    Le client `boto3` est créé à la demande (jamais à l'import) : l'import du
    module ne touche donc ni AWS ni la configuration locale.
    """

    SERVICE_CODE = "AmazonBedrock"

    def __init__(self, region: str = "eu-west-1", service_code: str | None = None) -> None:
        """Prépare la lecture du Price List pour une région."""
        self._region = region
        self._service_code = service_code or self.SERVICE_CODE

    def client(self) -> object:
        """Crée le client `pricing` (jamais appelé en test)."""
        import boto3

        return boto3.client("pricing", region_name="us-east-1")

    def tarifs(self) -> list[TarifModele]:
        """Lit les prix Bedrock du Price List et rend des lignes datées."""
        produits = self._produits()
        return self._convertir(produits)

    def _produits(self) -> list[dict[str, object]]:
        """Interroge le Price List (appel réseau : hors tests)."""
        client = self.client()
        reponse = client.get_products(  # type: ignore[attr-defined]
            ServiceCode=self._service_code,
            Filters=[{"Type": "TERM_MATCH", "Field": "regionCode", "Value": self._region}],
        )
        return list(reponse.get("PriceList", []))

    def _convertir(self, produits: list[dict[str, object]]) -> list[TarifModele]:
        """Convertit les produits du Price List en lignes de tarif datées."""
        aujourdhui = date_du_jour()
        lignes: dict[tuple[str, ServiceTier], list[float]] = {}
        for produit in produits:
            attributs = produit.get("product", {}).get("attributes", {})  # type: ignore[union-attr]
            prix = self._prix_unitaire(produit)
            if prix is None:
                continue
            modele = str(attributs.get("model", ""))  # type: ignore[union-attr]
            usage = str(attributs.get("usagetype", "")).lower()  # type: ignore[union-attr]
            niveau = self._niveau(usage)
            cle = (modele, niveau)
            valeurs = lignes.setdefault(cle, [0.0, 0.0, 0.0, 0.0])
            position = self._position(usage)
            if position is not None:
                valeurs[position] = prix
        resultat: list[TarifModele] = []
        for (modele, niveau), (entree, sortie, cache_e, cache_l) in lignes.items():
            if not entree and not sortie:
                continue
            resultat.append(
                TarifModele(
                    modele_id=modele,
                    region=self._region,
                    niveau=niveau,
                    date_effet=aujourdhui,
                    prix_entree_1k=entree,
                    prix_sortie_1k=sortie,
                    prix_cache_ecriture_1k=cache_e,
                    prix_cache_lecture_1k=cache_l,
                )
            )
        return resultat

    def _prix_unitaire(self, produit: dict[str, object]) -> float | None:
        """Prix pour 1 000 jetons d'un produit, `None` si l'unité ne correspond pas."""
        termes = produit.get("terms", {})  # type: ignore[union-attr]
        on_demand = termes.get("OnDemand", {})  # type: ignore[union-attr]
        for dimension in on_demand.get("priceDimensions", {}).values():  # type: ignore[union-attr]
            unite = str(dimension.get("unit", ""))
            if "1K tokens" not in unite and "1,000 tokens" not in unite:
                continue
            montant = dimension.get("pricePerUnit", {}).get("USD")  # type: ignore[union-attr]
            if montant is None:
                continue
            return float(montant)
        return None

    def _niveau(self, usage: str) -> ServiceTier:
        """Niveau de service déduit du type d'usage Bedrock."""
        if "flex" in usage:
            return ServiceTier.FLEX
        if "priority" in usage:
            return ServiceTier.PRIORITY
        return ServiceTier.STANDARD

    def _position(self, usage: str) -> int | None:
        """Position du prix dans le tuple (entrée, sortie, cache écriture, cache lecture).

        Le Price List écrit les types d'usage tantôt collés, tantôt séparés
        (`EUW1-InputTokens`, `EUW1-CacheWrite-Tokens`) : la comparaison porte sur
        une forme normalisée (ni espace ni tiret), sinon aucun prix n'est retenu.
        """
        normalise = usage.replace(" ", "").replace("-", "")
        if "cachewrite" in normalise:
            return 2
        if "cacheread" in normalise:
            return 3
        if "outputtoken" in normalise:
            return 1
        if "inputtoken" in normalise:
            return 0
        return None


class CatalogueTarifs:
    """Catalogue historisé d'une table de tarifs datée.

    Il lit les lignes sans les réécrire : les prix viennent de `TarifModele`, la
    recherche du tarif applicable reste celle du socle (`TableTarifs.trouver`).
    """

    def __init__(
        self, table: TableTarifs | None = None, lignes_table: list[TarifModele] | None = None
    ):
        """Construit le catalogue depuis une table ou une liste de lignes datées."""
        if lignes_table is not None:
            table = TableTarifs(list(lignes_table))
        self._table = table if table is not None else TableTarifs()
        self._lignes = lignes(self._table)

    @classmethod
    def depuis_source(cls, source: SourceTarifs) -> CatalogueTarifs:
        """Construit le catalogue depuis un port `SourceTarifs`."""
        return cls(lignes_table=source.tarifs())

    @property
    def table(self) -> TableTarifs:
        """Table des tarifs sous-jacente (lecture du socle)."""
        return self._table

    def cles(self) -> list[CleTarif]:
        """Triplets (modèle, région, niveau) présents dans la table, triés."""
        return sorted({(t.modele_id, t.region, t.niveau) for t in self._lignes})

    def tarif_a(self, cle: CleTarif, a_la_date: date) -> TarifModele | None:
        """Ligne d'un triplet en vigueur à une date, `None` si aucune.

        Contrairement à `TableTarifs.trouver`, il n'y a **pas** de repli sur le
        niveau Standard : le catalogue liste des prix, il ne décide pas du prix
        applicable à une requête (c'est le rôle du calcul de coût).
        """
        candidates = [
            t
            for t in self._lignes
            if (t.modele_id, t.region, t.niveau) == cle and t.date_effet <= a_la_date
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda t: t.date_effet)

    def tarif_applicable(
        self, modele_id: str, region: str, niveau: ServiceTier, a_la_date: date
    ) -> TarifModele:
        """Tarif applicable au sens du socle (avec repli Standard), ou erreur."""
        return self._table.trouver(modele_id, region, niveau, a_la_date)

    def tarifs_courants(
        self,
        a_la_date: date,
        region: str | None = None,
        niveau: ServiceTier | None = None,
    ) -> list[PrixApplicatif]:
        """Prix en vigueur à une date, filtrés par région et niveau.

        Chaque prix porte sa période de validité réelle (issue de
        `historique`) : une ligne est courante tant que la suivante n'a pas
        pris effet.
        """
        courant: list[PrixApplicatif] = []
        for cle in self.cles():
            if region is not None and cle[1] != region:
                continue
            if niveau is not None and cle[2] != niveau:
                continue
            en_vigueur = self._ligne_en_vigueur(cle, a_la_date)
            if en_vigueur is not None:
                courant.append(en_vigueur)
        return courant

    def _ligne_en_vigueur(self, cle: CleTarif, a_la_date: date) -> PrixApplicatif | None:
        """Ligne d'un triplet en vigueur à une date, avec sa fin de validité."""
        for ligne in self.historique(*cle):
            if ligne.date_effet > a_la_date:
                return None
            if ligne.date_fin is None or a_la_date <= ligne.date_fin:
                return ligne
        return None

    def historique(self, modele_id: str, region: str, niveau: ServiceTier) -> list[PrixApplicatif]:
        """Historique daté d'un triplet (modèle, région, niveau), du plus ancien au plus récent."""
        suite = sorted(
            (
                t
                for t in self._lignes
                if (t.modele_id, t.region, t.niveau) == (modele_id, region, niveau)
            ),
            key=lambda t: t.date_effet,
        )
        historique: list[PrixApplicatif] = []
        for position, tarif in enumerate(suite):
            date_fin = None
            if position + 1 < len(suite):
                date_fin = suite[position + 1].date_effet - timedelta(days=1)
            historique.append(self._applicatif(tarif, date_fin))
        return historique

    def empreinte(self, a_la_date: date) -> str:
        """Empreinte de la table à une date (signature stable des prix en vigueur)."""
        morceaux = []
        for cle in self.cles():
            tarif = self.tarif_a(cle, a_la_date)
            if tarif is None:
                continue
            morceaux.append(
                "|".join(
                    (
                        tarif.modele_id,
                        tarif.region,
                        tarif.niveau.value,
                        f"{tarif.prix_entree_1k:g}",
                        f"{tarif.prix_sortie_1k:g}",
                        f"{tarif.prix_cache_ecriture_1k:g}",
                        f"{tarif.prix_cache_lecture_1k:g}",
                    )
                )
            )
        return deterministic(";".join(morceaux))

    def _applicatif(self, tarif: TarifModele, date_fin: date | None) -> PrixApplicatif:
        """Convertit une ligne de tarif en prix applicable avec sa fin de validité."""
        return PrixApplicatif(
            modele_id=tarif.modele_id,
            region=tarif.region,
            niveau=tarif.niveau,
            date_effet=tarif.date_effet,
            date_fin=date_fin,
            prix_entree_1k=tarif.prix_entree_1k,
            prix_sortie_1k=tarif.prix_sortie_1k,
            prix_cache_ecriture_1k=tarif.prix_cache_ecriture_1k,
            prix_cache_lecture_1k=tarif.prix_cache_lecture_1k,
            devise=tarif.devise,
        )
