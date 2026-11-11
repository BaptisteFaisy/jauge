"""Application de démonstration « assistant de support » (client A).

Profil : un **grand** modèle, des requêtes longues et des réponses rédigées,
des latences élevées (§18.1). Le préfixe système est long et identique à
chaque appel : c'est le signal du levier « cache de prompts absent »
(Annexe A).

Aucun texte de ce module ne provient d'une donnée réelle : les questions, la
politique et les réponses sont synthétiques.
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

# Politique interne synthétique : le même préfixe à chaque appel (~900 jetons).
PARAGRAPHES_POLITIQUE = (
    "Tu es l'assistant de support d'un fournisseur de services numériques. Tu "
    "réponds en français, avec un ton professionnel et factuel, sans promettre "
    "ce que la politique de l'entreprise ne prévoit pas.",
    "Tu ne donnes jamais de conseil juridique, médical ou financier. En cas de "
    "question relevant de l'un de ces domaines, tu renvoies vers "
    "l'interlocuteur compétent et tu indiques au client la procédure de "
    "contact.",
    "Avant de répondre, tu vérifies que la demande porte bien sur un produit "
    "couvert par le catalogue en vigueur et que le client dispose d'un contrat "
    "actif. Si l'une de ces deux conditions n'est pas remplie, tu le dis "
    "clairement et tu proposes la marche à suivre.",
    "Si la demande concerne un remboursement, tu rappelles les conditions de "
    "remboursement, le délai contractuel de traitement et les pièces à "
    "fournir. Tu précises que le remboursement est effectué sur le moyen de "
    "paiement d'origine, sauf accord contraire du client.",
    "Si la demande concerne une résiliation, tu expliques les engagements en "
    "cours, la date d'effet possible et les conséquences sur les services "
    "facturés au titre de la période d'engagement. Tu ne confirmes jamais une "
    "date d'effet qui n'apparaît pas dans le dossier.",
    "Si la demande est hors périmètre, tu l'indiques, tu ne developpes pas de "
    "réponse approximative et tu proposes de la transmettre au service "
    "concerné. Un renvoi argumenté vaut mieux qu'une réponse inventée.",
    "Tu cites les conditions générales uniquement lorsque la question porte "
    "dessus, et tu précises l'article concerné. Tu n'avances aucun chiffre qui "
    "ne figure pas dans les informations dont tu disposes.",
    "Tu ne donnes pas d'indication sur un autre client, un autre compte ou un "
    "autre dossier, même lorsque la demande semble légitime. Cette règle "
    "prime sur la satisfaction immédiate du demandeur.",
    "Tu termines ta réponse par la prochaine étape concrète pour le client : "
    "la pièce à envoyer, le délai attendu, le canal à utiliser. Une réponse "
    "sans prochaine étape est incomplète.",
    "Tu limites ta réponse à l'essentiel et tu évites les redites. Les "
    "formules de politesse sont limitées à une formule d'ouverture et une "
    "formule de clôture.",
    "Tu ne mentionnes jamais le modèle qui produit ta réponse, ni les limites "
    "techniques du service. Tu n'évoques pas les autres conversationnaires ni "
    "les autres clients assistés par l'outil.",
    "En cas d'ambiguïté, tu demandes une précision avant de répondre. Une "
    "question de clarification vaut mieux qu'une hypothèse non fondée sur un "
    "dossier client.",
)

QUESTIONS_EXEMPLES = (
    "Comment se passe le remboursement d'un abonnement annuel ?",
    "Puis-je changer de formule en cours d'année ?",
    "Quels justificatifs sont demandés pour une résiliation ?",
    "Le service est-il disponible en dehors des heures ouvrées ?",
    "Comment mettre à jour l'adresse de facturation du compte ?",
    "Quels moyens de paiement sont acceptés pour le renouvellement ?",
    "Une panne déclarée hier est-elle déjà traitée par l'équipe technique ?",
    "Le contrat peut-il être transféré vers une autre entité ?",
)

PREFIXE_SYSTEME = "\n\n".join(PARAGRAPHES_POLITIQUE)


class SupportAssistant:
    """Assistant de support : longues entrées, réponses rédigées."""

    def __init__(
        self,
        client: ModeleClient,
        modele_id: str,
        application_id: str = "support-assistant",
        equipe: str = "support",
        environnement: str = "production",
        max_tokens: int = 4096,
    ) -> None:
        self.client = client
        self.modele_id = modele_id
        self.max_tokens = max_tokens
        self.metadonnees = MetadonneesAppel(
            application_id=application_id,
            team=equipe,
            environment=environnement,
            type_requete="question",
        )

    def construire_requete(
        self, question: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> RequeteModele:
        """Construit la requête : politique longue + contexte + question."""
        contexte = (
            remplir_contexte(contexte_jetons, rnd or random.Random(0)) if contexte_jetons else ""
        )
        contenu = f"{contexte}\n\n{question}".strip() if contexte else question
        return RequeteModele(
            modele_id=self.modele_id,
            messages=(Message(role="user", texte=contenu),),
            systeme=(PREFIXE_SYSTEME,),
            parametres=ParametresRejeu(temperature=0.2, max_tokens=self.max_tokens),
            metadonnees=self.metadonnees,
        )

    def repondre(
        self, question: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> str:
        """Envoie la requête au client de modèle et rend le texte de la réponse."""
        requete = self.construire_requete(question, contexte_jetons, rnd)
        reponse: ReponseModele = self.client.converse(requete)
        return reponse.texte

    @staticmethod
    def jetons_politique() -> int:
        """Taille du préfixe système, en jetons (levier « cache de prompts »)."""
        return estimer_jetons(PREFIXE_SYSTEME)
