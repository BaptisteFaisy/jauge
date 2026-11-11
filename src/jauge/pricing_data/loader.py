"""Chargeur de la table des tarifs datée (EF-ATT-04, §14, §23 Q3).

La table est un paramètre daté, tenu hors du code : un changement de tarif crée
une nouvelle ligne avec sa date d'effet, sans recalculer les coûts passés
(Annexe D). Ce module ne calcule aucun prix : il lit un fichier JSON, le
valide, et rend un `jauge.engine.calculator.TableTarifs` — la recherche du tarif
applicable et la formule de coût restent celles du socle (zéro duplication).

Toute donnée invalide lève `jauge.common.errors.ValidationError` avec un message
qui désigne la ligne fautive : une table silencieusement fausse fausserait le
rapprochement avec la facture (EF-ATT-06).

Format attendu (voir `data/pricing/bedrock-eu-west-1.json`) ::

    {
      "region": "eu-west-1",            # défaut pour les lignes
      "devise": "USD",                  # défaut pour les lignes
      "tarifs": [
        {"modele_id": "...", "niveau": "standard", "date_effet": "2026-01-01",
         "prix_entree_1k": 0.015, "prix_sortie_1k": 0.075,
         "prix_cache_ecriture_1k": 0.01875, "prix_cache_lecture_1k": 0.0015}
      ]
    }
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError as ErreurPydantic

from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import ServiceTier, TarifModele

#: Clés de prix attendues dans une ligne, avec leur nom de champ côté modèle.
CHAMPS_PRIX: tuple[tuple[str, str], ...] = (
    ("prix_entree_1k", "prix d'entrée"),
    ("prix_sortie_1k", "prix de sortie"),
    ("prix_cache_ecriture_1k", "prix d'écriture de cache"),
    ("prix_cache_lecture_1k", "prix de lecture de cache"),
)

#: Prix facultatifs (défaut 0.0 côté modèle) — les autres sont obligatoires.
PRIX_FACULTATIFS = frozenset({"prix_cache_ecriture_1k", "prix_cache_lecture_1k"})


def charger_table(chemin: str | Path) -> TableTarifs:
    """Charge, valide et rend la table des tarifs d'un fichier JSON."""
    return TableTarifs(charger_tarifs(chemin))


def charger_tarifs(chemin: str | Path) -> list[TarifModele]:
    """Charge et valide les lignes de tarif d'un fichier JSON."""
    document = _lire_json(chemin)
    region_defaut, devise_defaut = _defauts(document, chemin)
    brutes = document.get("tarifs")
    if not isinstance(brutes, list) or not brutes:
        raise ValidationError(
            "la table des tarifs doit contenir une liste « tarifs » non vide",
            chemin=str(chemin),
        )
    tarifs = [
        _construire_ligne(brut, rang + 1, region_defaut, devise_defaut, chemin)
        for rang, brut in enumerate(brutes)
    ]
    _refuser_doublons(tarifs, chemin)
    return tarifs


def lignes(table: TableTarifs) -> list[TarifModele]:
    """Liste des lignes d'une `TableTarifs` (lecture seule, copie défensive).

    Le socle (`jauge.engine.calculator`) n'expose pas d'accesseur public : ce
    module lit l'attribut privé et le signale comme besoin de socle (une
    propriété `tarifs` ou un `__iter__` suffirait).
    """
    return list(table._tarifs)  # noqa: SLF001 — besoin de socle signalé en PR


def _lire_json(chemin: str | Path) -> dict[str, Any]:
    """Lit le fichier et vérifie qu'il contient un objet JSON."""
    fichier = Path(chemin)
    if not fichier.is_file():
        raise ValidationError(
            f"table de tarifs introuvable : {fichier}",
            chemin=str(fichier),
        )
    try:
        contenu = json.loads(fichier.read_text(encoding="utf-8"))
    except json.JSONDecodeError as erreur:
        raise ValidationError(
            f"JSON invalide dans la table de tarifs {fichier} : {erreur}",
            chemin=str(fichier),
            position=erreur.pos,
        ) from erreur
    if not isinstance(contenu, dict):
        raise ValidationError(
            f"la table de tarifs {fichier} doit être un objet JSON",
            chemin=str(fichier),
        )
    return contenu


def _defauts(document: dict[str, Any], chemin: str | Path) -> tuple[str, str]:
    """Région et devise par défaut, appliquées aux lignes qui les omettent."""
    region = _texte_ou_none(document.get("region"))
    devise = _texte_ou_none(document.get("devise"))
    if region is None:
        raise ValidationError(
            "la table des tarifs doit déclarer une « region » ou une région par ligne",
            chemin=str(chemin),
        )
    return region, devise or ""


def _construire_ligne(
    brut: Any, rang: int, region_defaut: str, devise_defaut: str, chemin: str | Path
) -> TarifModele:
    """Valide une ligne brute et la convertit en `TarifModele`."""
    if not isinstance(brut, dict):
        raise _erreur(rang, "n'est pas un objet JSON", chemin)
    modele_id = _texte_ou_none(brut.get("modele_id"))
    if modele_id is None:
        raise _erreur(rang, "n'a pas de « modele_id » exploitable", chemin)
    region = _texte_ou_none(brut.get("region")) or region_defaut
    devise = _texte_ou_none(brut.get("devise")) or devise_defaut
    if not devise:
        raise _erreur(rang, "n'a pas de « devise » (ni ligne ni fichier)", chemin)
    niveau = _niveau(brut.get("niveau"), rang, chemin)
    date_effet = _date_effet(brut.get("date_effet"), rang, chemin)
    prix = {champ: _montant(brut, champ, rang, chemin) for champ, _ in CHAMPS_PRIX}
    try:
        return TarifModele(
            modele_id=modele_id,
            region=region,
            niveau=niveau,
            date_effet=date_effet,
            devise=devise,
            **prix,
        )
    except ErreurPydantic as erreur:  # garde-fou : le modèle refuse aussi les valeurs
        raise _erreur(
            rang, f"est refusée par le modèle ({erreur.error_count()} champ(s))", chemin
        ) from erreur


def _niveau(brut: Any, rang: int, chemin: str | Path) -> ServiceTier:
    """Niveau de service ; « standard » par défaut, valeur inconnue refusée."""
    if brut is None:
        return ServiceTier.STANDARD
    texte = _texte_ou_none(brut)
    if texte is None:
        raise _erreur(rang, "a un « niveau » qui n'est pas une chaîne", chemin)
    try:
        return ServiceTier(texte)
    except ValueError as erreur:
        autorises = ", ".join(niveau.value for niveau in ServiceTier)
        raise _erreur(
            rang, f"a un niveau « {texte} » inconnu (attendu : {autorises})", chemin
        ) from erreur


def _date_effet(brut: Any, rang: int, chemin: str | Path) -> date:
    """Date d'effet au format AAAA-MM-JJ, obligatoire."""
    texte = _texte_ou_none(brut)
    if texte is None:
        raise _erreur(rang, "n'a pas de « date_effet » (AAAA-MM-JJ)", chemin)
    try:
        return date.fromisoformat(texte)
    except ValueError as erreur:
        raise _erreur(
            rang, f"a une date d'effet mal formée « {texte} » (attendu AAAA-MM-JJ)", chemin
        ) from erreur


def _montant(ligne: dict[str, Any], champ: str, rang: int, chemin: str | Path) -> float:
    """Montant numérique fini et positif ou nul ; absence tolérée si facultatif."""
    if champ not in ligne:
        if champ in PRIX_FACULTATIFS:
            return 0.0
        libelle = dict(CHAMPS_PRIX)[champ]
        raise _erreur(rang, f"n'a pas de « {champ} » ({libelle})", chemin)
    valeur = ligne[champ]
    if isinstance(valeur, bool) or not isinstance(valeur, int | float):
        raise _erreur(rang, f"a un « {champ} » non numérique ({valeur!r})", chemin)
    nombre = float(valeur)
    if not math.isfinite(nombre):
        raise _erreur(rang, f"a un « {champ} » non fini ({valeur!r})", chemin)
    if nombre < 0:
        raise _erreur(rang, f"a un « {champ} » négatif ({nombre})", chemin)
    return nombre


def _refuser_doublons(tarifs: list[TarifModele], chemin: str | Path) -> None:
    """Refuse deux lignes de même (modèle, région, niveau, date d'effet)."""
    vues: set[tuple[str, str, ServiceTier, date]] = set()
    for tarif in tarifs:
        cle = (tarif.modele_id, tarif.region, tarif.niveau, tarif.date_effet)
        if cle in vues:
            raise ValidationError(
                "ligne de tarif en double pour "
                f"({tarif.modele_id}, {tarif.region}, {tarif.niveau.value}, "
                f"{tarif.date_effet.isoformat()})",
                chemin=str(chemin),
                modele=tarif.modele_id,
                region=tarif.region,
                niveau=tarif.niveau.value,
                date_effet=tarif.date_effet.isoformat(),
            )
        vues.add(cle)


def _texte_ou_none(valeur: Any) -> str | None:
    """Chaîne non vide, sinon `None` (les blancs sont considérés absents)."""
    if isinstance(valeur, str) and valeur.strip():
        return valeur.strip()
    return None


def _erreur(rang: int, message: str, chemin: str | Path) -> ValidationError:
    """Erreur de validation désignant la ligne fautive (1 = première ligne)."""
    return ValidationError(
        f"ligne de tarif n°{rang} {message}",
        chemin=str(chemin),
        ligne=rang,
    )
