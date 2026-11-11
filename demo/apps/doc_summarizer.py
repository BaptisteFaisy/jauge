"""Application de démonstration « résumé de documents » (client A).

Profil : un traitement **nocturne** resté au niveau de service **Standard**
(§18.1) — c'est le signal du levier « traitement différable » (Annexe A).
Les sorties sont par ailleurs anormalement longues, proches de la limite de
jetons — signal du levier « sorties trop longues ».

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
    "Tu résumes un document interne en français. Le résumé comporte cinq "
    "paragraphes, couvre les faits, les décisions et les échéances, et ne "
    "contient aucune recommandation."
)

PARAGRAPHES_EXEMPLES = (
    "Le présent document décrit le bilan d'activité de l'exercice écoulé pour "
    "l'ensemble des services internes.",
    "Les volumes traités ont progressé de huit pour cent par rapport à "
    "l'année précédente, sans modification de l'organisation.",
    "Trois chantiers prioritaires ont été livrés au cours du premier semestre, "
    "dont la refonte du suivi des indicateurs.",
    "Les délais de traitement ont été réduits d'un quart après la bascule du "
    "référentiel documentaire.",
    "Les risques identifiés portent principalement sur la saisonnalité de "
    "l'activité et sur le renouvellement des contrats-cadres.",
    "Les décisions prises en comité consistent à maintenir les effectifs et à "
    "planifier la consolidation des outils de mesure.",
    "Les échéances de fin d'année restent inchangées pour les engagements de "
    "niveau de service les plus exigeants.",
    "Les perspectives annoncées pour le prochain exercice portent sur "
    "l'industrialisation de la collecte automatisée.",
)

TITRES_EXEMPLES = (
    "Bilan d'activité — exercice précédent",
    "Revue de service — direction opérationnelle",
    "Synthèse trimestrielle des engagements",
    "Rapport annuel de conformité",
    "Note de cadrage du plan d'action",
)


class DocSummarizer:
    """Résumé de documents : longues entrées, longues sorties, nuit."""

    def __init__(
        self,
        client: ModeleClient,
        modele_id: str,
        application_id: str = "doc-summarizer",
        equipe: str = "back-office",
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
            type_requete="resume",
        )

    def construire_requete(
        self, document: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> RequeteModele:
        """Construit la requête : document long (+ contexte de remplissage)."""
        contexte = (
            remplir_contexte(contexte_jetons, rnd or random.Random(0)) if contexte_jetons else ""
        )
        contenu = f"{contexte}\n\n{document}".strip() if contexte else document
        return RequeteModele(
            modele_id=self.modele_id,
            messages=(Message(role="user", texte=contenu),),
            systeme=(CONSIGNE,),
            parametres=ParametresRejeu(temperature=0.1, max_tokens=self.max_tokens),
            metadonnees=self.metadonnees,
        )

    def resumer(
        self, document: str, contexte_jetons: int = 0, rnd: random.Random | None = None
    ) -> str:
        """Envoie le document au client de modèle et rend le résumé."""
        requete = self.construire_requete(document, contexte_jetons, rnd)
        reponse: ReponseModele = self.client.converse(requete)
        return reponse.texte

    @staticmethod
    def jetons_consigne() -> int:
        """Taille de la consigne système, en jetons."""
        return estimer_jetons(CONSIGNE)
