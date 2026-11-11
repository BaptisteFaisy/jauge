"""Outillage commun des tests d'infrastructure (aucun appel AWS).

`pytest` ajoute `tests/infra` au chemin d'import : ce module s'y trouve et
installe aussi `infra/control-plane` pour rendre `jauge_control_plane`
importable, que les tests soient lancés depuis la racine du dépôt ou depuis un
autre répertoire.
"""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
APPLICATION = RACINE / "infra" / "control-plane"
if str(APPLICATION) not in sys.path:
    sys.path.insert(0, str(APPLICATION))

from aws_cdk import App, Environment  # noqa: E402
from jauge_control_plane.config import ControlPlaneConfig  # noqa: E402
from jauge_control_plane.stack import ControlPlaneStack  # noqa: E402

COMPTE_TEST = "123456789012"
REGION_TEST = "eu-west-1"


def config_test(**surcharges) -> ControlPlaneConfig:
    """Configuration de test : compte fictif, courriel de budget factice."""
    valeurs = {
        "msp_account_id": COMPTE_TEST,
        "region": REGION_TEST,
        "environment": "test",
        "monthly_budget_usd": 200.0,
        "budget_alert_emails": ("finops@example.com",),
    }
    valeurs.update(surcharges)
    return ControlPlaneConfig(**valeurs)


def stack_test(config: ControlPlaneConfig | None = None) -> ControlPlaneStack:
    """Pile de test synthétisable hors ligne."""
    config = config or config_test()
    app = App()
    return ControlPlaneStack(
        app,
        "JaugeControlPlaneTest",
        config=config,
        env=Environment(account=config.msp_account_id, region=config.region),
    )
