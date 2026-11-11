"""API du plan de contrôle : Lambda + API Gateway + authorizer Cognito (§11.3, §15).

La Lambda n'est qu'un **empaquetage** : le code applicatif vit dans
`src/jauge/api` (module M8) et expose une application ASGI FastAPI
(`jauge.api.app:app`). L'adaptateur `lambda/handler.py` (fourni ici) traduit un
événement proxy d'API Gateway en requête ASGI, exécute l'application et renvoie
la réponse — aucun code métier n'est dupliqué dans l'infrastructure.

Le paquet `jauge` est livré par une **couche Lambda** construite à partir de
`src/` : le code de l'API et l'adaptateur restent séparés, et la couche est
réutilisable par les autres fonctions du plan de contrôle.

Toute route sauf `/health` exige un jeton Cognito (EF-POR-01) ; le
cloisonnement par client (ENF-SEC-02) est appliqué par l'API, pas ici.
"""

from __future__ import annotations

from aws_cdk import (
    CfnOutput,
    Duration,
)
from aws_cdk import (
    aws_apigateway as apigateway,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
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
from jauge_control_plane.constructs.packaging import code_couche_jauge
from jauge_control_plane.paths import chemin_application


class Api(Construct):
    """Fonction Lambda, couche `jauge`, API Gateway et authorizer."""

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
        allowed_origins: list[str],
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.log_group = logs.LogGroup(
            self,
            "ApiLogs",
            log_group_name=f"/aws/lambda/{config.nom('api')}",
            retention=retention(config),
            removal_policy=removal_policy(),
        )
        self.layer = self._creer_couche()
        self.function = self._creer_fonction(identity, table, reports_bucket, key)
        self.rest_api = self._creer_api(identity, allowed_origins)
        CfnOutput(self, "ApiUrl", value=self.rest_api.url)

    def _creer_couche(self) -> lambda_.LayerVersion:
        """Couche `jauge` : le paquet applicatif, partagé par les fonctions."""
        return lambda_.LayerVersion(
            self,
            "JaugeLayer",
            layer_version_name=self.config.nom("jauge"),
            code=code_couche_jauge(),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="Jauge application package (src/jauge)",
        )

    def _creer_fonction(
        self,
        identity: Identity,
        table: dynamodb.ITable,
        reports_bucket: s3.IBucket,
        key: kms.IKey,
    ) -> lambda_.Function:
        """Fonction unique du plan de contrôle : adaptateur ASGI → Lambda."""
        fonction = lambda_.Function(
            self,
            "ApiFunction",
            function_name=self.config.nom("api"),
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(chemin_application("lambda")),
            layers=[self.layer],
            memory_size=1024,
            timeout=Duration.seconds(29),
            tracing=lambda_.Tracing.ACTIVE,
            log_group=self.log_group,
            environment=self._environnement(identity, table, reports_bucket),
            description="Jauge control plane API (ASGI adapter)",
        )
        table.grant_read_write_data(fonction)
        reports_bucket.grant_read_write(fonction)
        key.grant_encrypt_decrypt(fonction)
        return fonction

    def _environnement(
        self, identity: Identity, table: dynamodb.ITable, reports_bucket: s3.IBucket
    ) -> dict[str, str]:
        """Variables d'environnement : noms seuls, aucun secret."""
        return {
            "JAUGE_ENVIRONMENT": self.config.environment,
            # Choix du store : `jauge.store.creer_store()` lit ces variables.
            # Sans `JAUGE_STORE=dynamodb`, la Lambda démarrerait sur le store en
            # mémoire et perdrait tout état à chaque conteneur froid.
            "JAUGE_STORE": "dynamodb",
            "JAUGE_STATE_TABLE": table.table_name,
            "JAUGE_DYNAMO_REGION": self.config.region,
            # Jamais de mode démonstration en déploiement réel : l'identité vient
            # de Cognito, pas des en-têtes `X-Jauge-*`.
            "JAUGE_MODE_DEMO": "0",
            "JAUGE_REPORTS_BUCKET": reports_bucket.bucket_name,
            "JAUGE_USER_POOL_ID": identity.user_pool.user_pool_id,
            "JAUGE_USER_POOL_CLIENT_ID": identity.client.user_pool_client_id,
            "JAUGE_AGENT_REGION": self.config.region,
            "JAUGE_LOG_LEVEL": "INFO",
            "POWERTOOLS_SERVICE_NAME": "jauge-api",
        }

    def _creer_api(self, identity: Identity, allowed_origins: list[str]) -> apigateway.RestApi:
        """API REST : authorizer Cognito global, pré-vol CORS, journal d'accès."""
        authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self,
            "CognitoAuthorizer",
            cognito_user_pools=[identity.user_pool],
            identity_source="method.request.header.Authorization",
        )
        api = apigateway.RestApi(
            self,
            "RestApi",
            rest_api_name=self.config.nom("api"),
            description="Jauge control plane API",
            deploy_options=self._options_deploiement(),
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=allowed_origins,
                allow_headers=["Authorization", "Content-Type", "X-Jauge-Tenant"],
                allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                max_age=Duration.minutes(10),
            ),
            cloud_watch_role=self.config.enable_api_access_logs,
        )
        integration = apigateway.LambdaIntegration(self.function, proxy=True)
        self._ajouter_routes(api, integration, authorizer)
        return api

    def _options_deploiement(self) -> apigateway.StageOptions:
        """Étage unique, métriques et traces, quotas de débit bas."""
        options = {
            "stage_name": self.config.api_stage_name,
            "metrics_enabled": True,
            "logging_level": apigateway.MethodLoggingLevel.INFO,
            "data_trace_enabled": False,
            "tracing_enabled": True,
            "throttling_rate_limit": 50,
            "throttling_burst_limit": 100,
        }
        if self.config.enable_api_access_logs:
            acces = logs.LogGroup(
                self,
                "ApiAccessLogs",
                log_group_name=f"/aws/apigateway/{self.config.nom('api')}",
                retention=retention(self.config),
                removal_policy=removal_policy(),
            )
            options["access_log_destination"] = apigateway.LogGroupLogDestination(acces)
            options["access_log_format"] = apigateway.AccessLogFormat.json_with_standard_fields(
                caller=True,
                http_method=True,
                ip=True,
                protocol=True,
                request_time=True,
                resource_path=True,
                response_length=True,
                status=True,
                user=True,
            )
        return apigateway.StageOptions(**options)

    def _ajouter_routes(
        self,
        api: apigateway.RestApi,
        integration: apigateway.LambdaIntegration,
        authorizer: apigateway.IAuthorizer,
    ) -> None:
        """`/health` ouvert (sonde), tout le reste derrière Cognito."""
        sante = api.root.add_resource("health")
        sante.add_method("GET", integration, authorization_type=apigateway.AuthorizationType.NONE)
        proxy = api.root.add_resource("{proxy+}")
        proxy.add_method(
            "ANY",
            integration,
            authorization_type=apigateway.AuthorizationType.COGNITO,
            authorizer=authorizer,
        )


def retention(config: ControlPlaneConfig) -> logs.RetentionDays:
    """Rétention des journaux CloudWatch, pilotée par la configuration (ENF-OBS-01)."""
    mapping = {
        7: logs.RetentionDays.ONE_WEEK,
        14: logs.RetentionDays.TWO_WEEKS,
        30: logs.RetentionDays.ONE_MONTH,
        90: logs.RetentionDays.THREE_MONTHS,
        365: logs.RetentionDays.ONE_YEAR,
    }
    if config.log_retention_days not in mapping:
        raise ValueError(
            f"log_retention_days={config.log_retention_days} non supporté "
            f"(valeurs : {sorted(mapping)})"
        )
    return mapping[config.log_retention_days]


def removal_policy():
    """Les journaux survivent à la pile : la trace d'audit ne disparaît pas."""
    from aws_cdk import RemovalPolicy

    return RemovalPolicy.RETAIN
