"""Tests du paquetage Lambda du plan de contrôle (marqueur `infra`).

Ces modules ne contiennent aucune logique métier : on vérifie qu'ils se
comportent proprement quand l'application applicative n'est pas encore
empaquetée (déploiement partiel, module M8 non encore fusionné).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

APPLICATION = Path(__file__).resolve().parents[2] / "infra" / "control-plane"
if str(APPLICATION) not in sys.path:
    sys.path.insert(0, str(APPLICATION))

pytestmark = pytest.mark.infra


def _charger(nom: str):
    """Charge un module du dossier `lambda/` (nom réservé : pas un import)."""
    spec = importlib.util.spec_from_file_location(nom, APPLICATION / "lambda" / f"{nom}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


def test_handler_repond_501_sans_application(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans `jauge.api` dans la couche, la fonction le dit au lieu de planter."""
    handler = _charger("handler")

    def _application_absente() -> object:
        raise ImportError("jauge.api.app absent de la couche Lambda")

    monkeypatch.setattr(handler, "_application", _application_absente)
    reponse = handler.handler({"httpMethod": "GET", "path": "/health"}, None)
    assert reponse["statusCode"] == 501
    assert "not available" in reponse["body"]


def test_handler_sert_la_sonde_de_vie() -> None:
    """Application présente : la sonde `/health` passe réellement par l'ASGI.

    Prouve que le nom d'intégration documenté (`jauge.api.app:app`) existe et
    que l'adaptateur Lambda exécute l'application au lieu de répondre 501.
    """
    handler = _charger("handler")
    reponse = handler.handler({"httpMethod": "GET", "path": "/health"}, None)
    assert reponse["statusCode"] == 200
    assert '"status": "ok"' in reponse["body"] or '"status":"ok"' in reponse["body"]


def test_handler_construit_une_portee_asgi():
    """La portée ASGI porte méthode, chemin et en-têtes de l'événement proxy."""
    handler = _charger("handler")
    portee, corps = handler._portee(
        {
            "httpMethod": "POST",
            "path": "/campagnes",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer x"},
            "queryStringParameters": {"tenant_id": "acme"},
            "body": '{"a": 1}',
        }
    )
    assert portee["method"] == "POST"
    assert portee["path"] == "/campagnes"
    assert portee["query_string"] == b"tenant_id=acme"
    assert corps == b'{"a": 1}'
    entetes = {cle.decode(): valeur.decode() for cle, valeur in portee["headers"]}
    assert entetes["authorization"] == "Bearer x"


def test_handler_decode_le_corps_base64():
    """Un corps encodé en base64 est décodé avant d'atteindre l'application."""
    handler = _charger("handler")
    _, corps = handler._portee({"body": "eyJhIjogMX0=", "isBase64Encoded": True})
    assert corps == b'{"a": 1}'


def test_tools_repond_501_sans_executeur():
    """Sans module applicatif, l'outil renvoie une erreur explicite à l'agent."""
    tools = _charger("tools")
    reponse = tools.handler({"name": "query_cost_aggregates", "arguments": {}}, None)
    assert reponse["isError"] is True
    assert "not_implemented" in reponse["content"][0]["text"]


def test_tools_ignore_un_appel_sans_nom():
    """Un appel sans nom d'outil est rejeté proprement."""
    tools = _charger("tools")
    reponse = tools.handler({}, None)
    assert reponse["isError"] is True
    charge = reponse["content"][0]["text"]
    assert "invalid_request" in charge or "not_implemented" in charge
