"""Rapprochement entre coût recalculé et facture AWS (§8.2, EF-ATT-06, EF-ATT-08).

Le rapprochement est la preuve que l'attribution est juste : on compare le total
recalculé depuis les journaux d'invocation au total **facturé** lu dans l'export
CUR 2.0, jour par jour. La cible de production est un écart inférieur à 3 %
(§4) ; au-delà, `RapprochementJour.conforme` passe à `False` et le portail doit
afficher l'alerte.

Structure d'une ligne CUR 2.0 retenue ici (§3.2, EF-ATT-08) : une date d'usage,
un montant non remisé, une étiquette d'allocation et un type de ligne. Les noms
de colonnes exacts dépendent de la définition d'export du client : ils sont
listés dans :data:`CHAMPS_CUR` et comparés sous forme normalisée, comme pour les
journaux (`logs.py`). Les colonnes inconnues sont ignorées ; une ligne
inexploitable est comptée, jamais fatale.

Deux règles de comptabilité appliquées ici :

- seules les lignes de type `Usage` sont retenues : taxes, crédits et remises ne
  mesurent pas la consommation d'inférence et fausseraient l'écart ;
- une ligne sans étiquette d'allocation alimente la dépense non attribuée
  (EF-ATT-07) : c'est là que se voient les appels non étiquetés et le point
  d'accès `bedrock-mantle`, absent des journaux.

Aucune formule de coût n'est réimplémentée : l'écart, son pourcentage et la part
non attribuée sont calculés par `jauge.engine.attribution.rapprocher_facture`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import IO

from jauge.attribution.aggregate import ResultatAgregation
from jauge.attribution.tags import CAUSE_SANS_ETIQUETTE
from jauge.common.errors import ValidationError
from jauge.engine.attribution import rapprocher_facture
from jauge.models.cost import RapprochementJour

#: Orthographes acceptées pour chaque champ d'une ligne CUR 2.0.
#:
#: La forme longue (`line_item_usage_start_date`) est celle de l'export CUR 2.0
#: en Parquet/CSV ; la forme courte sert aux fixtures de test et aux exports
#: retraités par le client. Comparaison sous forme normalisée (cf. `normaliser`).
CHAMPS_CUR: dict[str, tuple[str, ...]] = {
    "jour": (
        "line_item_usage_start_date",
        "usage_start_date",
        "usage_date",
        "date",
        "jour",
        "timestamp",
    ),
    "montant": (
        "line_item_unblended_cost",
        "unblended_cost",
        "line_item_blended_cost",
        "montant",
        "cout",
        "cost",
        "amount",
    ),
    "etiquette": (
        "resource_tags_user_application",
        "resource_tags_user_jauge_application",
        "resource_tags",
        "cost_category_application",
        "etiquette_allocation",
        "allocation_tag",
        "application",
        "tag",
    ),
    "type_ligne": (
        "line_item_line_item_type",
        "line_item_type",
        "type_ligne",
        "charge_type",
    ),
    "service": ("line_item_product_code", "product_code", "service", "service_code"),
    "devise": ("line_item_currency_code", "currency_code", "devise", "currency"),
}

#: Type de ligne comptabilisé : la consommation, pas les taxes ni les crédits.
TYPE_LIGNE_USAGE = "usage"

#: Service CUR qui porte l'inférence Bedrock.
SERVICE_BEDROCK = "amazonbedrock"

#: Valeurs d'étiquette vides : la ligne est alors réputée non attribuée.
_ETIQUETTES_VIDES = {"", "-", "none", "null", "non-attribue", "non_attribué", "unallocated"}


def normaliser(nom: object) -> str:
    """Forme canonique d'un nom de colonne : minuscules, sans séparateur."""
    return re.sub(r"[^a-z0-9]", "", str(nom).lower())


_VARIANTES: dict[str, tuple[str, ...]] = {
    champ: tuple(normaliser(variante) for variante in variantes)
    for champ, variantes in CHAMPS_CUR.items()
}


@dataclass(frozen=True, slots=True)
class LigneCur:
    """Une ligne d'export CUR 2.0 retenue pour le rapprochement."""

    jour: date
    montant_usd: float
    etiquette: str | None = None
    type_ligne: str = TYPE_LIGNE_USAGE

    @property
    def attribuee(self) -> bool:
        """Vrai si la ligne porte une étiquette d'allocation exploitable."""
        return bool(self.etiquette) and self.etiquette.strip().lower() not in _ETIQUETTES_VIDES


@dataclass(slots=True)
class StatistiquesCur:
    """Compteurs de lecture d'un export CUR, pour l'audit et le support."""

    lues: int = 0
    retenues: int = 0
    ignorees_type: int = 0
    ignorees_illisibles: int = 0
    motifs: dict[str, int] = field(default_factory=dict)

    def ignorer(self, motif: str) -> None:
        """Compte une ligne écartée et son motif."""
        self.ignorees_illisibles += 1
        self.motifs[motif] = self.motifs.get(motif, 0) + 1


@dataclass(frozen=True, slots=True)
class FactureJour:
    """Coût facturé d'un jour, ventilé par étiquette d'allocation."""

    jour: date
    cout_total_usd: float
    cout_par_etiquette: dict[str, float]
    cout_non_attribue_usd: float

    @property
    def part_non_attribuee_pourcentage(self) -> float:
        """Part de la facture du jour sans étiquette d'allocation."""
        if self.cout_total_usd <= 0:
            return 0.0
        return round(min(self.cout_non_attribue_usd / self.cout_total_usd * 100.0, 100.0), 2)


def parser_ligne_cur(brut: object) -> LigneCur:
    """Convertit une ligne CUR brute en :class:`LigneCur`.

    Lève `ValidationError` si la ligne n'est pas un objet, ou si la date ou le
    montant est absent : une ligne qu'on ne sait pas dater ne peut pas entrer
    dans un rapprochement quotidien.
    """
    if not isinstance(brut, Mapping):
        raise ValidationError(f"ligne CUR non exploitable (type {type(brut).__name__})")
    champs = _aplatir_ligne(brut)
    jour = _jour(_valeur(champs, "jour"))
    if jour is None:
        raise ValidationError("ligne CUR sans date d'usage exploitable")
    montant = _montant(_valeur(champs, "montant"))
    if montant is None:
        raise ValidationError(f"ligne CUR sans montant exploitable (jour {jour})")
    return LigneCur(
        jour=jour,
        montant_usd=montant,
        etiquette=_etiquette(_valeur(champs, "etiquette")),
        type_ligne=(_valeur(champs, "type_ligne") or TYPE_LIGNE_USAGE).strip().lower(),
    )


def iterer_lignes_cur(
    source: Iterable[Mapping[str, object]],
    stats: StatistiquesCur | None = None,
    service: str | None = SERVICE_BEDROCK,
) -> Iterator[LigneCur]:
    """Parcourt des lignes CUR **en flux** et ne garde que la consommation Bedrock.

    `service=None` désactive le filtre de service, utile pour un export déjà
    restreint à Bedrock par le client.
    """
    statistiques = stats if stats is not None else StatistiquesCur()
    for brut in source:
        statistiques.lues += 1
        try:
            ligne = parser_ligne_cur(brut)
        except ValidationError as erreur:
            statistiques.ignorer(erreur.message)
            continue
        if ligne.type_ligne != TYPE_LIGNE_USAGE:
            statistiques.ignorees_type += 1
            continue
        if service and not _est_bedrock(brut, service):
            statistiques.ignorees_type += 1
            continue
        statistiques.retenues += 1
        yield ligne


def lire_cur(
    flux: IO[str],
    stats: StatistiquesCur | None = None,
    service: str | None = SERVICE_BEDROCK,
) -> Iterator[LigneCur]:
    """Raccourci : flux NDJSON d'export CUR → lignes retenues, en flux."""
    return iterer_lignes_cur(iterer_lignes_json(flux), stats=stats, service=service)


def iterer_lignes_json(flux: IO[str]) -> Iterator[Mapping[str, object]]:
    """Lit un export NDJSON, une ligne de coût par ligne de fichier."""
    for ligne in flux:
        contenu = ligne.strip()
        if not contenu:
            continue
        try:
            charge = json.loads(contenu)
        except json.JSONDecodeError:
            yield {}
            continue
        yield charge if isinstance(charge, Mapping) else {}


def facture_par_jour(lignes: Iterable[LigneCur]) -> dict[date, FactureJour]:
    """Consolide des lignes CUR en facture quotidienne par étiquette."""
    cumuls: dict[date, dict[str, float]] = {}
    totaux: dict[date, float] = {}
    for ligne in lignes:
        totaux[ligne.jour] = totaux.get(ligne.jour, 0.0) + ligne.montant_usd
        etiquette = ligne.etiquette if ligne.attribuee else None
        cle = etiquette or "non-attribue"
        cumuls.setdefault(ligne.jour, {})
        cumuls[ligne.jour][cle] = cumuls[ligne.jour].get(cle, 0.0) + ligne.montant_usd
    factures: dict[date, FactureJour] = {}
    for jour, total in totaux.items():
        par_etiquette = {cle: round(montant, 4) for cle, montant in cumuls[jour].items()}
        factures[jour] = FactureJour(
            jour=jour,
            cout_total_usd=round(total, 4),
            cout_par_etiquette=par_etiquette,
            cout_non_attribue_usd=par_etiquette.get("non-attribue", 0.0),
        )
    return factures


def rapprocher_journee(
    resultat: ResultatAgregation,
    facture: FactureJour,
    tenant_id: str,
) -> RapprochementJour:
    """Rapproche le coût recalculé d'un jour de la facture du même jour.

    La part non attribuée vient de deux sources complémentaires : les appels
    journalisés sans étiquette (calculés par `tags.py`) et les lignes CUR sans
    étiquette d'allocation, qui portent la dépense invisible des journaux
    (point d'accès `bedrock-mantle`). On retient la plus grande des deux pour ne
    pas sous-estimer le trou d'attribution.
    """
    cout_recalcule = resultat.par_jour().get(facture.jour, 0.0)
    causes = list(resultat.part_non_attribuee.causes)
    if facture.cout_non_attribue_usd > 0 and CAUSE_SANS_ETIQUETTE not in causes:
        causes.append(CAUSE_SANS_ETIQUETTE)
    non_attribue = max(resultat.cout_non_attribue_usd, facture.cout_non_attribue_usd)
    non_attribue = min(non_attribue, facture.cout_total_usd)
    return rapprocher_facture(
        tenant_id,
        facture.jour,
        cout_recalcule=cout_recalcule,
        cout_facture=facture.cout_total_usd,
        depense_non_attribuee=non_attribue,
        causes=causes,
    )


def rapprocher_periode(
    resultats: Iterable[ResultatAgregation],
    factures: Iterable[FactureJour],
    tenant_id: str,
) -> list[RapprochementJour]:
    """Rapproche plusieurs jours : un `RapprochementJour` par jour facturé.

    Un jour facturé sans agrégat (journaux pas encore arrivés, ou point d'accès
    non journalisé seul) est sauté : l'écart serait de 100 % et masquerait les
    vrais problèmes.
    """
    lectures = list(resultats)
    rapprochements: list[RapprochementJour] = []
    for facture in factures:
        agregat = _resultat_du_jour(lectures, facture.jour)
        if agregat is None:
            continue
        rapprochements.append(rapprocher_journee(agregat, facture, tenant_id))
    rapprochements.sort(key=lambda item: item.jour)
    return rapprochements


def _resultat_du_jour(resultats: list[ResultatAgregation], jour: date) -> ResultatAgregation | None:
    """Resultat d'agrégation restreint au jour demandé, ou `None` si vide."""
    for resultat in resultats:
        restreint = resultat.pour_jour(jour)
        if restreint is not None:
            return restreint
    return None


def _aplatir_ligne(brut: Mapping[str, object]) -> dict[str, object]:
    """Aplatit une ligne CUR : clés normalisées, dictionnaires imbriqués inclus."""
    champs: dict[str, object] = {}
    for cle, valeur in brut.items():
        champs.setdefault(normaliser(cle), valeur)
        if isinstance(valeur, Mapping):
            for sous_cle, sous_valeur in valeur.items():
                champs.setdefault(normaliser(sous_cle), sous_valeur)
                champs.setdefault(normaliser(f"{cle}{sous_cle}"), sous_valeur)
    return champs


def _valeur(champs: Mapping[str, object], champ: str) -> object | None:
    """Première variante connue d'un champ CUR, ou `None`."""
    for variante in _VARIANTES[champ]:
        if variante in champs and champs[variante] not in (None, ""):
            return champs[variante]
    return None


def _jour(valeur: object | None) -> date | None:
    """Date d'usage d'une ligne CUR (ISO 8601, `AAAA-MM-JJ`, epoch)."""
    if isinstance(valeur, datetime):
        return valeur.astimezone(UTC).date() if valeur.tzinfo else valeur.date()
    if isinstance(valeur, date):
        return valeur
    if valeur is None:
        return None
    texte = str(valeur).strip()
    try:
        return datetime.fromisoformat(texte.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(texte[:10])
    except ValueError:
        return None


def _montant(valeur: object | None) -> float | None:
    """Montant non remisé d'une ligne CUR, négatif accepté (crédits)."""
    if valeur is None:
        return None
    try:
        return float(str(valeur).replace(",", ".").replace("$", "").strip())
    except ValueError:
        return None


def _etiquette(valeur: object | None) -> str | None:
    """Étiquette d'allocation d'une ligne CUR, normalisée en identifiant."""
    if valeur is None:
        return None
    texte = str(valeur).strip()
    return texte or None


def _est_bedrock(brut: Mapping[str, object], service: str) -> bool:
    """Vrai si la ligne CUR concerne bien le service attendu (Bedrock)."""
    champs = _aplatir_ligne(brut)
    valeur = _valeur(champs, "service")
    if valeur is None:
        return True  # export déjà restreint : on ne rejette pas à l'aveugle
    return normaliser(valeur) == normaliser(service)


def lire_fichier_cur(chemin: Path, stats: StatistiquesCur | None = None) -> Iterator[LigneCur]:
    """Lit un export CUR NDJSON depuis le disque, en flux."""
    with chemin.open(encoding="utf-8") as flux:
        yield from lire_cur(flux, stats=stats)
