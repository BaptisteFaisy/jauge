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

#: Clés de contexte reconnues (`cdk synth -c jauge.<clé>=<valeur>`).
CLES_CONTEXTE = (
    "msp_account_id",
    "region",
    "environment",
    "portal_domain_name",
    "portal_certificate_arn",
    "monthly_budget_usd",
    "budget_alert_emails",
    "log_retention_days",
    "api_stage_name",
    "agent_image_uri",
    "enable_api_access_logs",
)


def contexte_jauge(app: App) -> dict:
    """Extrait les paramètres `jauge.*` du contexte CDK, sous ses deux formes.

    `cdk synth -c jauge.region=eu-west-1` pose une clé **littérale** nommée
    « jauge.region » ; un `cdk.json` peut au contraire porter un bloc imbriqué
    `{"jauge": {...}}`. Les deux formes sont lues, la clé littérale étant
    prioritaire (c'est celle de la ligne de commande).

    Sans cette double lecture, `try_get_context("jauge")` ne voyait rien et
    **tous** les paramètres documentés dans le README étaient ignorés en
    silence : région, budget, domaine du portail retombaient sur les valeurs par
    défaut. Constaté le 2026-11-11 par `cdk synth -c jauge.region=eu-west-1`,
    qui synthétisait quand même la pile dans la région ambiante.
    """
    bloc: dict = {}
    try:
        imbrique = app.node.try_get_context(CONTEXTE)
    except Exception:  # contexte absent : on retombe sur l'environnement
        imbrique = None
    if isinstance(imbrique, dict):
        bloc.update({str(cle): valeur for cle, valeur in imbrique.items()})
    for cle in CLES_CONTEXTE:
        try:
            valeur = app.node.try_get_context(f"{CONTEXTE}.{cle}")
        except Exception:  # clé absente
            continue
        if valeur is not None:
            bloc[cle] = valeur
    return bloc


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
