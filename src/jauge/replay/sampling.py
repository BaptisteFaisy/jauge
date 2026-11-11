"""Tirage d'échantillon stratifié et reproductible d'une campagne de rejeu.

Exigences visées :
- EF-REJ-01 : tirer un échantillon **stratifié et reproductible** des requêtes
  d'une application ;
- §13.1 : 200 requêtes par application par défaut, tirées sur 14 jours,
  stratification par longueur de requête et par étiquette de type de requête,
  même graine ⇒ même échantillon, exclusion des requêtes marquées sensibles par
  le client ;
- ENF-CONF-01 : l'échantillon ne contient que des **métadonnées** (identifiant,
  type, longueur, jetons d'entrée). Aucun texte de requête n'entre ici.

La reproductibilité ne dépend ni de l'ordre d'arrivée des requêtes ni du hasard
global de Python : chaque strate reçoit une graine dérivée par
`jauge.common.ids.graine_de(graine, application_id, strate)` et le résultat est
trié. Deux exécutions avec la même graine rendent la même liste, dans le même
ordre.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from jauge.common.errors import ValidationError
from jauge.common.ids import graine_de
from jauge.models.campaign import EchantillonRequete

# §13.1 : 200 requêtes par application par défaut.
VOLUME_DEFAUT = 200
# Graine par défaut d'une campagne (`Campagne.graine_aleatoire`).
GRAINE_DEFAUT = 42
# Fenêtre de tirage recommandée par le cahier des charges (§13.1).
FENETRE_JOURS_DEFAUT = 14

# Bornes des strates de longueur, en caractères du texte de requête.
BORNE_COURT = 500
BORNE_MOYEN = 2000
STRATES_LONGUEUR = ("court", "moyen", "long")


@dataclass(frozen=True)
class RequeteSource:
    """Métadonnée d'une requête candidate au tirage (sortie de l'attribution).

    `sensible` porte le marquage du client (§13.1) : une requête marquée
    sensible n'entre jamais dans un échantillon de rejeu.
    """

    requete_id: str
    application_id: str
    type_requete: str = "general"
    longueur_caracteres: int = 0
    jetons_entree_origine: int = 0
    sensible: bool = False


@dataclass(frozen=True)
class Echantillon:
    """Résultat du tirage : les requêtes retenues et de quoi l'auditer."""

    application_id: str
    graine: int
    requetes: tuple[EchantillonRequete, ...] = ()
    exclusions_sensibles: int = 0
    hors_application: int = 0
    strates: dict[str, int] = field(default_factory=dict)

    @property
    def identifiants(self) -> list[str]:
        """Identifiants retenus, dans l'ordre stable du tirage."""
        return [requete.requete_id for requete in self.requetes]

    @property
    def taille(self) -> int:
        """Nombre de requêtes effectivement tirées."""
        return len(self.requetes)

    def jetons_entree_moyens(self) -> float:
        """Jetons d'entrée moyens de l'échantillon (base de l'estimation de coût)."""
        if not self.requetes:
            return 0.0
        total = sum(requete.jetons_entree_origine for requete in self.requetes)
        return total / len(self.requetes)


def classe_longueur(longueur_caracteres: int) -> str:
    """Strate de longueur d'une requête : `court`, `moyen` ou `long`."""
    if longueur_caracteres < BORNE_COURT:
        return "court"
    if longueur_caracteres < BORNE_MOYEN:
        return "moyen"
    return "long"


def nom_strate(longueur: str, type_requete: str) -> str:
    """Nom lisible d'une strate croisant longueur et type de requête (§13.1)."""
    return f"{longueur}:{type_requete}"


def _strate(requete: RequeteSource) -> str:
    return nom_strate(classe_longueur(requete.longueur_caracteres), requete.type_requete)


def _repartition_proportionnelle(comptes: dict[str, int], total: int) -> dict[str, int]:
    """Allocation au plus fort reste, en arithmétique entière (donc exacte).

    Chaque strate reçoit sa part proportionnelle ; les sièges restants vont aux
    plus forts restes, départagés par le nom de strate pour rester déterministe.
    """
    effectif = sum(comptes.values())
    if effectif == 0 or total <= 0:
        return dict.fromkeys(comptes, 0)
    quotas = {strate: (total * compte, compte) for strate, compte in comptes.items()}
    allocation = {strate: numerateur // effectif for strate, (numerateur, _) in quotas.items()}
    restes = {strate: numerateur % effectif for strate, (numerateur, _) in quotas.items()}
    sieges = total - sum(allocation.values())
    ordre = sorted(comptes, key=lambda strate: (-restes[strate], strate))
    for strate in ordre[:sieges]:
        allocation[strate] += 1
    return allocation


def _filtrer(
    requetes: Sequence[RequeteSource], application_id: str
) -> tuple[list[RequeteSource], int, int]:
    """Écarte les requêtes sensibles et celles d'une autre application."""
    retenues: list[RequeteSource] = []
    sensibles = 0
    etrangeres = 0
    for requete in requetes:
        if requete.application_id != application_id:
            etrangeres += 1
        elif requete.sensible:
            sensibles += 1
        else:
            retenues.append(requete)
    return retenues, sensibles, etrangeres


def _tirer_dans_strate(
    requetes: list[RequeteSource], combien: int, graine: int
) -> list[RequeteSource]:
    """Tire `combien` requêtes dans une strate, de façon reproductible."""
    if combien <= 0:
        return []
    # Le tri préalable rend le tirage indépendant de l'ordre d'arrivée.
    pool = sorted(requetes, key=lambda requete: requete.requete_id)
    generateur = random.Random(graine)
    return generateur.sample(pool, min(combien, len(pool)))


def _vers_echantillon_requete(requete: RequeteSource) -> EchantillonRequete:
    """Convertit une métadonnée source en entrée d'échantillon du socle."""
    return EchantillonRequete(
        requete_id=requete.requete_id,
        application_id=requete.application_id,
        type_requete=requete.type_requete,
        longueur_caracteres=requete.longueur_caracteres,
        jetons_entree_origine=requete.jetons_entree_origine,
        stratum=_strate(requete),
    )


def _grouper_par_strate(pool: list[RequeteSource]) -> dict[str, list[RequeteSource]]:
    """Regroupe les requêtes par strate (longueur × type)."""
    par_strate: dict[str, list[RequeteSource]] = {}
    for requete in pool:
        par_strate.setdefault(_strate(requete), []).append(requete)
    return par_strate


def _tirer_par_strate(
    par_strate: dict[str, list[RequeteSource]],
    allocation: dict[str, int],
    graine: int,
    application_id: str,
) -> list[RequeteSource]:
    """Tire chaque strate avec sa propre graine dérivée, puis ordonne le résultat."""
    retenues: list[RequeteSource] = []
    for strate in sorted(par_strate):
        graine_strate = graine_de(graine, application_id, strate)
        retenues.extend(_tirer_dans_strate(par_strate[strate], allocation[strate], graine_strate))
    # Ordre stable et lisible : strate croissante puis identifiant.
    retenues.sort(key=lambda requete: (_strate(requete), requete.requete_id))
    return retenues


def _compter_par_strate(echantillon: list[EchantillonRequete]) -> dict[str, int]:
    """Répartition effective de l'échantillon, pour l'audit (EF-ADM-02)."""
    repartition: dict[str, int] = {}
    for requete in echantillon:
        repartition[requete.stratum] = repartition.get(requete.stratum, 0) + 1
    return repartition


def tirer_echantillon(
    requetes: Sequence[RequeteSource],
    *,
    application_id: str,
    taille: int = VOLUME_DEFAUT,
    graine: int = GRAINE_DEFAUT,
) -> Echantillon:
    """Tire l'échantillon d'une application (§13.1, EF-REJ-01).

    Même `graine` et même `application_id` ⇒ même échantillon, dans le même
    ordre, quel que soit l'ordre des requêtes en entrée. Les requêtes marquées
    sensibles et celles d'une autre application sont écartées (comptées dans le
    résultat pour l'audit EF-ADM-02).
    """
    if taille <= 0:
        raise ValidationError(
            "La taille d'échantillon doit être strictement positive.", taille=taille
        )
    if not application_id:
        raise ValidationError("L'identifiant d'application est obligatoire pour un tirage.")

    pool, sensibles, etrangeres = _filtrer(requetes, application_id)
    if not pool:
        raise ValidationError(
            "Aucune requête tirable pour cette application.", application_id=application_id
        )

    par_strate = _grouper_par_strate(pool)
    comptes = {strate: len(lot) for strate, lot in par_strate.items()}
    allocation = _repartition_proportionnelle(comptes, min(taille, len(pool)))
    retenues = _tirer_par_strate(par_strate, allocation, graine, application_id)
    echantillon = [_vers_echantillon_requete(requete) for requete in retenues]

    return Echantillon(
        application_id=application_id,
        graine=graine,
        requetes=tuple(echantillon),
        exclusions_sensibles=sensibles,
        hors_application=etrangeres,
        strates=_compter_par_strate(echantillon),
    )
