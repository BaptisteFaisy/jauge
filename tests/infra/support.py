"""Outillage commun des tests d'infrastructure (aucun appel AWS).

`pytest` ajoute `tests/infra` au chemin d'import : ce module s'y trouve et
installe aussi `infra/control-plane` pour rendre `jauge_control_plane`
importable, que les tests soient lancés depuis la racine du dépôt ou depuis un
autre répertoire.

**Import paresseux obligatoire** : le job CI `test` n'installe pas le groupe de
dépendances `infra` (pas d'`aws-cdk-lib`). Les modules de test sont malgré tout
*collectés* — puis désélectionnés par le marqueur `infra` — donc importer
`aws_cdk` au chargement du module ferait échouer ce job. Les imports CDK sont
donc faits à l'intérieur des fonctions.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parents[2]
APPLICATION = RACINE / "infra" / "control-plane"
if str(APPLICATION) not in sys.path:
    sys.path.insert(0, str(APPLICATION))

COMPTE_TEST = "123456789012"
REGION_TEST = "eu-west-1"


def config_test(**surcharges) -> Any:
    """Configuration de test : compte fictif, courriel de budget factice."""
    from jauge_control_plane.config import ControlPlaneConfig

    valeurs = {
        "msp_account_id": COMPTE_TEST,
        "region": REGION_TEST,
        "environment": "test",
        "monthly_budget_usd": 200.0,
        "budget_alert_emails": ("finops@example.com",),
    }
    valeurs.update(surcharges)
    return ControlPlaneConfig(**valeurs)


def stack_test(config: Any = None) -> Any:
    """Pile de test synthétisable hors ligne."""
    from aws_cdk import App, Environment
    from jauge_control_plane.stack import ControlPlaneStack

    config = config or config_test()
    app = App()
    return ControlPlaneStack(
        app,
        "JaugeControlPlaneTest",
        config=config,
        env=Environment(account=config.msp_account_id, region=config.region),
    )
