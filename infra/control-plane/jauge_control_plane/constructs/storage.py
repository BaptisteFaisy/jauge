"""État du plan de contrôle : clé KMS et table DynamoDB (ENF-SEC-03, §11.3).

**Modèle de données : une table unique à clé générique.**

Toutes les entités du plan de contrôle (agrégats quotidiens, campagnes,
recommandations, bascules, paramètres client, audit) vivent dans une seule table
`<préfixe>-state`, avec :

- `PK` = `TENANT#<tenant_id>` — la partition est le client : chaque lecture de
  l'API commence par le client, ce qui rend le cloisonnement (ENF-SEC-02)
  structurel et non optionnel ;
- `SK` = `<TYPE>#<identifiant ou date>` — `AGG#2026-11-11`, `CAMP#<id>`,
  `REC#<id>`, `SWITCH#<id>`, `PARAM#<clé>`, `AUDIT#<horodatage>#<id>` ;
- `GSI1PK` / `GSI1SK` — index `par-type` (`TYPE#<type>` / `<horodatage>#<id>`)
  pour la vue de parc du MSP (toutes les applications d'un client, toutes les
  recommandations en attente d'approbation).

Justification du choix « table unique » plutôt qu'une table par entité :

1. les accès sont toujours « par client, puis par entité » — un seul couple
   PK/SK les couvre, sans jointure ni `BatchGetItem` multi-tables ;
2. une seule clé KMS, une seule politique IAM, un seul point de sauvegarde
   (PITR) : moins de surface d'erreur pour un MSP qui duplique la solution
   chez plusieurs clients ;
3. une bascule et sa recommandation doivent être lues de façon cohérente
   (`TransactWriteItems` sur une même table) alors qu'elles vivraient dans deux
   tables distinctes sinon.

Le mode « provisionné » n'est pas utilisé : le trafic d'un MSP est irrégulier
(campagnes de rejeu), le mode à la demande évite tout dimensionnement.
"""

from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct
from jauge_control_plane.config import ControlPlaneConfig


class Storage(Construct):
    """Clé KMS partagée, table d'état et compartiment des rapports."""

    def __init__(self, scope: Construct, construct_id: str, *, config: ControlPlaneConfig) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.key = self._creer_cle()
        self.table = self._creer_table()
        self.reports_bucket = self._creer_compartiment_rapports()

    def _creer_cle(self) -> kms.Key:
        """Clé gérée par le client : rotation annuelle, suppression différée."""
        return kms.Key(
            self,
            "DataKey",
            alias=f"alias/{self.config.prefixe}-data",
            description="Jauge control plane - data at rest (DynamoDB, S3)",
            enable_key_rotation=True,
            pending_window=Duration.days(14),
            removal_policy=RemovalPolicy.RETAIN,
        )

    def _creer_table(self) -> dynamodb.Table:
        """Table unique PK/SK + index `par-type` (cf. docstring du module)."""
        table = dynamodb.Table(
            self,
            "StateTable",
            table_name=self.config.nom("state"),
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=self.key,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="expires_at",
            deletion_protection=self.config.environment == "prod",
            removal_policy=RemovalPolicy.RETAIN,
        )
        self._ajouter_index_par_type(table)
        return table

    @staticmethod
    def _ajouter_index_par_type(table: dynamodb.Table) -> None:
        """Index `par-type` : vue de parc du MSP, clés seulement (coût minimal)."""
        table.add_global_secondary_index(
            index_name="par-type",
            partition_key=dynamodb.Attribute(name="GSI1PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI1SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )

    def _creer_compartiment_rapports(self) -> s3.Bucket:
        """Rapports mensuels : privé, chiffré, versionné, TLS obligatoire."""
        return s3.Bucket(
            self,
            "ReportsBucket",
            bucket_name=self.config.nom("reports"),
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            minimum_tls_version=1.2,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
        )
