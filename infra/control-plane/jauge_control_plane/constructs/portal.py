"""Portail du MSP : compartiment S3 privé derrière CloudFront (EF-POR-01, §11.3).

Le portail est un site statique sans étape de build (module M13). Il est servi
par CloudFront via un **Origin Access Control** : le compartiment reste privé
(`BlockPublicAccess.BLOCK_ALL`), aucun accès public direct n'existe, et les
objets sont chiffrés au repos par la clé KMS du plan de contrôle.

Le contenu du portail (JS/CSS/HTML) n'est pas un secret, mais il est chiffré
comme le reste : une seule clé à gérer, une seule politique à auditer. La
politique de la clé autorise explicitement le principal de service CloudFront
(`cloudfront.amazonaws.com`), restreint au compte du MSP — sans quoi la
distribution ne pourrait pas lire les objets chiffrés.

Le certificat ACM du domaine personnalisé n'est **pas** créé ici : il doit
exister en `us-east-1` avec sa validation DNS (voir le README).
"""

from __future__ import annotations

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
)
from aws_cdk import (
    aws_certificatemanager as acm,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct
from jauge_control_plane.config import ControlPlaneConfig


class Portal(Construct):
    """Compartiment du portail, distribution CloudFront et son OAC."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ControlPlaneConfig,
        key: kms.IKey,
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.bucket = self._creer_compartiment(key)
        autoriser_cloudfront_sur_cle(key, config)
        self.distribution = self._creer_distribution()
        CfnOutput(self, "PortalUrl", value=f"https://{self.distribution.distribution_domain_name}")

    def _creer_compartiment(self, key: kms.IKey) -> s3.Bucket:
        """Compartiment privé, chiffré, sans hébergement de site web S3."""
        return s3.Bucket(
            self,
            "PortalBucket",
            bucket_name=self.config.nom("portal"),
            encryption=s3.BucketEncryption.KMS,
            encryption_key=key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            minimum_tls_version=1.2,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
        )

    def _creer_distribution(self) -> cloudfront.Distribution:
        """Distribution HTTPS, OAC, en-têtes de sécurité, routage SPA."""
        oac = cloudfront.S3OriginAccessControl(
            self,
            "PortalOac",
            origin_access_control_name=self.config.nom("portal"),
            signing=cloudfront.Signing.SIGV4_ALWAYS,
            description="Jauge portal origin access control",
        )
        options: dict = {
            "default_root_object": "index.html",
            "default_behavior": self._comportement_par_defaut(oac),
            "enabled": True,
            "http_version": cloudfront.HttpVersion.HTTP2_AND_3,
            "minimum_protocol_version": cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            "price_class": cloudfront.PriceClass.PRICE_CLASS_100,
            "comment": f"{self.config.prefixe} portal",
            "error_responses": [
                cloudfront.ErrorResponse(
                    http_status=403, response_http_status=200, response_page_path="/index.html"
                ),
                cloudfront.ErrorResponse(
                    http_status=404, response_http_status=200, response_page_path="/index.html"
                ),
            ],
        }
        if self.config.portal_domain_name:
            options["domain_names"] = [self.config.portal_domain_name]
            options["certificate"] = acm.Certificate.from_certificate_arn(
                self, "PortalCertificate", self.config.portal_certificate_arn or ""
            )
        distribution = cloudfront.Distribution(self, "Distribution", **options)
        if not self.config.portal_domain_name:
            # Sans domaine personnalisé, le CDK n'émet pas `ViewerCertificate` :
            # CloudFront retomberait sur TLSv1. On force TLS 1.2 minimum.
            cfn = distribution.node.default_child
            cfn.add_property_override(
                "DistributionConfig.ViewerCertificate",
                {"CloudFrontDefaultCertificate": True, "MinimumProtocolVersion": "TLSv1.2_2021"},
            )
        return distribution

    def _comportement_par_defaut(
        self, oac: cloudfront.S3OriginAccessControl
    ) -> cloudfront.BehaviorOptions:
        """Lecture seule, redirection HTTPS, en-têtes de sécurité, compression."""
        return cloudfront.BehaviorOptions(
            origin=origins.S3BucketOrigin.with_origin_access_control(
                self.bucket, origin_access_control=oac
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
            cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
            cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            compress=True,
        )


def autoriser_cloudfront_sur_cle(key: kms.IKey, config: ControlPlaneConfig) -> None:
    """Autorise le principal CloudFront à déchiffrer (restreint au compte du MSP)."""
    key.add_to_resource_policy(
        iam.PolicyStatement(
            sid="AllowCloudFrontReadEncryptedPortalObjects",
            actions=["kms:Decrypt", "kms:GenerateDataKey*"],
            resources=["*"],
            principals=[iam.ServicePrincipal("cloudfront.amazonaws.com")],
            conditions={"StringEquals": {"aws:SourceAccount": config.msp_account_id}},
        )
    )
