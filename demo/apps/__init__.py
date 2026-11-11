"""Applications « cliente » de la démonstration Jauge (§18.1).

Trois applications synthétiques chez le client A, plus un client de modèle
hors ligne :

- `support-assistant` : assistant de support sur un grand modèle ;
- `ticket-classifier` : classement de tickets sur le même grand modèle ;
- `doc-summarizer` : résumé nocturne de documents au niveau Standard.

Le quatrième profil de trafic (`kb-search`) appartient au client B : il
alimente la vue parc, sans levier attendu (formule de visibilité).

Le port `ModeleClient` est défini dans `demo/apps/port.py` et doit rester
aligné avec celui du module de rejeu (M4).
"""

from __future__ import annotations

from demo.apps.doc_summarizer import DocSummarizer
from demo.apps.fake_client import FakeModeleClient
from demo.apps.kb_search import KbSearch
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
from demo.apps.support_assistant import SupportAssistant
from demo.apps.ticket_classifier import TicketClassifier

__all__ = [
    "DocSummarizer",
    "FakeModeleClient",
    "KbSearch",
    "Message",
    "MetadonneesAppel",
    "ModeleClient",
    "ParametresRejeu",
    "RequeteModele",
    "ReponseModele",
    "SupportAssistant",
    "TicketClassifier",
    "estimer_jetons",
    "remplir_contexte",
]
