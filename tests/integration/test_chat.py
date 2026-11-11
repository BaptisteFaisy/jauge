"""Le port de chat est branché sur l'orchestrateur Strands (M7 → M17).

Trois comportements vérifiés, tous hors ligne :

1. en mode démonstration, la réponse est **déterministe** et ses chiffres sont
   produits par les outils des agents (donc par les modules réels) ;
2. avec un modèle disponible, la question passe par le chef d'orchestre Strands ;
3. si le modèle échoue, la réponse déterministe est rendue **avec le motif** :
   jamais d'erreur silencieuse.

Exigences visées : EF-RAP-04, §12.1, ENF-IA-01, ENF-CONF-01.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.integration.conftest import TENANT_A, Environnement, entetes

from jauge.agents.modele_factice import ModeleFactice
from jauge.integration import Clients, creer_services
from jauge.integration.chat import AdaptateurChat


@pytest.fixture
def chat(services, environnement: Environnement) -> AdaptateurChat:
    """Adaptateur de chat en mode démonstration."""
    return AdaptateurChat(services, environnement.store, mode_demo=True)


def test_chat_deterministe_en_mode_demo(chat, environnement: Environnement) -> None:
    """Les chiffres de la réponse viennent des modules, pas d'un modèle."""
    reponse = chat.repondre(TENANT_A, "Où part l'argent ce mois-ci ?")
    assert reponse["mode"] == "demonstration"
    chiffres = reponse["chiffres"]
    assert chiffres["cout_total_usd"] > 0
    assert chiffres["leviers_detectes"] > 0
    assert str(chiffres["cout_total_usd"]) in str(reponse["reponse"])
    assert "texte_requete" not in reponse
    assert "reponse_candidate" not in reponse


def test_chat_passe_par_l_orchestrateur_strands(
    environnement: Environnement,
) -> None:
    """Hors démo, la question est confiée au chef d'orchestre Strands."""
    pytest.importorskip("strands")
    services = creer_services(
        environnement.store,
        mode_demo=False,
        clients=Clients(),
        tarifs=environnement.tarifs,
        plan=environnement.plan,
    )
    modele = ModeleFactice(texte_final="La dépense vient surtout du classement de tickets.")
    chat = AdaptateurChat(services, environnement.store, modele=modele, mode_demo=False)
    reponse = chat.repondre(TENANT_A, "Pourquoi le classement coûte-t-il plus ?")
    assert reponse["mode"] == "agent"
    assert "classement" in str(reponse["reponse"])
    assert modele.appels >= 1


def test_chat_repli_explicite_si_le_modele_echoue(environnement: Environnement) -> None:
    """Un modèle injoignable produit une réponse déterministe **et** le motif."""
    services = creer_services(
        environnement.store,
        mode_demo=False,
        clients=Clients(),
        tarifs=environnement.tarifs,
        plan=environnement.plan,
    )

    class ModeleEnPanne(ModeleFactice):
        """Modèle qui échoue à chaque appel (panne du fournisseur)."""

        async def stream(self, *args: Any, **kwargs: Any):  # noqa: D102
            raise RuntimeError("modèle injoignable")
            yield {}  # pragma: no cover - générateur asynchrone pour satisfaire le protocole

    chat = AdaptateurChat(services, environnement.store, modele=ModeleEnPanne(), mode_demo=False)
    reponse = chat.repondre(TENANT_A, "Où part l'argent ?")
    assert reponse["mode"] == "deterministe"
    assert "motif" in reponse
    assert "injoignable" in str(reponse["motif"])
    assert reponse["chiffres"]["cout_total_usd"] > 0


def test_chat_via_api_en_mode_demo(client) -> None:
    """La route `/v1/chat` sert la réponse déterministe de l'adaptateur."""
    reponse = client.post(
        "/v1/chat",
        json={"question": "Quels leviers reste-t-il sur le classement de tickets ?"},
        headers=entetes(TENANT_A),
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["mode"] == "demonstration"
    assert corps["chiffres"]["leviers_detectes"] > 0


def test_chat_refuse_un_client_inconnu(chat) -> None:
    """Une question sur un client inexistant est refusée (ENF-SEC-02)."""
    from jauge.common.errors import CloisonnementError

    with pytest.raises(CloisonnementError):
        chat.repondre("t-inconnu", "Où part l'argent ?")
