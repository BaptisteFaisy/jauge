"""Empaquetage des fonctions Lambda du plan de contrôle (§11.3).

Deux actifs sont nécessaires :

1. **le code de la fonction** : `infra/control-plane/lambda/` (adaptateur ASGI et
   outils), déposé tel quel à la racine du zip — les handlers sont
   `handler.handler` et `tools.handler` ;
2. **la couche `jauge`** : le paquet applicatif `src/jauge` (modules M8/M7),
   qui doit atterrir dans `python/jauge` pour être importable par Lambda.

Le CDK ne réorganise pas les actifs : la couche est donc assemblée par une
liaison locale (`ILocalBundling`), **sans Docker et sans appel réseau**, qui
copie `src/jauge` vers `python/jauge` dans le répertoire de sortie. Aucun code
n'est dupliqué : le paquet livré est exactement celui du dépôt, et la synthèse
fonctionne hors ligne (les tests d'infrastructure en dépendent).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import jsii
from aws_cdk import BundlingOptions, DockerImage, ILocalBundling
from aws_cdk import aws_lambda as lambda_
from jauge_control_plane.paths import RACINE_DEPOT

PAQUET = "jauge"
"""Nom du paquet applicatif livré dans la couche."""


@jsii.implements(ILocalBundling)
class AssemblageCoucheJauge:
    """Assemble la couche Lambda au layout `python/<paquet>` attendu par AWS."""

    def try_bundle(self, output_dir: str, options: object = None) -> bool:
        """Copie `src/jauge` vers `<output_dir>/python/jauge` ; jamais d'échec doux.

        jsii appelle `try_bundle(output_dir, options)` : les options de
        liaison (image, commande) ne sont pas utilisées ici, l'assemblage est
        une simple copie de fichiers.
        """
        source = RACINE_DEPOT / "src" / PAQUET
        if not source.is_dir():
            raise FileNotFoundError(f"paquet applicatif introuvable : {source}")
        destination = Path(output_dir) / "python" / PAQUET
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return True


def code_couche_jauge() -> lambda_.Code:
    """Actif de la couche `jauge`, assemblé localement (aucun Docker requis)."""
    return lambda_.Code.from_asset(
        str(RACINE_DEPOT / "src"),
        bundling=BundlingOptions(
            image=DockerImage.from_registry("public.ecr.aws/sam/build-python3.12:latest"),
            local=AssemblageCoucheJauge(),
            command=["true"],
        ),
    )
