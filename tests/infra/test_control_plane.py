"""Tests d'assertions du plan de contrôle Jauge (marqueur `infra`).

Aucun appel AWS : chaque test inspecte le gabarit CloudFormation synthétisé en
mémoire par `aws_cdk.assertions.Template.from_stack` (voir `conftest.py`).

Couverture : les composants du §11.2/§11.3 (table d'état, Cognito, CloudFront,
Lambda et API Gateway, AgentCore Runtime et Gateway, compartiments S3, KMS,
AWS Budgets, alarmes) et l'exigence « aucun compartiment public ».
"""

from __future__ import annotations

import pytest
from support import config_test, stack_test

pytestmark = pytest.mark.infra

COMPARTIMENTS = "AWS::S3::Bucket"


def _proprietes(template, type_ressource: str) -> list[dict]:
    """Propriétés de toutes les ressources d'un type donné."""
    return [r["Properties"] for r in template.find_resources(type_ressource).values()]


def test_table_etat_unique(template):
    """Une seule table DynamoDB : PK/SK génériques, chiffrée par la clé KMS."""
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "jauge-test-state",
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            "SSESpecification": {"SSEEnabled": True, "SSEType": "KMS"},
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
        },
    )


def test_index_par_type(template):
    """L'index `par-type` sert la vue de parc du MSP (clés seulement)."""
    table = _proprietes(template, "AWS::DynamoDB::Table")[0]
    index = {item["IndexName"]: item for item in table["GlobalSecondaryIndexes"]}
    assert "par-type" in index
    assert index["par-type"]["Projection"] == {"ProjectionType": "KEYS_ONLY"}


def test_cognito_user_pool_et_client(template):
    """EF-POR-01 : user pool du portail et client public (SPA, code + PKCE)."""
    template.resource_count_is("AWS::Cognito::UserPool", 1)
    template.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "UserPoolName": "jauge-test-portal",
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        },
    )
    template.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "ClientName": "jauge-test-portal-spa",
            "GenerateSecret": False,
            "AllowedOAuthFlows": ["code"],
            "AllowedOAuthFlowsUserPoolClient": True,
        },
    )


def test_cognito_groupes_de_roles(template):
    """Trois groupes : la séparation MSP / client est portée par le jeton."""
    noms = {g["GroupName"] for g in _proprietes(template, "AWS::Cognito::UserPoolGroup")}
    assert noms == {"msp-admin", "client-admin", "client-viewer"}


def test_distribution_cloudfront(template):
    """Portail servi par CloudFront : HTTPS seulement, OAC, TLS 1.2 minimum."""
    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    distribution = _proprietes(template, "AWS::CloudFront::Distribution")[0]
    assert distribution["DistributionConfig"]["DefaultRootObject"] == "index.html"
    comportement = distribution["DistributionConfig"]["DefaultCacheBehavior"]
    assert comportement["ViewerProtocolPolicy"] == "redirect-to-https"
    assert comportement["AllowedMethods"] == ["GET", "HEAD", "OPTIONS"]
    certificat = distribution["DistributionConfig"]["ViewerCertificate"]
    assert certificat["MinimumProtocolVersion"] == "TLSv1.2_2021"
    assert distribution["DistributionConfig"]["HttpVersion"] == "http2and3"


def test_cloudfront_origine_privee(template):
    """Aucun accès public : l'origine S3 est servie par OAC, jamais en HTTP."""
    distribution = _proprietes(template, "AWS::CloudFront::Distribution")[0]
    origines = distribution["DistributionConfig"]["Origins"]
    assert origines, "la distribution doit avoir au moins une origine"
    for origine in origines:
        assert "S3OriginConfig" in origine
        assert "CustomOriginConfig" not in origine
        assert origine["OriginAccessControlId"], "origine S3 servie par OAC obligatoire"


def test_lambda_api_et_couche(template):
    """Deux fonctions (API, outils des agents), toutes deux en Python 3.12."""
    template.resource_count_is("AWS::Lambda::Function", 2)
    fonctions = _proprietes(template, "AWS::Lambda::Function")
    handlers = {fonction["Handler"] for fonction in fonctions}
    assert handlers == {"handler.handler", "tools.handler"}
    for fonction in fonctions:
        assert fonction["Runtime"] == "python3.12"
        assert fonction["Architectures"] == ["arm64"]
        assert fonction["Layers"], "le paquet jauge est fourni par une couche"


def test_api_gateway_cognito(template):
    """L'API exige un jeton Cognito ; seule `/health` reste ouverte."""
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    template.has_resource_properties("AWS::ApiGateway::Authorizer", {"Type": "COGNITO_USER_POOLS"})
    methodes = _proprietes(template, "AWS::ApiGateway::Method")
    types = sorted(methode.get("AuthorizationType") for methode in methodes)
    assert "COGNITO_USER_POOLS" in types
    assert "NONE" in types


def test_api_gateway_etage(template):
    """Étage unique : traces actives, journal d'accès, quotas de débit."""
    template.resource_count_is("AWS::ApiGateway::Stage", 1)
    etage = _proprietes(template, "AWS::ApiGateway::Stage")[0]
    assert etage["StageName"] == "prod"
    assert etage["TracingEnabled"] is True
    assert etage["AccessLogSetting"]
    assert etage["MethodSettings"][0]["ThrottlingRateLimit"] == 50


def test_compartiments_prives(template):
    """Deux compartiments (portail, rapports) : privés, chiffrés, versionnés."""
    compartiments = _proprietes(template, COMPARTIMENTS)
    assert len(compartiments) == 2
    noms = {compartiment["BucketName"] for compartiment in compartiments}
    assert noms == {"jauge-test-portal", "jauge-test-reports"}
    for compartiment in compartiments:
        assert compartiment["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        assert (
            compartiment["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
                "ServerSideEncryptionByDefault"
            ]["SSEAlgorithm"]
            == "aws:kms"
        )
        assert compartiment["VersioningConfiguration"] == {"Status": "Enabled"}
        assert "AccessControl" not in compartiment
        assert "WebsiteConfiguration" not in compartiment


def test_aucune_politique_publique(template):
    """Aucune politique de compartiment n'autorise un principal `*`."""
    for politique in _proprietes(template, "AWS::S3::BucketPolicy"):
        for statement in politique["PolicyDocument"]["Statement"]:
            if statement["Effect"] != "Allow":
                continue
            principal = statement.get("Principal")
            assert principal != "*", f"politique publique détectée : {statement}"
            assert principal != {"AWS": "*"}, f"politique publique détectée : {statement}"


def test_kms(template):
    """ENF-SEC-03 : une clé gérée par le client, rotation active, CloudFront autorisé."""
    template.resource_count_is("AWS::KMS::Key", 1)
    template.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})
    cle = _proprietes(template, "AWS::KMS::Key")[0]
    statements = cle["KeyPolicy"]["Statement"]
    cloudfront = [
        s for s in statements if s.get("Sid") == "AllowCloudFrontReadEncryptedPortalObjects"
    ]
    assert cloudfront, "la clé doit autoriser le principal CloudFront"
    assert cloudfront[0]["Condition"]["StringEquals"]["aws:SourceAccount"] == "123456789012"


def test_budget_mensuel(template):
    """§17.3 : budget mensuel en dollars avec notifications par courriel."""
    template.resource_count_is("AWS::Budgets::Budget", 1)
    budget = _proprietes(template, "AWS::Budgets::Budget")[0]
    assert budget["Budget"]["BudgetType"] == "COST"
    assert budget["Budget"]["TimeUnit"] == "MONTHLY"
    assert budget["Budget"]["BudgetLimit"] == {"Amount": 200, "Unit": "USD"}
    notifications = budget["NotificationsWithSubscribers"]
    seuils = {
        (n["Notification"]["Threshold"], n["Notification"]["NotificationType"])
        for n in notifications
    }
    assert seuils == {(80, "FORECASTED"), (100, "ACTUAL")}
    for notification in notifications:
        assert notification["Subscribers"] == [
            {"Address": "finops@example.com", "SubscriptionType": "EMAIL"}
        ]


def test_agentcore_runtime(template):
    """§12 : agents Strands hébergés, authentifiés par le user pool du portail."""
    template.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)
    runtime = _proprietes(template, "AWS::BedrockAgentCore::Runtime")[0]
    assert runtime["AgentRuntimeName"] == "jauge_test_agents"
    assert "ContainerConfiguration" in runtime["AgentRuntimeArtifact"]
    jwt = runtime["AuthorizerConfiguration"]["CustomJWTAuthorizer"]
    assert "cognito-idp" in str(jwt["DiscoveryUrl"])
    assert runtime["NetworkConfiguration"]["NetworkMode"] == "PUBLIC"
    assert runtime["ProtocolConfiguration"] == "HTTP"


def test_agentcore_gateway(template):
    """§11.5 : passerelle d'outils privée (IAM), chiffrée, protocole MCP."""
    template.resource_count_is("AWS::BedrockAgentCore::Gateway", 1)
    template.resource_count_is("AWS::BedrockAgentCore::GatewayTarget", 1)
    passerelle = _proprietes(template, "AWS::BedrockAgentCore::Gateway")[0]
    assert passerelle["AuthorizerType"] == "AWS_IAM"
    assert passerelle["ProtocolType"] == "MCP"
    assert passerelle["KmsKeyArn"]


def test_alarmes(template):
    """ENF-OBS-01 : alarmes sur les erreurs de l'API et les 5XX d'API Gateway."""
    alarmes = _proprietes(template, "AWS::CloudWatch::Alarm")
    assert len(alarmes) == 2
    noms = {alarme["AlarmName"] for alarme in alarmes}
    assert noms == {"jauge-test-api-errors", "jauge-test-api-5xx"}


def test_journaux_avec_retention(template):
    """ENF-OBS-01 : les journaux CloudWatch ont une rétention explicite."""
    groupes = _proprietes(template, "AWS::Logs::LogGroup")
    assert groupes, "au moins un groupe de journaux"
    for groupe in groupes:
        assert groupe.get("RetentionInDays", 0) > 0


def test_domaine_portail_personnalise():
    """Avec un domaine, la distribution porte l'alias et le certificat ACM."""
    config = config_test(
        portal_domain_name="portal.example.com",
        portal_certificate_arn="arn:aws:acm:us-east-1:123456789012:certificate/abc",
    )
    from aws_cdk.assertions import Template

    gabarit = Template.from_stack(stack_test(config))
    distribution = _proprietes(gabarit, "AWS::CloudFront::Distribution")[0]
    assert distribution["DistributionConfig"]["Aliases"] == ["portal.example.com"]


@pytest.mark.parametrize(
    ("surcharges", "message"),
    [
        ({"msp_account_id": "12"}, "msp_account_id"),
        ({"monthly_budget_usd": 0}, "monthly_budget_usd"),
        ({"log_retention_days": 0}, "log_retention_days"),
        (
            {"portal_certificate_arn": "arn:aws:acm:us-east-1::certificate/x"},
            "portal_certificate_arn",
        ),
    ],
)
def test_configuration_invalide(surcharges, message):
    """La configuration échoue tôt, avec un message explicite."""
    with pytest.raises(ValueError, match=message):
        config_test(**surcharges)


def test_retention_non_supportee():
    """Une rétention hors barème est refusée au lieu d'être arrondie en silence."""
    from aws_cdk.assertions import Template

    with pytest.raises(ValueError, match="log_retention_days"):
        Template.from_stack(stack_test(config_test(log_retention_days=45)))
