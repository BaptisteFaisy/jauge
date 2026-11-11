"""Paramètres du plan de contrôle Jauge (ENF-REP-01).

Tout ce qui change d'un MSP à l'autre (compte, région, domaine du portail,
budget) est un paramètre : la même application CDK sert un autre MSP sans
modification de code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTEXTE = "jauge"
"""Clé de contexte CDK (`cdk synth -c jauge.<clé>=<valeur>`)."""


@dataclass(frozen=True)
class ControlPlaneConfig:
    """Paramètres d'un déploiement du plan de contrôle."""

    msp_account_id: str
    region: str
    environment: str = "dev"
    portal_domain_name: str | None = None
    portal_certificate_arn: str | None = None
    monthly_budget_usd: float = 200.0
    budget_alert_emails: tuple[str, ...] = field(default_factory=tuple)
    log_retention_days: int = 30
    api_stage_name: str = "prod"
    agent_image_uri: str | None = None
    """Image conteneur des agents Strands (ECR). `None` = image de référence
    construite sur le compte et la région du déploiement."""
    enable_api_access_logs: bool = True
    """Active les journaux d'accès d'API Gateway (exige le rôle de compte
    CloudWatch, singleton par compte)."""

    def __post_init__(self) -> None:
        if not self.msp_account_id.isdigit() or len(self.msp_account_id) != 12:
            raise ValueError(f"msp_account_id invalide : {self.msp_account_id!r}")
        if not self.region:
            raise ValueError("region est obligatoire")
        if self.monthly_budget_usd <= 0:
            raise ValueError("monthly_budget_usd doit être strictement positif")
        if self.log_retention_days <= 0:
            raise ValueError("log_retention_days doit être strictement positif")
        if self.portal_certificate_arn and not self.portal_domain_name:
            raise ValueError("portal_certificate_arn exige portal_domain_name")

    @property
    def prefixe(self) -> str:
        """Préfixe commun des ressources (lisible dans la console AWS)."""
        return f"jauge-{self.environment}"

    def nom(self, suffixe: str) -> str:
        """Nom physique d'une ressource, préfixé par l'environnement."""
        return f"{self.prefixe}-{suffixe}"

    def nom_agent(self, suffixe: str) -> str:
        """Nom d'un agent : lettres, chiffres et soulignés seulement (AgentCore)."""
        return f"{self.prefixe}-{suffixe}".replace("-", "_")

    @property
    def image_agents(self) -> str:
        """URI de l'image conteneur des agents (AgentCore Runtime)."""
        if self.agent_image_uri:
            return self.agent_image_uri
        registre = f"{self.msp_account_id}.dkr.ecr.{self.region}.amazonaws.com"
        return f"{registre}/{self.nom('agents')}:latest"


def config_depuis_contexte(
    contexte: dict[str, Any] | None, compte: str, region: str
) -> ControlPlaneConfig:
    """Construit la configuration depuis le contexte CDK et l'environnement.

    Les clés du contexte ont la forme `jauge.monthly_budget_usd`. Le compte et
    la région viennent de `CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`.
    """
    valeurs = dict(contexte or {})
    courriels = valeurs.get("budget_alert_emails", ())
    if isinstance(courriels, str):
        courriels = tuple(item.strip() for item in courriels.split(",") if item.strip())
    return ControlPlaneConfig(
        msp_account_id=str(valeurs.get("msp_account_id", compte)),
        region=str(valeurs.get("region", region)),
        environment=str(valeurs.get("environment", "dev")),
        portal_domain_name=valeurs.get("portal_domain_name") or None,
        portal_certificate_arn=valeurs.get("portal_certificate_arn") or None,
        monthly_budget_usd=float(valeurs.get("monthly_budget_usd", 200.0)),
        budget_alert_emails=tuple(courriels),
        log_retention_days=int(valeurs.get("log_retention_days", 30)),
        api_stage_name=str(valeurs.get("api_stage_name", "prod")),
        agent_image_uri=valeurs.get("agent_image_uri") or None,
        enable_api_access_logs=bool(valeurs.get("enable_api_access_logs", True)),
    )
