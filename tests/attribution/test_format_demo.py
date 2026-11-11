"""Test du format de journal proposé par le module de démo (M12).

Le générateur de trafic synthétique (M12, `demo/traffic.py`) produit des lignes
d'invocation où les métadonnées de requête et les métriques d'invocation sont des
**chaînes JSON**, et non des objets. Le parseur doit lire ce format tel quel :
c'est la forme que produira la démonstration.

Ce test verrouille l'interopérabilité des deux modules ; il est à relire si le
format de la démo change.
"""

from __future__ import annotations

import json

import pytest
from tests.attribution.conftest import JOUR

from jauge.attribution.logs import analyser_enregistrement, iterer_enregistrements
from jauge.models.pricing import ServiceTier


def _ligne_demo(**surcharges: object) -> dict[str, object]:
    """Une ligne d'invocation au format de la démo (M12)."""
    ligne: dict[str, object] = {
        "requestId": "req-demo-0001",
        "eventTime": "2026-11-09T08:12:00Z",
        "modelId": "modele-demo-grand",
        "input": json.dumps({"messages": [{"role": "user", "content": [{"text": "bonjour"}]}]}),
        "output": json.dumps(
            {
                "output": {"message": {"role": "assistant", "content": [{"text": "salut"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 1200, "outputTokens": 80, "totalTokens": 1280},
            }
        ),
        "inputTokenCount": 1200,
        "outputTokenCount": 80,
        "totalTokenCount": 1280,
        "amazon-bedrock-invocationMetrics": json.dumps(
            {
                "inputTokenCount": 1200,
                "outputTokenCount": 80,
                "cacheReadInputTokenCount": 300,
                "cacheWriteInputTokenCount": 100,
                "invocationLatency": 620,
                "firstByteLatency": 120,
                "serviceTier": "Standard",
            }
        ),
        "requestMetadata": json.dumps(
            {
                "application": "ticket-classifier",
                "team": "support",
                "environment": "production",
                "typeRequete": "classement",
            }
        ),
        "errorCode": None,
        "errorMessage": None,
    }
    ligne.update(surcharges)
    return ligne


def test_format_demo_est_lu_entierement() -> None:
    """Le format de la démo donne modèle, jetons, cache, latence, niveau et étiquettes."""
    enregistrement = analyser_enregistrement(_ligne_demo())

    assert enregistrement.request_id == "req-demo-0001"
    assert enregistrement.model_id == "modele-demo-grand"
    assert enregistrement.jour == JOUR
    assert enregistrement.jetons_entree == 1200
    assert enregistrement.jetons_sortie == 80
    assert enregistrement.jetons_cache_lecture == 300
    assert enregistrement.jetons_cache_ecriture == 100
    assert enregistrement.latence_ms == 620
    assert enregistrement.niveau_service is ServiceTier.STANDARD
    assert enregistrement.etiquettes.application == "ticket-classifier"
    assert enregistrement.etiquettes.equipe == "support"
    assert enregistrement.etiquettes.environnement == "production"
    assert enregistrement.etiquettes.type_requete == "classement"


def test_format_demo_avec_erreur() -> None:
    """Une ligne d'erreur (jetons nuls, `errorCode` présent) reste exploitable.

    Elle doit être comptée et non rejetée : une erreur de throttling fait partie
    du trafic réel, elle ne doit pas disparaître du rapprochement.
    """
    enregistrement = analyser_enregistrement(
        _ligne_demo(
            inputTokenCount=0,
            outputTokenCount=0,
            totalTokenCount=0,
            errorCode="throttlingException",
            errorMessage="Too many requests",
            output=None,
        )
    )

    assert enregistrement.jetons_entree == 0
    assert enregistrement.statut == "throttlingexception"
    assert enregistrement.reussie is False


def test_format_demo_en_flux() -> None:
    """Le format de la démo se lit en flux, comme tout journal."""
    lignes = [_ligne_demo(requestId=f"req-{index}") for index in range(5)]

    enregistrements = list(iterer_enregistrements(lignes))

    assert len(enregistrements) == 5
    assert all(enregistrement.jour == JOUR for enregistrement in enregistrements)


def test_metadonnees_en_objet_et_en_chaine_equivalent() -> None:
    """La même métadonnée, en objet ou en chaîne JSON, donne la même étiquette."""
    en_objet = _ligne_demo(requestMetadata={"application": "app-x", "team": "support"})
    en_chaine = _ligne_demo(requestMetadata=json.dumps({"application": "app-x", "team": "support"}))

    assert (
        analyser_enregistrement(en_objet).etiquettes
        == analyser_enregistrement(en_chaine).etiquettes
    )


def test_chaine_json_invalide_ignoree_sans_erreur() -> None:
    """Une métadonnée en chaîne illisible ne fait pas tomber l'enregistrement."""
    enregistrement = analyser_enregistrement(_ligne_demo(requestMetadata="{pas du json"))

    assert enregistrement.model_id == "modele-demo-grand"
    assert enregistrement.etiquettes.application is None


@pytest.mark.parametrize("service_tier", ["Standard", "Flex", "Priority", "Reserved"])
def test_niveaux_de_service_du_format_demo(service_tier: str) -> None:
    """Les quatre niveaux de service sont lus depuis les métriques imbriquées."""
    metrics = json.dumps(
        {"inputTokenCount": 10, "outputTokenCount": 1, "serviceTier": service_tier}
    )

    enregistrement = analyser_enregistrement(
        _ligne_demo(**{"amazon-bedrock-invocationMetrics": metrics})
    )

    assert enregistrement.niveau_service is ServiceTier(service_tier.lower())
