"""Interprétation des valeurs d'un journal d'invocation (§3.2).

Un enregistrement de journal arrive en texte : `"flex"`, `"2026-11-09T08:12:00Z"`,
`"bedrock-mantle"`, `"true"`. Ce module traduit ces valeurs vers les types du
domaine — niveau de service, instant UTC, point d'accès — en restant tolérant :
une valeur inattendue retombe sur une valeur sûre plutôt que de lever.

Trois décisions de la spécification sont appliquées ici :

- **niveaux de service** (§3.2) : Reserved, Priority, Standard, Flex. Un niveau
  non reconnu vaut Standard, qui est le tarif de repli de la table (EF-ATT-04) :
  facturer un niveau inventé serait pire que facturer le standard ;
- **points d'accès** (§3.2) : seuls les appels `bedrock-runtime` sont journalisés.
  Un point d'accès non reconnu est traité comme **non journalisé**, donc signalé
  comme cause d'écart (EF-ATT-07) plutôt que supposé présent ;
- **horodatages** : ISO-8601 ou epoch (secondes ou millisecondes), toujours
  convertis en UTC — un journal sans fuseau est réputé UTC, jamais en heure
  locale (une heure locale décalerait le rattachement au jour, donc l'agrégat).
"""

from __future__ import annotations

from datetime import UTC, datetime

from jauge.attribution.champs import normaliser_cle
from jauge.attribution.tags import PointAcces
from jauge.models.pricing import ServiceTier

#: Niveaux de service reconnus (§3.2) ; tout le reste vaut Standard.
NIVEAUX: dict[str, ServiceTier] = {
    "standard": ServiceTier.STANDARD,
    "ondemand": ServiceTier.STANDARD,
    "default": ServiceTier.STANDARD,
    "flex": ServiceTier.FLEX,
    "priority": ServiceTier.PRIORITY,
    "reserved": ServiceTier.RESERVED,
    "provisioned": ServiceTier.RESERVED,
    "reservedthroughput": ServiceTier.RESERVED,
}

#: Opérations du point d'accès `bedrock-runtime`, seules journalisées (§3.2).
OPERATIONS_RUNTIME = (
    "converse",
    "conversestream",
    "invokemodel",
    "invokemodelwithresponsestream",
)

#: Marqueurs d'un appel passé par `bedrock-mantle` (projets et espaces de travail).
MARQUEURS_MANTLE = ("mantle", "project", "workspace")

#: Au-delà de cette valeur, un epoch est en millisecondes (≈ mars 5138 en secondes).
SEUIL_MILLISECONDES = 1e11


def determiner_niveau_service(valeur: object) -> ServiceTier:
    """Niveau de service journalisé ; Standard par défaut (§3.2)."""
    if valeur is None:
        return ServiceTier.STANDARD
    return NIVEAUX.get(normaliser_cle(valeur), ServiceTier.STANDARD)


def determiner_point_acces(valeur: object) -> PointAcces:
    """Point d'accès d'un appel : `bedrock-mantle` n'est pas journalisé (§3.2)."""
    if valeur is None:
        return PointAcces.RUNTIME  # journal d'invocation ⇒ bedrock-runtime
    texte = normaliser_cle(valeur)
    if not texte:
        return PointAcces.RUNTIME
    if any(marqueur in texte for marqueur in MARQUEURS_MANTLE):
        return PointAcces.MANTLE
    if "runtime" in texte or texte in OPERATIONS_RUNTIME:
        return PointAcces.RUNTIME
    return PointAcces.INCONNU


def lire_horodatage(valeur: object) -> datetime | None:
    """Horodatage ISO-8601 ou epoch (s ou ms) converti en `datetime` UTC."""
    if isinstance(valeur, datetime):
        return valeur if valeur.tzinfo else valeur.replace(tzinfo=UTC)
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, int | float):
        return _depuis_epoch(float(valeur))
    if not isinstance(valeur, str) or not valeur.strip():
        return None
    contenu = valeur.strip()
    try:
        return _depuis_epoch(float(contenu))
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(contenu.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _depuis_epoch(valeur: float) -> datetime | None:
    """Epoch en secondes ou millisecondes → `datetime` UTC (`None` si absurde)."""
    secondes = valeur / 1000.0 if valeur > SEUIL_MILLISECONDES else valeur
    try:
        return datetime.fromtimestamp(secondes, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
