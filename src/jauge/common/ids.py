"""Identifiants déterministes et reproductibles.

Deux exigences du cahier des charges s'appuient sur ce module :
- le tirage d'échantillon reproductible (§13.1) : même graine → même échantillon ;
- la traçabilité (EF-ADM-02) : un identifiant se retrouve dans l'audit, les
  journaux AWS et le portail.

Aucun identifiant ne contient de donnée de requête (ENF-CONF-01).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime

PREFIXES = {
    "tenant": "t",
    "application": "app",
    "campaign": "c",
    "recommendation": "r",
    "rollout": "ro",
    "report": "rp",
    "audit": "a",
    "lever": "lv",
}


def aleatoire(entite: str) -> str:
    """Identifiant court et unique : `c-4f2a91`. Préfixe issu de `PREFIXES`."""
    prefixe = PREFIXES.get(entite, entite[:3])
    return f"{prefixe}-{secrets.token_hex(3)}"


def deterministic(*parties: object) -> str:
    """Empreinte stable des parties données (hexadécimal, 16 caractères).

    Sert aux clés d'agrégat (application|modèle|jour) et à l'identifiant d'une
    paire rejouée (requête|modèle|campagne), qui doit être recalculable.

    L'empreinte ne dépend que des parties fournies : elle est identique d'une
    machine à l'autre et d'une exécution à l'autre, ce qui permet de rejouer un
    tirage ou de retrouver un agrégat sans base de données partagée.
    """
    joint = "|".join(str(partie) for partie in parties)
    return hashlib.sha256(joint.encode("utf-8")).hexdigest()[:16]


def graine_de(seed: int, *contexte: object) -> int:
    """Graine 63 bits dérivée d'une graine utilisateur et d'un contexte.

    Permet de changer d'application à graine constante sans reproduire le même
    tirage, tout en restant reproductible (§13.1).
    """
    return int(deterministic(seed, *contexte), 16) % (2**63)


def jour_compact(moment: date | datetime) -> str:
    """Jour au format `AAAA-MM-JJ` (clé de partition des agrégats)."""
    return moment.strftime("%Y-%m-%d")


def horodatage_utc(moment: datetime) -> str:
    """Horodatage ISO-8601 en UTC, à la seconde (lisible en audit)."""
    return moment.astimezone().isoformat(timespec="seconds")
