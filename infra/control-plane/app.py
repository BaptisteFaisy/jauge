"""Point d'entrée de l'application CDK du plan de contrôle Jauge (ENF-REP-01).

Usage :

    cdk synth  --app "uv run python app.py" -c jauge.environment=demo
    cdk deploy --app "uv run python app.py" -c jauge.environment=demo

Le compte et la région viennent de l'environnement AWS standard
(`CDK_DEFAULT_ACCOUNT`, `CDK_DEFAULT_REGION`) ou du contexte CDK
(`-c jauge.msp_account_id=…`, `-c jauge.region=…`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Le paquet `jauge_control_plane` vit à côté de ce fichier : on l'ajoute au
# chemin d'import pour que `python app.py` fonctionne sans installation.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aws_cdk import App, Environment  # noqa: E402
from jauge_control_plane.config import CONTEXTE, config_depuis_contexte  # noqa: E402
from jauge_control_plane.stack import ControlPlaneStack  # noqa: E402


def contexte_jauge(app: App) -> dict:
    """Extrait le bloc `jauge.*` du contexte CDK."""
    try:
        bloc = app.node.try_get_context(CONTEXTE) or {}
    except Exception:  # contexte absent : on retombe sur l'environnement
        bloc = {}
    return dict(bloc) if isinstance(bloc, dict) else {}


def construire_app() -> App:
    """Construit l'application : une pile `ControlPlaneStack` par environnement."""
    app = App()
    config = config_depuis_contexte(
        contexte_jauge(app),
        compte=os.environ.get("CDK_DEFAULT_ACCOUNT", "111111111111"),
        region=os.environ.get("CDK_DEFAULT_REGION", "eu-west-1"),
    )
    ControlPlaneStack(
        app,
        f"JaugeControlPlane-{config.environment}",
        config=config,
        env=Environment(account=config.msp_account_id, region=config.region),
        description="Jauge - FinOps for generative AI on Amazon Bedrock (control plane)",
    )
    return app


if __name__ == "__main__":
    construire_app().synth()
