"""Application de démonstration « recherche documentaire » (client B).

Profil : recherche dans la base de connaissances, requêtes courtes, réponses
courtes, heures ouvrées. Elle existe pour la **vue parc** : le MSP montre deux
clients côte à côte (§18.1). Aucun levier n'est attendu chez ce client, qui
n'a souscrit que la formule de visibilité.

Aucun texte de ce module ne provient d'une donnée réelle.
"""

from __future__ import annotations

import random

from demo.apps.port import (
    Message,
    MetadonneesAppel,
    ModeleClient,
    ParametresRejeu,
    ReponseModele,
    RequeteModele,
    estimer_jetons,
    remplir_contexte,
)

CONSIGNE = (
    "Tu réponds à une question à partir de la base de connaissances interne. "
    "Tu cites les documents utilisés et tu indiques clairement lorsqu'aucun "
    "document ne répond à la question."
)

QUESTIONS_EXEMPLES = (
    "Quelle est la procédure de remboursement d'un abonnement ?",
    "Où trouver le référentiel des engagements de service ?",
    "Quel est le délai de traitement d'une demande de support ?",
    "Comment mettre en service un nouvel espace client ?",
)


class KbSearch:
    """Recherche documentaire : entrée courte, réponse courte et citée."""

    def __init__(
        self,
        client: ModeleClient,
        modele_id: str,
        application_id: str = "kb-search",
        equipe: str = "documentation",
        environnement: str = "production",
        max_tokens: int = 2048,
    ) -> None:
        self.client = client
        self.modele_id = modele_id
        self.max_tokens = max_tokens
        self.metadonnees = MetadonneesAppel(
            application_id=application_id,
            team=equipe,
            environment=environnement,
            type_requete="recherche",
        )

    def construire_requete(
        self, question: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> RequeteModele:
        """Construit la requête : consigne courte + extraits + question."""
        contexte = (
            remplir_contexte(contexte_jetons, rnd or random.Random(0)) if contexte_jetons else ""
        )
        contenu = f"{contexte}\n\n{question}".strip() if contexte else question
        return RequeteModele(
            modele_id=self.modele_id,
            messages=(Message(role="user", texte=contenu),),
            systeme=(CONSIGNE,),
            parametres=ParametresRejeu(temperature=0.0, max_tokens=self.max_tokens),
            metadonnees=self.metadonnees,
        )

    def chercher(
        self, question: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> str:
        """Envoie la question au client de modèle et rend la réponse citée."""
        requete = self.construire_requete(question, contexte_jetons, rnd)
        reponse: ReponseModele = self.client.converse(requete)
        return reponse.texte

    @staticmethod
    def jetons_consigne() -> int:
        """Taille de la consigne système, en jetons."""
        return estimer_jetons(CONSIGNE)
