"""Pile CloudFormation du plan de contrôle Jauge (ENF-REP-01, §11.2).

Une seule pile, parce qu'un MSP installe la solution en une commande
(`cdk deploy`) et que les composants sont fortement couplés (le portail appelle
l'API, l'API utilise le user pool Cognito, les agents utilisent la passerelle et
la table). Le plan de données du client reste dans **son** compte, déployé par
le gabarit du module M11 : rien de sensible ne traverse cette pile.

Ordre de construction (les dépendances vont dans ce sens) :
stockage (KMS, DynamoDB, rapports) → portail (CloudFront, S3) → identité
(Cognito) → API (Lambda, API Gateway) → agents (Runtime, Gateway) → opérations
(budget, alarmes).
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, Stack, Tags
from constructs import Construct

from jauge_control_plane.config import ControlPlaneConfig
from jauge_control_plane.constructs.agents import Agents
from jauge_control_plane.constructs.api import Api
from jauge_control_plane.constructs.identity import Identity
from jauge_control_plane.constructs.operations import Operations
from jauge_control_plane.constructs.portal import Portal
from jauge_control_plane.constructs.storage import Storage


class ControlPlaneStack(Stack):
    """Plan de contrôle du MSP : portail, API, identité, état, agents, budget."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ControlPlaneConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.storage = Storage(self, "Storage", config=config)
        self.portal = Portal(self, "Portal", config=config, key=self.storage.key)
        self.identity = Identity(
            self,
            "Identity",
            config=config,
            callback_urls=self._urls_de_rappel(),
        )
        self.api = Api(
            self,
            "Api",
            config=config,
            identity=self.identity,
            table=self.storage.table,
            reports_bucket=self.storage.reports_bucket,
            key=self.storage.key,
            allowed_origins=self._origines_autorisees(),
        )
        self.agents = Agents(
            self,
            "Agents",
            config=config,
            identity=self.identity,
            table=self.storage.table,
            reports_bucket=self.storage.reports_bucket,
            key=self.storage.key,
            layer=self.api.layer,
        )
        self.operations = Operations(self, "Operations", config=config)
        self._poser_alarmes()
        self._etiqueter()
        self._exporter()

    def _urls_de_rappel(self) -> list[str]:
        """Retour d'authentification du portail : son domaine CloudFront."""
        if self.config.portal_domain_name:
            return [f"https://{self.config.portal_domain_name}/"]
        domaine = self.portal.distribution.distribution_domain_name
        return [f"https://{domaine}/"]

    def _origines_autorisees(self) -> list[str]:
        """CORS : le portail uniquement (jamais `*` sur une API authentifiée)."""
        if self.config.portal_domain_name:
            return [f"https://{self.config.portal_domain_name}"]
        domaine = self.portal.distribution.distribution_domain_name
        return [f"https://{domaine}"]

    def _poser_alarmes(self) -> None:
        """Alarmes sur les erreurs de la fonction API et les 5XX d'API Gateway."""
        self.operations.ajouter_alarme_erreurs(self.api.function.metric_errors(), "api")
        self.operations.ajouter_alarme_5xx(
            self.api.rest_api.metric_server_error(statistic="Sum", period=Duration.minutes(5))
        )

    def _exporter(self) -> None:
        """Sorties utiles à l'exploitation (identifiants, compartiments, étage)."""
        for cle, valeur in (
            ("StateTableName", self.storage.table.table_name),
            ("PortalBucketName", self.portal.bucket.bucket_name),
            ("ReportsBucketName", self.storage.reports_bucket.bucket_name),
            ("UserPoolId", self.identity.user_pool.user_pool_id),
            ("UserPoolClientId", self.identity.client.user_pool_client_id),
            ("ApiStage", self.config.api_stage_name),
            ("AgentRuntimeRoleArn", self.agents.runtime_role.role_arn),
        ):
            CfnOutput(self, f"Out{cle}", value=valeur, description=f"Jauge control plane: {cle}")

    def _etiqueter(self) -> None:
        """Étiquettes communes : coût par projet, environnement, solution."""
        for cle, valeur in (
            ("project", "jauge"),
            ("environment", self.config.environment),
            ("component", "control-plane"),
            ("managed-by", "aws-cdk"),
        ):
            Tags.of(self).add(cle, valeur)
