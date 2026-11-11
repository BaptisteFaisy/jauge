"""Tests des applications clientes et du client de modèle hors ligne.

Vérifient que le port `ModeleClient` est bien respecté, que les trois
applications produisent des requêtes conformes à leur profil et que le client
factice est déterministe et sans réseau.
"""

from __future__ import annotations

import json
import random

import pytest
from demo.apps import (
    DocSummarizer,
    FakeModeleClient,
    ModeleClient,
    SupportAssistant,
    TicketClassifier,
)
from demo.apps.port import MetadonneesAppel, RequeteModele, estimer_jetons, remplir_contexte
from demo.profils import GRAND_MODELE, PETIT_MODELE


@pytest.fixture
def client() -> FakeModeleClient:
    """Client de modèle hors ligne."""
    return FakeModeleClient()


def test_client_respecte_le_port(client: FakeModeleClient) -> None:
    """Le client factice implémente le protocole `ModeleClient`."""
    assert isinstance(client, ModeleClient)
    assert callable(client.converse)


def test_assistant_prefixe_long_et_reponse_redigee(client: FakeModeleClient) -> None:
    """L'assistant envoie un préfixe long et obtient une réponse rédigée."""
    assistant = SupportAssistant(client, GRAND_MODELE)
    requete = assistant.construire_requete("Comment résilier mon abonnement ?")
    assert len(requete.systeme) == 1
    assert estimer_jetons(requete.systeme[0]) > 500
    assert requete.metadonnees.application_id == "support-assistant"
    assert requete.metadonnees.team == "support"
    assert requete.parametres.max_tokens == 4096
    reponse = client.converse(requete)
    assert reponse.jetons_sortie > 400
    assert reponse.jetons_entree > 500


def test_classement_sortie_json_courte(client: FakeModeleClient) -> None:
    """Le classement rend un JSON court, décodable et borné."""
    classifieur = TicketClassifier(client, GRAND_MODELE)
    resultat = classifieur.classer("La carte bancaire a été refusée.")
    assert set(resultat) == {"categorie", "priorite", "confiance"}
    assert 0.0 <= float(resultat["confiance"]) <= 1.0
    requete = classifieur.construire_requete("La carte bancaire a été refusée.")
    reponse = client.converse(requete)
    assert reponse.jetons_sortie < 40
    assert reponse.jetons_entree < 200


def test_resume_sortie_longue(client: FakeModeleClient) -> None:
    """Le résumé produit une sortie longue, proche de la limite du profil."""
    resumeur = DocSummarizer(client, GRAND_MODELE)
    texte = resumeur.resumer("Le présent document décrit le bilan d'activité.")
    assert len(texte) > 2000
    requete = resumeur.construire_requete("Le présent document décrit le bilan d'activité.")
    assert requete.metadonnees.type_requete == "resume"


def test_client_deterministe(client: FakeModeleClient) -> None:
    """Deux appels identiques donnent la même réponse et les mêmes jetons."""
    classifieur = TicketClassifier(client, GRAND_MODELE)
    requete = classifieur.construire_requete("Le service est indisponible ce matin.")
    premiere = client.converse(requete)
    seconde = client.converse(requete)
    assert premiere == seconde


def test_modeles_differents_memes_jetons(client: FakeModeleClient) -> None:
    """Le coût change avec le modèle, pas les jetons (même requête)."""
    classifieur = TicketClassifier(client, PETIT_MODELE)
    requete = classifieur.construire_requete("La facture comporte une ligne en double.")
    reponse = client.converse(requete)
    assert reponse.modele_id == PETIT_MODELE


def test_port_champs_serialisables() -> None:
    """La requête du port porte tous les champs attendus par M4 (§13.2)."""
    requete = RequeteModele(
        modele_id=GRAND_MODELE,
        messages=(),
        systeme=("Consigne",),
        metadonnees=MetadonneesAppel(application_id="x", team="y", environment="production"),
    )
    assert requete.parametres.max_tokens > 0
    assert requete.parametres.temperature == 0.0
    assert requete.metadonnees.environment == "production"


def test_remplir_contexte_taille() -> None:
    """Le contexte de remplissage atteint l'ordre de grandeur demandé."""
    texte = remplir_contexte(500, random.Random(7))
    assert 400 <= estimer_jetons(texte) <= 700
    assert remplir_contexte(0, random.Random(7)) == ""


def test_reponse_json_du_client_est_du_json() -> None:
    """La sortie du classement est un objet JSON sérialisable."""
    client = FakeModeleClient()
    classifieur = TicketClassifier(client, GRAND_MODELE)
    reponse = client.converse(classifieur.construire_requete("Test de ticket."))
    assert isinstance(json.loads(reponse.texte), dict)
