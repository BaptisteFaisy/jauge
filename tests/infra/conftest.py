"""Fixtures des tests d'infrastructure (marqueur `infra`).

Aucun appel AWS : la pile est synthétisée en mémoire dans une `App` de test et
inspectée par `aws_cdk.assertions.Template.from_stack`. Les fabriques
`config_test` / `stack_test` vivent dans `support.py` (importable par les tests).

Les imports CDK sont **paresseux** : le job CI `test` collecte ces modules sans
installer le groupe `infra`, il ne fait que les désélectionner.
"""

from __future__ import annotations

import pytest
from tests.infra.support import config_test, stack_test


@pytest.fixture(scope="module")
def config():
    """Configuration par défaut des assertions."""
    return config_test()


@pytest.fixture(scope="module")
def template(config):
    """Gabarit CloudFormation synthétisé en mémoire (aucun appel AWS)."""
    from aws_cdk.assertions import Template

    return Template.from_stack(stack_test(config))
