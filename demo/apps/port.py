"""Port de client de modèle et structures partagées par les applications démo.

Le port `ModeleClient` est volontairement minimal : une seule opération,
`converse`, qui reprend l'API Bedrock `Converse`. Il est écrit pour être
**compatible avec le port du module de rejeu (M4, `src/jauge/replay/client.py`)** :
si ce module existe sur la branche d'intégration, `demo/apps/port.py` devient le
double de test et les noms de champs doivent concorder :

    RequeteModele(modele_id, messages, systeme, parametres, metadonnees)
    ReponseModele(texte, modele_id, jetons_entree, jetons_sortie, latence_ms,
                  jetons_cache_lecture, jetons_cache_ecriture, stop_reason)
    ModeleClient.converse(requete: RequeteModele) -> ReponseModele

Les paramètres d'inférence réutilisent `ParametresRejeu` du socle : même objet,
même sémantique que la campagne de rejeu (§13.2).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from jauge.models.campaign import ParametresRejeu

# Phrases neutres servant à fabriquer un contexte factice de longueur donnée.
PHRASES_CONTEXTE = (
    "Le client a déjà contacté le support hier pour un dossier voisin.",
    "La référence de contrat associée est mentionnée dans la note interne.",
    "Un conseiller a ouvert un ticket précédent avec le même motif.",
    "Les conditions générales de l'offre prévoient un délai de traitement de cinq jours.",
    "Le dossier est rattaché au compte professionnel du demandeur.",
    "Une pièce jointe contient les informations nécessaires à la réponse.",
    "La dernière mise à jour du dossier date de la semaine précédente.",
    "Le tarif applicable dépend de la formule souscrite par le client.",
)


@dataclass(frozen=True)
class Message:
    """Un message de conversation : rôle (`user`/`assistant`) et texte."""

    role: str
    texte: str


@dataclass(frozen=True)
class MetadonneesAppel:
    """Étiquettes d'attribution attachées à l'appel (§8.1, EF-ATT-03).

    Elles apparaissent dans `requestMetadata` du journal d'invocation et
    permettent de rattacher la requête à une application et à une équipe.
    """

    application_id: str
    team: str
    environment: str
    type_requete: str = "general"


@dataclass(frozen=True)
class RequeteModele:
    """Requête d'inférence complète, prête à être envoyée au modèle."""

    modele_id: str
    messages: tuple[Message, ...]
    systeme: tuple[str, ...] = ()
    parametres: ParametresRejeu = field(default_factory=ParametresRejeu)
    metadonnees: MetadonneesAppel = field(default_factory=lambda: MetadonneesAppel("", "", ""))


@dataclass(frozen=True)
class ReponseModele:
    """Réponse du modèle : texte rendu, jetons consommés et latence."""

    texte: str
    modele_id: str
    jetons_entree: int
    jetons_sortie: int
    latence_ms: int
    jetons_cache_lecture: int = 0
    jetons_cache_ecriture: int = 0
    stop_reason: str = "end_turn"


@runtime_checkable
class ModeleClient(Protocol):
    """Port minimal vers un modèle (compatible M4)."""

    def converse(self, requete: RequeteModele) -> ReponseModele: ...


def estimer_jetons(texte: str) -> int:
    """Estime le nombre de jetons d'un texte (~4 caractères par jeton).

    Estimation volontairement grossière et **déterministe** : aucun
    tokeniseur réel n'est disponible hors ligne, et la démo n'a pas besoin
    d'une tokenisation exacte.
    """
    return max(1, math.ceil(len(texte) / 4))


def remplir_contexte(jetons_cible: int, rnd: random.Random) -> str:
    """Fabrique un contexte factice d'environ `jetons_cible` jetons.

    Le texte est composé de phrases neutres, jamais de donnée réelle.
    """
    if jetons_cible <= 0:
        return ""
    phrases: list[str] = []
    total = 0
    while total < jetons_cible:
        phrase = PHRASES_CONTEXTE[rnd.randrange(len(PHRASES_CONTEXTE))]
        phrases.append(phrase)
        total += estimer_jetons(phrase)
    return " ".join(phrases)
