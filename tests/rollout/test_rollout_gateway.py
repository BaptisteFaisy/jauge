"""Tests des règles de passerelle AgentCore (EF-BAS-01, Annexe C).

Aucun appel réseau : `FakePasserelleClient` porte l'état, et l'adaptateur réel
`AgentCoreGatewayClient` est exercé avec une fabrique de client factice qui
enregistre les paramètres reçus. On vérifie ainsi la **forme** des messages
(conditions, route statique, route pondérée) sans jamais toucher AWS.
"""

from __future__ import annotations

import pytest
from tests.rollout.conftest import PETIT_MODELE

from jauge.common.errors import IntrouvableError, ServiceIndisponibleError, ValidationError
from jauge.rollout.gateway import (
    PRIORITE_PAR_DEFAUT,
    AgentCoreGatewayClient,
    FakePasserelleClient,
    construire_action_ponderee,
    construire_action_statique,
    construire_conditions,
    verifier_pourcentage,
)

MODELE_DEMANDE = "anthropic.:0"
PASSERELLE = "gw-demo"


class ClientFactice:
    """Double minimal du client boto3 : enregistre les appels, rend une réponse."""

    def __init__(self, reponse: dict[str, object] | None = None, erreur: Exception | None = None):
        self.appels: list[tuple[str, dict[str, object]]] = []
        self._reponse = reponse or {"ruleIdentifier": "regle-aws-1"}
        self._erreur = erreur

    def create_gateway_rule(self, **parametres: object) -> dict[str, object]:
        self.appels.append(("create_gateway_rule", parametres))
        if self._erreur:
            raise self._erreur
        return self._reponse

    def update_gateway_rule(self, **parametres: object) -> dict[str, object]:
        self.appels.append(("update_gateway_rule", parametres))
        return {}

    def delete_gateway_rule(self, **parametres: object) -> dict[str, object]:
        self.appels.append(("delete_gateway_rule", parametres))
        return {}


def test_conditions_portent_le_modele_demande() -> None:
    """La condition de l'Annexe C matche le modèle demandé par l'application."""
    conditions = construire_conditions(MODELE_DEMANDE)
    assert conditions == [{"matchModels": {"anyOf": [{"value": MODELE_DEMANDE}]}}]


def test_action_statique_route_vers_le_modele_cible() -> None:
    """À 100 %, la route est fixe et épingle le modèle servi (EF-BAS-01)."""
    action = construire_action_statique("bedrock-target", PETIT_MODELE)
    route = action[0]["routeToTarget"]
    assert route["staticRoute"] == {"targetName": "bedrock-target", "model": PETIT_MODELE}


def test_action_ponderee_somme_a_cent() -> None:
    """Hypothèse §23 Q4 : deux entrées pondérées dont les poids somment à 100."""
    action = construire_action_ponderee("bedrock-target", MODELE_DEMANDE, PETIT_MODELE, 10)
    cibles = action[0]["routeToTarget"]["weightedRoute"]["targets"]
    assert [c["weight"] for c in cibles] == [10, 90]
    assert cibles[0]["model"] == PETIT_MODELE
    assert cibles[1]["model"] == MODELE_DEMANDE


def test_action_ponderee_a_cent_retombe_sur_la_route_statique() -> None:
    """Le dernier palier n'a pas besoin d'une route pondérée."""
    assert construire_action_ponderee(
        "bedrock-target", MODELE_DEMANDE, PETIT_MODELE, 100
    ) == construire_action_statique("bedrock-target", PETIT_MODELE)


@pytest.mark.parametrize("pourcentage", [0, 101, -10])
def test_pourcentage_hors_bornes_refuse(pourcentage: int) -> None:
    """Un palier hors ]0, 100] est une donnée invalide, pas une règle silencieuse."""
    with pytest.raises(ValidationError):
        verifier_pourcentage(pourcentage)


def test_fake_passerelle_cree_pondere_et_supprime() -> None:
    """Cycle complet d'une règle : pondérée à 10 %, portée à 50 %, retirée."""
    passerelle = FakePasserelleClient()
    regle = passerelle.creer_regle_ponderee(PASSERELLE, MODELE_DEMANDE, PETIT_MODELE, 10)
    assert regle.ponderee is True
    assert regle.pourcentage_cible == 10

    maj = passerelle.mettre_a_jour_poids(regle.id, 50)
    assert maj.pourcentage_cible == 50
    assert passerelle.lire_regle(regle.id) == maj

    passerelle.supprimer_regle(regle.id)
    assert passerelle.lire_regle(regle.id) is None
    assert passerelle.lister_regles(PASSERELLE) == []
    assert passerelle.operations() == [
        "creer_regle_ponderee",
        "mettre_a_jour_poids",
        "supprimer_regle",
    ]


def test_fake_passerelle_regle_inconnue() -> None:
    """Supprimer ou modifier une règle absente est une erreur explicite."""
    passerelle = FakePasserelleClient()
    with pytest.raises(IntrouvableError):
        passerelle.supprimer_regle("regle-inexistante")
    with pytest.raises(IntrouvableError):
        passerelle.mettre_a_jour_poids("regle-inexistante", 50)


def test_agentcore_forme_de_la_creation() -> None:
    """L'appel réel reproduit l'Annexe C : priorité, conditions, actions."""
    factice = ClientFactice()
    client = AgentCoreGatewayClient(region="eu-west-1", client_factory=lambda: factice)

    regle = client.creer_regle_statique(PASSERELLE, MODELE_DEMANDE, PETIT_MODELE, "démo")

    assert regle.id == "regle-aws-1"
    methode, parametres = factice.appels[0]
    assert methode == "create_gateway_rule"
    assert parametres["gatewayIdentifier"] == PASSERELLE
    assert parametres["priority"] == PRIORITE_PAR_DEFAUT
    assert parametres["conditions"] == construire_conditions(MODELE_DEMANDE)
    assert parametres["actions"] == construire_action_statique(client.nom_cible, PETIT_MODELE)


def test_agentcore_mise_a_jour_puis_suppression() -> None:
    """Un changement de palier puis un retrait visent le même identifiant de règle."""
    factice = ClientFactice()
    client = AgentCoreGatewayClient(region="eu-west-1", client_factory=lambda: factice)
    regle = client.creer_regle_ponderee(PASSERELLE, MODELE_DEMANDE, PETIT_MODELE, 10)

    client.mettre_a_jour_poids(regle.id, 50)
    client.supprimer_regle(regle.id)

    methode_maj, parametres_maj = factice.appels[1]
    assert methode_maj == "update_gateway_rule"
    assert parametres_maj["gatewayIdentifier"] == PASSERELLE
    assert parametres_maj["ruleIdentifier"] == regle.id
    assert parametres_maj["actions"] == construire_action_ponderee(
        client.nom_cible, MODELE_DEMANDE, PETIT_MODELE, 50
    )
    methode_sup, parametres_sup = factice.appels[2]
    assert methode_sup == "delete_gateway_rule"
    assert parametres_sup["ruleIdentifier"] == regle.id
    assert client.lire_regle(regle.id) is None


def test_agentcore_panne_reseau_traduite() -> None:
    """Une panne de la passerelle devient `ServiceIndisponibleError` (pas un 500 nu)."""
    factice = ClientFactice(erreur=RuntimeError("endpoint injoignable"))
    client = AgentCoreGatewayClient(region="eu-west-1", client_factory=lambda: factice)

    with pytest.raises(ServiceIndisponibleError):
        client.creer_regle_statique(PASSERELLE, MODELE_DEMANDE, PETIT_MODELE)


def test_agentcore_reponse_sans_identifiant_refusee() -> None:
    """Sans identifiant de règle, impossible de piloter la bascule : on échoue."""
    factice = ClientFactice(reponse={"autreChamp": "x"})
    client = AgentCoreGatewayClient(region="eu-west-1", client_factory=lambda: factice)

    with pytest.raises(ServiceIndisponibleError):
        client.creer_regle_ponderee(PASSERELLE, MODELE_DEMANDE, PETIT_MODELE, 10)
