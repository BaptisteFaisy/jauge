"""Tests de la lecture du contexte CDK (`-c jauge.<clé>=<valeur>`).

Ces tests couvrent un bug réel : `cdk synth -c jauge.region=eu-west-1` pose une
clé de contexte **littérale** (« jauge.region »), alors que l'application lisait
un bloc imbriqué (`try_get_context("jauge")`). Résultat : tous les paramètres
documentés dans `infra/control-plane/README.md` étaient ignorés en silence.

Aucun appel AWS : on construit une `App` en mémoire avec un contexte fourni.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
APPLICATION = RACINE / "infra" / "control-plane"
if str(APPLICATION) not in sys.path:
    sys.path.insert(0, str(APPLICATION))

pytestmark = pytest.mark.infra


def _app(contexte: dict) -> object:
    """Construit une `App` CDK portant le contexte donné (aucun appel AWS)."""
    from aws_cdk import App

    return App(context=contexte)


def test_cles_litterales_pointees_sont_lues() -> None:
    """La forme ligne de commande (`-c jauge.region=…`) est bien lue."""
    import app as point_entree

    application = _app({"jauge.region": "eu-west-1", "jauge.environment": "demo"})
    bloc = point_entree.contexte_jauge(application)
    assert bloc["region"] == "eu-west-1"
    assert bloc["environment"] == "demo"


def test_bloc_imbrique_est_lu() -> None:
    """La forme `cdk.json` (`{"jauge": {...}}`) reste supportée."""
    import app as point_entree

    application = _app({"jauge": {"region": "eu-west-3", "monthly_budget_usd": 120}})
    bloc = point_entree.contexte_jauge(application)
    assert bloc["region"] == "eu-west-3"
    assert bloc["monthly_budget_usd"] == 120


def test_la_cle_litterale_a_priorite_sur_le_bloc() -> None:
    """La ligne de commande l'emporte sur `cdk.json`."""
    import app as point_entree

    application = _app({"jauge": {"region": "eu-west-3"}, "jauge.region": "eu-west-1"})
    assert point_entree.contexte_jauge(application)["region"] == "eu-west-1"


def test_cles_inconnues_ignores_sans_erreur() -> None:
    """Une clé hors liste n'est pas inventée : seules les clés connues remontent."""
    import app as point_entree

    application = _app({"jauge.inexistant": "x", "autre": "y"})
    bloc = point_entree.contexte_jauge(application)
    assert "inexistant" not in bloc
    assert "autre" not in bloc


def test_region_du_contexte_dans_la_configuration() -> None:
    """La région demandée atteint `ControlPlaneConfig`, pas la région ambiante."""
    import app as point_entree
    from jauge_control_plane.config import config_depuis_contexte

    application = _app({"jauge.region": "eu-west-1", "jauge.msp_account_id": "123456789012"})
    config = config_depuis_contexte(
        point_entree.contexte_jauge(application), compte="111111111111", region="eu-north-1"
    )
    assert config.region == "eu-west-1"
    assert config.msp_account_id == "123456789012"


def test_booleen_de_ligne_de_commande() -> None:
    """`-c jauge.enable_api_access_logs=false` ne doit pas valoir True."""
    from jauge_control_plane.config import _booleen

    assert _booleen("false") is False
    assert _booleen("0") is False
    assert _booleen("no") is False
    assert _booleen("true") is True
    assert _booleen(True) is True
    assert _booleen(False) is False
    assert _booleen(None) is True  # défaut du dépôt : journaux d'accès activés
    assert _booleen("") is True


def test_config_booleen_via_contexte() -> None:
    """Le booléen de la config passe par la coercition, pas par `bool()`."""
    from jauge_control_plane.config import config_depuis_contexte

    config = config_depuis_contexte(
        {"enable_api_access_logs": "false"}, compte="123456789012", region="eu-west-1"
    )
    assert config.enable_api_access_logs is False


def test_courriels_budget_en_chaine() -> None:
    """Une liste de courriels passée en chaîne est découpée."""
    from jauge_control_plane.config import config_depuis_contexte

    config = config_depuis_contexte(
        {"budget_alert_emails": "a@example.com, b@example.com"},
        compte="123456789012",
        region="eu-west-1",
    )
    assert config.budget_alert_emails == ("a@example.com", "b@example.com")
