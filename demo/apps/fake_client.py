"""Client de modèle hors ligne, déterministe (aucun appel réseau).

`FakeModeleClient` implémente le port `ModeleClient` : il produit des réponses
synthétiques à partir du profil de l'application (voir `demo/profils.py`). Il
sert à trois usages :

1. faire tourner les trois applications de démonstration sans compte AWS ;
2. produire les corps de réponse des journaux d'invocation synthétiques ;
3. servir de double de test pour le port du module de rejeu (M4).

Déterminisme : la graine d'une réponse est dérivée du contenu de la requête
(`graine_de` du socle), donc deux appels identiques donnent la même réponse.
"""

from __future__ import annotations

import json
import random

from demo.apps.port import (
    ReponseModele,
    RequeteModele,
    estimer_jetons,
)
from demo.apps.ticket_classifier import CATEGORIES, PRIORITES
from demo.profils import PROFILS, ProfilApplication
from jauge.common.ids import graine_de

PHRASES_REPONSE = (
    "Bonjour, j'ai bien pris connaissance de votre demande.",
    "La situation est couverte par les conditions générales de l'offre en vigueur.",
    "Le traitement est effectué sous cinq jours ouvrés à compter de la réception du dossier complet.",
    "Si une pièce justificative manque, le dossier reste en attente jusqu'à sa transmission.",
    "Une confirmation vous est adressée par courriel une fois l'opération effectuée.",
    "Aucun frais supplémentaire n'est facturé pour cette demande.",
    "Le service client reste disponible pour tout complément d'information.",
    "Vous pouvez suivre l'avancement depuis votre espace client.",
)

PHRASES_RESUME = (
    "Le document présente une activité stable sur le premier semestre, portée par "
    "une croissance modérée des volumes traités.",
    "Les principaux indicateurs ont été consolidés selon la nouvelle méthode de "
    "mesure arrêtée en début d'année.",
    "Les décisions structurantes concernent l'organisation du support et le "
    "calendrier des livraisons de fin d'exercice.",
    "Les risques identifiés restent maîtrisés et font l'objet d'un suivi "
    "mensuel par les équipes concernées.",
    "Les échéances contractuelles les plus exigeantes sont tenues sans "
    "décalage par rapport au calendrier initial.",
    "Les perspectives évoquent une consolidation des outils de mesure et une "
    "automatisation partielle de la collecte.",
    "Les moyens engagés sur l'exercice sont reconduits à l'identique pour la période suivante.",
)

PARAGRAPHE_VIDE = (
    "Ce document ne contient pas d'information exploitable au-delà des éléments "
    "déjà repris ci-dessus."
)


class FakeModeleClient:
    """Client de modèle hors ligne : mêmes règles que le port, zéro réseau."""

    def __init__(self, niveau_service: str | None = None) -> None:
        self.niveau_service = niveau_service

    # --- API du port --------------------------------------------------------
    def converse(self, requete: RequeteModele) -> ReponseModele:
        """Produit une réponse synthétique déterministe pour la requête."""
        rnd = random.Random(
            graine_de(0, "demo", requete.metadonnees.application_id, requete.messages[0].texte)
        )
        profil = PROFILS.get(requete.metadonnees.application_id)
        texte = self._texte(requete, profil, rnd)
        jetons_entree = estimer_jetons(self._contenu_entree(requete))
        latence = self._latence(profil, rnd)
        return ReponseModele(
            texte=texte,
            modele_id=requete.modele_id,
            jetons_entree=jetons_entree,
            jetons_sortie=estimer_jetons(texte),
            latence_ms=latence,
            jetons_cache_lecture=0,
            jetons_cache_ecriture=0,
        )

    # --- Interne ------------------------------------------------------------
    @staticmethod
    def _contenu_entree(requete: RequeteModele) -> str:
        """Texte transmis au modèle (système + messages), pour le compte de jetons."""
        morceaux = list(requete.systeme) + [m.texte for m in requete.messages]
        return "\n".join(morceaux)

    def _texte(
        self, requete: RequeteModele, profil: ProfilApplication | None, rnd: random.Random
    ) -> str:
        """Choisit la stratégie de réponse selon l'application."""
        application = requete.metadonnees.application_id
        if application == "ticket-classifier":
            return self._json_classification(requete.messages[0].texte, rnd)
        if application == "doc-summarizer":
            cible = self._cible(profil, rnd, minimum=600)
            return self._remplir(PHRASES_RESUME, cible, rnd)
        if application == "support-assistant":
            cible = self._cible(profil, rnd, minimum=120)
            return self._reponse_redigee(requete.messages[0].texte, cible, rnd)
        if application == "kb-search":
            cible = self._cible(profil, rnd, minimum=60)
            return self._reponse_recherche(cible, rnd)
        return self._remplir(PHRASES_REPONSE, 120, rnd)

    @staticmethod
    def _reponse_recherche(jetons_cible: int, rnd: random.Random) -> str:
        """Réponse de recherche documentaire : une courte liste de résultats."""
        titres = (
            "Procédure de remboursement des abonnements",
            "Conditions générales de l'offre professionnelle",
            "Guide de mise en service de l'espace client",
            "Délais de traitement des demandes de support",
            "Référentiel des engagements de niveau de service",
        )
        morceaux = ["Trois documents pertinents ont été trouvés :"]
        for titre in titres[:3]:
            morceaux.append(f"- {titre}")
        complement = FakeModeleClient._remplir(PHRASES_REPONSE, jetons_cible, rnd)
        return " ".join(morceaux) + " " + complement

    @staticmethod
    def _cible(profil: ProfilApplication | None, rnd: random.Random, minimum: int) -> int:
        """Nombre de jetons visé pour la réponse, dans la plage du profil."""
        if profil is None:
            return minimum
        bas, haut = profil.jetons_sortie
        return rnd.randint(max(bas, minimum), max(haut, minimum))

    @staticmethod
    def _remplir(phrases: tuple[str, ...], jetons_cible: int, rnd: random.Random) -> str:
        """Répète des phrases jusqu'à atteindre la taille visée."""
        morceaux: list[str] = []
        total = 0
        while total < jetons_cible:
            phrase = phrases[rnd.randrange(len(phrases))]
            morceaux.append(phrase)
            total += estimer_jetons(phrase)
        return " ".join(morceaux)

    @staticmethod
    def _reponse_redigee(question: str, jetons_cible: int, rnd: random.Random) -> str:
        """Réponse rédigée : une accroche, le corps, une conclusion."""
        corps = FakeModeleClient._remplir(PHRASES_REPONSE, jetons_cible, rnd)
        return (
            f"Bonjour, concernant « {question.strip()[:120]} », voici ce que nous pouvons faire. "
            f"{corps} La prochaine étape : transmettez-nous les éléments manquants, "
            "nous revenons vers vous sous cinq jours ouvrés."
        )

    @staticmethod
    def _json_classification(ticket: str, rnd: random.Random) -> str:
        """Sortie JSON courte et structurée (contrôle déterministe §13.3)."""
        categorie = CATEGORIES[rnd.randrange(len(CATEGORIES))]
        priorite = PRIORITES[rnd.randrange(len(PRIORITES))]
        confiance = round(0.72 + rnd.random() * 0.27, 2)
        charge = {"categorie": categorie, "priorite": priorite, "confiance": confiance}
        return json.dumps(charge, ensure_ascii=False)

    @staticmethod
    def _latence(profil: ProfilApplication | None, rnd: random.Random) -> int:
        """Latence synthétique : médiane du profil, avec une queue jusqu'au p95.

        Quatre appels sur cinq tombent à ±15 % de la médiane du profil ; les
        autres s'étalent jusqu'au 95e centile. La médiane observée reste donc
        proche de celle du profil, et la queue est réaliste.
        """
        if profil is None:
            return 500
        mediane = profil.latence_p50_ms
        if rnd.random() < 0.8:
            bas = max(1, int(mediane * 0.85))
            haut = max(bas, int(mediane * 1.15))
        else:
            bas = mediane
            haut = max(mediane, profil.latence_p95_ms)
        return rnd.randint(bas, haut)
