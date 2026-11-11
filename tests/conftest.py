"""Fixtures partagées des tests Jauge.

Ces fixtures sont le socle commun : un client, deux applications, une table de
tarifs datée. Les tests de chaque module les réutilisent plutôt que de
recréer leurs propres objets (zéro duplication).

Ce fichier est **verrouillé** : un module ne le modifie pas. Besoin d'une
fixture spécifique ? La définir dans le fichier de test du module.
"""

from __future__ import annotations

from datetime import date

import pytest

from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.tenant import Application, FormuleOffre, Tenant

# Modèles fictifs de la démonstration (aucun identifiant réel de production).
GRAND_MODELE = "anthropic.claude-opus-4-20250514-v1:0"
PETIT_MODELE = "anthropic.claude-3-5-haiku-20241022-v1:0"
MOYEN_MODELE = "anthropic.claude-3-5-sonnet-20241022-v2:0"
REGION = "eu-west-1"


@pytest.fixture
def tenant() -> Tenant:
    """Client de démonstration A."""
    return Tenant(
        id="t-demo-a",
        nom="Client A — Démo",
        compte_aws="123456789012",
        region_principale=REGION,
        role_arn="arn:aws:iam::123456789012:role/jauge-dataplane",
        external_id="ext-demo-a",
        formule=FormuleOffre.OPTIMISATION_CONTINUE,
        plafond_campagne_usd=50.0,
        modeles_autorises=[GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE],
    )


@pytest.fixture
def tenant_b() -> Tenant:
    """Client de démonstration B (vue du parc)."""
    return Tenant(
        id="t-demo-b",
        nom="Client B — Démo",
        compte_aws="210987654321",
        region_principale=REGION,
        role_arn="arn:aws:iam::210987654321:role/jauge-dataplane",
        external_id="ext-demo-b",
        formule=FormuleOffre.VISIBILITE,
        plafond_campagne_usd=20.0,
    )


@pytest.fixture
def app_classement(tenant: Tenant) -> Application:
    """Classement de tickets sur un grand modèle : levier « surdimensionné »."""
    return Application(
        id="ticket-classifier",
        tenant_id=tenant.id,
        nom="Classement de tickets",
        equipe="support",
        environnement="production",
        modele_actuel=GRAND_MODELE,
        seuil_qualite=0.95,
        seuil_intervalle_bas=0.90,
        description_tache="Classification en 6 catégories, sortie JSON",
    )


@pytest.fixture
def app_assistant(tenant: Tenant) -> Application:
    """Assistant de support : candidat trop faible attendu (scénario R5)."""
    return Application(
        id="support-assistant",
        tenant_id=tenant.id,
        nom="Assistant de support",
        equipe="support",
        environnement="production",
        modele_actuel=GRAND_MODELE,
        seuil_qualite=0.97,
        description_tache="Réponse rédigée au client, multi-sujets",
    )


@pytest.fixture
def tarifs() -> list[TarifModele]:
    """Table des tarifs datée (Annexe D) : deux modèles, deux niveaux."""
    return [
        TarifModele(
            modele_id=GRAND_MODELE,
            region=REGION,
            niveau=ServiceTier.STANDARD,
            date_effet=date(2026, 1, 1),
            prix_entree_1k=0.015,
            prix_sortie_1k=0.075,
            prix_cache_ecriture_1k=0.01875,
            prix_cache_lecture_1k=0.0015,
        ),
        TarifModele(
            modele_id=GRAND_MODELE,
            region=REGION,
            niveau=ServiceTier.FLEX,
            date_effet=date(2026, 1, 1),
            prix_entree_1k=0.0075,
            prix_sortie_1k=0.0375,
        ),
        TarifModele(
            modele_id=PETIT_MODELE,
            region=REGION,
            niveau=ServiceTier.STANDARD,
            date_effet=date(2026, 1, 1),
            prix_entree_1k=0.0008,
            prix_sortie_1k=0.004,
        ),
        TarifModele(
            modele_id=PETIT_MODELE,
            region=REGION,
            niveau=ServiceTier.FLEX,
            date_effet=date(2026, 1, 1),
            prix_entree_1k=0.0004,
            prix_sortie_1k=0.002,
        ),
        TarifModele(
            modele_id=MOYEN_MODELE,
            region=REGION,
            niveau=ServiceTier.STANDARD,
            date_effet=date(2026, 1, 1),
            prix_entree_1k=0.003,
            prix_sortie_1k=0.015,
        ),
    ]


@pytest.fixture(autouse=True)
def aws_hors_ligne(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Coupe toute découverte de configuration AWS pendant les tests.

    Règle du dépôt : **aucun test ne touche AWS ni le réseau**. Or botocore lit
    `~/.aws/config`, les variables `AWS_*` et les métadonnées EC2 dès qu'un
    client est construit — y compris indirectement, quand un agent Strands
    instancie son modèle. Constaté le 2026-11-11 : une simple configuration
    `~/.aws/config` posée par un autre chantier a fait échouer 12 tests de
    `tests/agents/` avec `MissingDependencyException: botocore[crt]`.

    Cette fixture rend la suite **hermétique** : elle pointe les fichiers de
    configuration vers un chemin inexistant, désactive IMDS et purge les
    identifiants ambiants. Elle s'applique à tous les tests (autouse).
    """
    inexistant = str(tmp_path_factory.mktemp("aws-vide") / "config-absent")
    monkeypatch.setenv("AWS_CONFIG_FILE", inexistant)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", inexistant)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    for variable in (
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SSO_SESSION",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        monkeypatch.delenv(variable, raising=False)
