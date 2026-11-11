"""Tests des adaptateurs de modèle (EF-REJ-02, EF-REJ-03, EF-REJ-04).

Aucun réseau : `BedrockModeleClient` est exercé avec un client boto3 factice
injecté, jamais avec `boto3.client`.
"""

from __future__ import annotations

import pytest

from jauge.common.errors import ServiceIndisponibleError
from jauge.models.campaign import ParametresRejeu
from jauge.models.pricing import ServiceTier
from jauge.replay.client import (
    BedrockModeleClient,
    DemandeRejeu,
    FakeModeleClient,
    ModeleClient,
)


class _FauxClientBoto3:
    """Doublure du client boto3 `bedrock-runtime` (aucun appel réseau)."""

    def __init__(self, reponse: dict | None = None) -> None:
        self.reponse = reponse or {
            "output": {"message": {"content": [{"text": "bonjour"}, {"text": " !"}]}},
            "usage": {
                "inputTokens": 12,
                "outputTokens": 34,
                "cacheWriteInputTokens": 5,
                "cacheReadInputTokens": 6,
            },
        }
        self.appels: list[dict] = []

    def converse(self, **arguments):
        """Enregistre les arguments et rend une réponse `Converse` type."""
        self.appels.append(arguments)
        return self.reponse


def _demande(niveau: ServiceTier = ServiceTier.FLEX, **surcharges) -> DemandeRejeu:
    """Demande de rejeu de test, paramètres d'origine compris."""
    parametres = ParametresRejeu(
        temperature=0.3, max_tokens=256, top_p=0.9, stop_sequences=["\n\n"]
    )
    valeurs = {
        "requete_id": "req-1",
        "modele_id": "demo-grand-modele",
        "texte_requete": "Classe ce ticket.",
        "parametres": parametres,
        "niveau": niveau,
        "consigne_systeme": "Tu classes des tickets.",
    }
    valeurs.update(surcharges)
    return DemandeRejeu(**valeurs)


def test_fake_rend_usage_et_latence(client_fictif: FakeModeleClient) -> None:
    """EF-REJ-04 : jetons et latence sont rendus pour chaque invocation."""
    resultat = client_fictif.invoquer(_demande())
    assert resultat.jetons_sortie == 40
    assert resultat.jetons_entree > 0
    assert resultat.latence_ms == 250
    assert resultat.niveau == ServiceTier.FLEX


def test_fake_est_deterministe() -> None:
    """Deux exécutions du même couple (modèle, texte) rendent le même texte."""
    client = FakeModeleClient()
    premier = client.invoquer(_demande())
    second = client.invoquer(_demande())
    assert premier.texte == second.texte


def test_fake_variabilite() -> None:
    """EF-EVA-04 : la variabilité simulée fait diverger la seconde exécution."""
    client = FakeModeleClient(variabilite=True)
    premier = client.invoquer(_demande())
    second = client.invoquer(_demande())
    assert premier.texte != second.texte


def test_fake_texte_impose() -> None:
    """Un texte imposé par couple (modèle, texte) prime sur la dérivation."""
    client = FakeModeleClient(textes={("demo-grand-modele", "Classe ce ticket."): '{"ok": true}'})
    assert client.invoquer(_demande()).texte == '{"ok": true}'


def test_fake_modele_en_erreur() -> None:
    """Une panne simulée remonte en erreur métier, jamais en exception nue."""
    client = FakeModeleClient(modeles_en_erreur={"demo-grand-modele"})
    with pytest.raises(ServiceIndisponibleError):
        client.invoquer(_demande())


def test_fake_journal_des_appels() -> None:
    """Le journal permet de vérifier les paramètres rejoués (EF-REJ-02)."""
    client = FakeModeleClient()
    client.invoquer(_demande())
    client.invoquer(_demande(requete_id="req-2"))
    assert len(client.demandes_pour("req-1")) == 1
    assert len(client.appels) == 2


def test_fake_satisfait_le_protocole(client_fictif: FakeModeleClient) -> None:
    """`FakeModeleClient` respecte le contrat `ModeleClient` (typage structurel)."""
    client: ModeleClient = client_fictif
    assert client.invoquer(_demande()).modele_id == "demo-grand-modele"


def test_bedrock_construit_les_parametres_d_origine() -> None:
    """EF-REJ-02 : température, top_p, limite de jetons et arrêt sont transmis tels quels."""
    faux = _FauxClientBoto3()
    client = BedrockModeleClient("eu-west-1", client=faux)
    client.invoquer(_demande())
    arguments = faux.appels[0]
    assert arguments["modelId"] == "demo-grand-modele"
    assert arguments["inferenceConfig"] == {
        "temperature": 0.3,
        "maxTokens": 256,
        "topP": 0.9,
        "stopSequences": ["\n\n"],
    }
    assert arguments["messages"] == [{"role": "user", "content": [{"text": "Classe ce ticket."}]}]
    assert arguments["system"] == [{"text": "Tu classes des tickets."}]


def test_bedrock_niveau_flex_transmis() -> None:
    """EF-REJ-03 : le niveau de service voyage dans l'appel (et pas pour Standard)."""
    faux = _FauxClientBoto3()
    client = BedrockModeleClient("eu-west-1", client=faux)
    client.invoquer(_demande())
    client.invoquer(_demande(niveau=ServiceTier.STANDARD))
    assert faux.appels[0]["serviceTier"] == {"type": "flex"}
    assert "serviceTier" not in faux.appels[1]


def test_bedrock_traduit_la_reponse() -> None:
    """EF-REJ-04 : texte concaténé, jetons et cache lus dans `usage`."""
    faux = _FauxClientBoto3()
    resultat = BedrockModeleClient("eu-west-1", client=faux).invoquer(_demande())
    assert resultat.texte == "bonjour !"
    assert resultat.jetons_entree == 12
    assert resultat.jetons_sortie == 34
    assert resultat.jetons_cache_ecriture == 5
    assert resultat.jetons_cache_lecture == 6
    assert resultat.latence_ms >= 0


def test_bedrock_panne_devenue_erreur_metier() -> None:
    """Une exception boto3 est traduite en `ServiceIndisponibleError`."""

    class _ClientEnPanne:
        def converse(self, **arguments):
            raise RuntimeError("throttled")

    client = BedrockModeleClient("eu-west-1", client=_ClientEnPanne())
    with pytest.raises(ServiceIndisponibleError):
        client.invoquer(_demande())


def test_bedrock_sans_consigne_systeme() -> None:
    """Sans consigne système, le champ `system` est omis (comportement Bedrock)."""
    faux = _FauxClientBoto3()
    BedrockModeleClient("eu-west-1", client=faux).invoquer(_demande(consigne_systeme=""))
    assert "system" not in faux.appels[0]
