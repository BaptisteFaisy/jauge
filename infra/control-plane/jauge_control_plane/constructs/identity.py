"""Identité du portail : Cognito (EF-POR-01, §11.3).

Le portail est une application statique : le client Cognito est donc **public**
(pas de secret) et s'authentifie par le flux « authorization code + PKCE » du
point d'accès hébergé. L'inscription libre est désactivée : un MSP crée les
comptes de ses clients (rôles applicatifs dans les groupes Cognito, vérifiés
côté API par le jeton d'accès).

Le même user pool sert d'autorité pour l'API (authorizer Cognito) et pour les
agents Strands sur AgentCore Runtime (authorizer JWT du runtime) : un seul
identifiant, trois usages.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_cognito as cognito
from constructs import Construct
from jauge_control_plane.config import ControlPlaneConfig

GROUPES = ("msp-admin", "client-admin", "client-viewer")
"""Groupes Cognito : le rôle effectif est porté par le jeton (`cognito:groups`)."""


class Identity(Construct):
    """User pool, client public du portail et domaines d'authentification."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: ControlPlaneConfig,
        callback_urls: list[str],
    ) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.user_pool = self._creer_user_pool()
        self._creer_groupes()
        self.client = self._creer_client(callback_urls)
        self.domain = self.user_pool.add_domain(
            "PortalDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=config.nom("portal"),
            ),
        )

    def _creer_user_pool(self) -> cognito.UserPool:
        """Pool sans auto-inscription, MFA optionnelle, e-mail comme identifiant."""
        return cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=self.config.nom("portal"),
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            self_sign_up_enabled=False,
            mfa=cognito.Mfa.OPTIONAL,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            deletion_protection=self.config.environment == "prod",
            removal_policy=RemovalPolicy.RETAIN,
        )

    def _creer_groupes(self) -> None:
        """Un groupe par rôle : la séparation MSP / client est portée par le jeton."""
        for groupe in GROUPES:
            cognito.CfnUserPoolGroup(
                self,
                f"Group-{groupe}",
                user_pool_id=self.user_pool.user_pool_id,
                group_name=groupe,
                description=f"Jauge role: {groupe}",
            )

    def _creer_client(self, callback_urls: list[str]) -> cognito.UserPoolClient:
        """Client public (SPA) : code d'autorisation + PKCE, jetons courts."""
        return self.user_pool.add_client(
            "PortalClient",
            user_pool_client_name=self.config.nom("portal-spa"),
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=callback_urls,
                logout_urls=[url.rsplit("/", 1)[0] for url in callback_urls],
            ),
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
            prevent_user_existence_errors=True,
        )
