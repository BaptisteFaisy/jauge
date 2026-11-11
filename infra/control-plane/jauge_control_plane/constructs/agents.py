"""Agents Strands : AgentCore Runtime et AgentCore Gateway (§11.2, §12, §11.3).

Deux ressources distinctes, comme le demande le cahier des charges :

- **Runtime** : héberge les agents Strands (image conteneur) ; l'authentification
  entrante est un jeton Cognito, le même user pool que le portail — un agent
  appelé par l'API est authentifié par l'identité de l'appelant ;
- **Gateway** : expose les **outils** des agents (MCP). Les outils sont des
  fonctions Lambda ; la passerelle est chiffrée par la clé du plan de contrôle
  et n'accepte que des appelants IAM (elle n'est jamais publique).

**Hypothèse L2** : `aws_cdk.aws_bedrockagentcore` (L2 `Runtime`, `Gateway`,
`GatewayTarget`) est disponible dans `aws-cdk-lib` ≥ 2.276, la version figée
par le groupe de dépendances `infra`. Si une version antérieure du SDK est
utilisée, ces L2 n'existent pas et il faut basculer sur `CfnRuntime` /
`CfnGateway` / `CfnGatewayTarget` (mêmes propriétés CloudFormation, mappées
1:1) : le mapping L2 doit donc être **revérifié à chaque montée de version** du
SDK. Les noms de propriétés CloudFormation utilisés ici sont ceux de
`AWS::BedrockAgentCore::Runtime` et `AWS::BedrockAgentCore::Gateway`.

Les agents ne lisent que des agrégats (ENF-CONF-01) : la politique IAM du
runtime ne donne accès ni aux compartiments de journaux du client, ni au texte
des requêtes.
"""

from __future__ import annotations

from aws_cdk import (
    CfnOutput,
    Duration,
)
from aws_cdk import (
    aws_bedrockagentcore as agentcore,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct
from jauge_control_plane.config import ControlPlaneConfig
from jauge_control_plane.constructs.identity import Identity
from jauge_control_plane.paths import chemin_application

OUTILS = (
    (
        "query_cost_aggregates",
        "Return pre-computed daily cost aggregates for a tenant and date range.",
    ),
    ("list_levers", "List detected saving levers for a tenant, ranked by estimated saving."),
    ("get_replay_results", "Return replay scores and confidence intervals for a campaign."),
    (
        "save_recommendation",
        "Persist a drafted recommendation after schema validation and human approval.",
    ),
)


class Agents(Construct):
    """Runtime des agents, passerelle d'outils et fonction Lambda des outils."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ControlPlaneConfig,
        identity: Identity,
        table: dynamodb.ITable,
        reports_bucket: s3.IBucket,
        key: kms.IKey,
        layer: lambda_.ILayerVersion,
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.layer = layer
        self.tools_function = self._creer_fonction_outils(table, reports_bucket, key)
        self.gateway = self._creer_passerelle(key)
        self._cabler_outils()
        self.runtime = self._creer_runtime(identity, table, reports_bucket, key)
        CfnOutput(self, "AgentGatewayId", value=self.gateway.gateway_id)
        CfnOutput(self, "AgentRuntimeId", value=self.runtime.agent_runtime_id)

    def _creer_fonction_outils(
        self, table: dynamodb.ITable, reports_bucket: s3.IBucket, key: kms.IKey
    ) -> lambda_.Function:
        """Outils des agents : lecture d'agrégats et d'identifiants, rien d'autre."""
        fonction = lambda_.Function(
            self,
            "ToolsFunction",
            function_name=self.config.nom("agent-tools"),
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="tools.handler",
            code=lambda_.Code.from_asset(chemin_application("lambda")),
            layers=[self.layer],
            memory_size=512,
            timeout=Duration.seconds(29),
            tracing=lambda_.Tracing.ACTIVE,
            log_group=logs.LogGroup(
                self,
                "ToolsLogs",
                log_group_name=f"/aws/lambda/{self.config.nom('agent-tools')}",
                retention=logs.RetentionDays.ONE_MONTH,
            ),
            environment={
                "JAUGE_ENVIRONMENT": self.config.environment,
                "JAUGE_STATE_TABLE": table.table_name,
                "JAUGE_REPORTS_BUCKET": reports_bucket.bucket_name,
            },
            description="Jauge agent tools (aggregates and identifiers only)",
        )
        table.grant_read_data(fonction)
        reports_bucket.grant_read(fonction)
        key.grant_decrypt(fonction)
        return fonction

    def _creer_passerelle(self, key: kms.IKey) -> agentcore.Gateway:
        """Passerelle MCP privée, appelants IAM, chiffrée par la clé du plan."""
        return agentcore.Gateway(
            self,
            "ToolGateway",
            gateway_name=self.config.nom("agent-tools"),
            description="Jauge agent tools gateway (MCP, IAM auth)",
            authorizer_configuration=agentcore.GatewayAuthorizer.using_aws_iam(),
            protocol_configuration=agentcore.McpProtocolConfiguration(
                instructions="Jauge FinOps tools: aggregates, levers, replay results.",
                search_type=agentcore.McpGatewaySearchType.SEMANTIC,
                supported_versions=[agentcore.MCPProtocolVersion.MCP_2025_06_18],
            ),
            kms_key=key,
        )

    def _cabler_outils(self) -> None:
        """Déclare chaque outil dans la passerelle, avec son schéma d'entrée."""
        agentcore.GatewayTarget(
            self,
            "ToolTarget",
            gateway=self.gateway,
            gateway_target_name=self.config.nom("agent-tools"),
            description="Jauge FinOps tools backed by a Lambda function",
            target_configuration=agentcore.LambdaTargetConfiguration.create(
                self.tools_function, agentcore.ToolSchema.from_inline(schemas_outils())
            ),
        )

    def _creer_runtime(
        self, identity: Identity, table: dynamodb.ITable, reports_bucket: s3.IBucket, key: kms.IKey
    ) -> agentcore.Runtime:
        """Runtime des agents Strands : image ECR, jeton Cognito, réseau public."""
        self.runtime_role = self._role_runtime(table, reports_bucket, key)
        return agentcore.Runtime(
            self,
            "AgentRuntime",
            runtime_name=self.config.nom_agent("agents"),
            description="Jauge Strands agents (orchestrator and specialists)",
            agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_image_uri(
                self.config.image_agents
            ),
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_cognito(
                identity.user_pool, [identity.client]
            ),
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_public_network(),
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.minutes(15),
                max_lifetime=Duration.hours(8),
            ),
            protocol_configuration=agentcore.ProtocolType.HTTP,
            tracing_enabled=True,
            execution_role=self.runtime_role,
            environment_variables={
                "JAUGE_ENVIRONMENT": self.config.environment,
                "JAUGE_STATE_TABLE": table.table_name,
                "JAUGE_AGENT_GATEWAY": self.gateway.gateway_id,
            },
        )

    def _role_runtime(
        self, table: dynamodb.ITable, reports_bucket: s3.IBucket, key: kms.IKey
    ) -> iam.Role:
        """Rôle du runtime : Bedrock en lecture d'inférence, agrégats, outils."""
        role = iam.Role(
            self,
            "AgentRuntimeRole",
            role_name=self.config.nom("agents"),
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Jauge agents runtime role",
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeBedrockModels",
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:{self.config.region}::foundation-model/*",
                ],
            )
        )
        table.grant_read_data(role)
        reports_bucket.grant_read_write(role)
        key.grant_decrypt(role)
        self.gateway.grant_invoke(role)
        return role


def schemas_outils() -> list:
    """Schémas d'entrée des outils (JSON Schema, validés par la passerelle).

    Chaque outil prend `tenant_id` en premier argument : le cloisonnement
    (ENF-SEC-02) est donc imposé dès la signature exposée à l'agent.
    """
    schema = agentcore.SchemaDefinition(
        type=agentcore.SchemaDefinitionType.OBJECT,
        properties={
            "tenant_id": agentcore.SchemaDefinition(
                type=agentcore.SchemaDefinitionType.STRING, description="Client identifier"
            ),
            "application_id": agentcore.SchemaDefinition(
                type=agentcore.SchemaDefinitionType.STRING, description="Application identifier"
            ),
        },
        required=["tenant_id"],
    )
    return [
        agentcore.ToolDefinition(name=nom, description=description, input_schema=schema)
        for nom, description in OUTILS
    ]
