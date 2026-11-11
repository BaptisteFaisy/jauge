"""Application de démonstration « classement de tickets » (client A).

Profil : le **même grand modèle** que l'assistant, alors qu'un petit suffirait
(§18.1). Les requêtes sont courtes et les sorties sont des JSON courts et
structurés : c'est le signal du levier « modèle surdimensionné » (Annexe A).

Aucun texte de ce module ne provient d'une donnée réelle.
"""

from __future__ import annotations

import json
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

CATEGORIES = (
    "paiement",
    "incident",
    "renouvellement",
    "facturation",
    "technique",
    "autre",
)

PRIORITES = ("basse", "normale", "haute", "critique")

CONSIGNE = (
    "Tu classes un ticket de support dans une seule catégorie parmi : "
    + ", ".join(CATEGORIES)
    + ". Tu réponds uniquement par un objet JSON avec les clés "
    '"categorie", "priorite" et "confiance".'
)

TICKETS_EXEMPLES = (
    "La carte bancaire a été refusée lors du dernier paiement.",
    "Le service est indisponible depuis ce matin pour toute l'équipe.",
    "Je souhaite stopper mon abonnement avant la fin du mois.",
    "La facture de mars comporte une ligne en double.",
    "Le mot de passe ne fonctionne plus après la réinitialisation.",
    "Comment ajouter un utilisateur supplémentaire sur mon compte ?",
    "Le remboursement annoncé n'est toujours pas crédité sur mon compte.",
    "Une latence importante est constatée sur les exports de données.",
    "La facture est émise au nom de l'ancienne société.",
    "Je ne parviens pas à téléverser ma pièce justificative.",
)


class TicketClassifier:
    """Classement de tickets : entrée courte, sortie JSON courte."""

    def __init__(
        self,
        client: ModeleClient,
        modele_id: str,
        application_id: str = "ticket-classifier",
        equipe: str = "support",
        environnement: str = "production",
        max_tokens: int = 256,
    ) -> None:
        self.client = client
        self.modele_id = modele_id
        self.max_tokens = max_tokens
        self.metadonnees = MetadonneesAppel(
            application_id=application_id,
            team=equipe,
            environment=environnement,
            type_requete="classification",
        )

    def construire_requete(
        self, ticket: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> RequeteModele:
        """Construit la requête courte : consigne + ticket (+ court contexte)."""
        contexte = (
            remplir_contexte(contexte_jetons, rnd or random.Random(0)) if contexte_jetons else ""
        )
        contenu = f"{contexte}\n{ticket}".strip() if contexte else ticket
        return RequeteModele(
            modele_id=self.modele_id,
            messages=(Message(role="user", texte=contenu),),
            systeme=(CONSIGNE,),
            parametres=ParametresRejeu(temperature=0.0, max_tokens=self.max_tokens),
            metadonnees=self.metadonnees,
        )

    def classer(
        self, ticket: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> dict[str, object]:
        """Classe un ticket et rend l'objet JSON décodé."""
        requete = self.construire_requete(ticket, contexte_jetons, rnd)
        reponse: ReponseModele = self.client.converse(requete)
        return json.loads(reponse.texte)

    @staticmethod
    def jetons_consigne() -> int:
        """Taille de la consigne système, en jetons."""
        return estimer_jetons(CONSIGNE)
