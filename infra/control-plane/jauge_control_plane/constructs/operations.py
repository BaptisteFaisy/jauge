"""Exploitation : alerte de budget et alarmes (ENF-OBS-01, §17.3).

L'alerte AWS Budgets est posée **dans chaque compte** (compte du MSP ici, compte
du client dans le gabarit M11) : elle prévient à 80 % du budget mensuel en
prévision et à 100 % en réalisé. Sans adresse de notification configurée, le
budget existe quand même (il reste consultable dans la console et sert de
garde-fou aux alertes de compte), mais aucune notification n'est envoyée.

Les alarmes couvrent les deux échecs visibles du plan de contrôle : erreurs de
la fonction Lambda de l'API et réponses 5XX d'API Gateway.
"""

from __future__ import annotations

from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from constructs import Construct
from jauge_control_plane.config import ControlPlaneConfig

SEUILS = ((80, "FORECASTED"), (100, "ACTUAL"))


class Operations(Construct):
    """Budget mensuel et alarmes du plan de contrôle."""

    def __init__(self, scope: Construct, construct_id: str, *, config: ControlPlaneConfig) -> None:
        super().__init__(scope, construct_id)
        self.config = config
        self.budget = self._creer_budget()

    def _creer_budget(self) -> budgets.CfnBudget:
        """Budget mensuel en dollars, avec notifications par courriel."""
        budget = budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=self.config.nom("monthly"),
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=self.config.monthly_budget_usd, unit="USD"
                ),
            ),
        )
        if self.config.budget_alert_emails:
            budget.notifications_with_subscribers = [
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type=type_notification,
                        comparison_operator="GREATER_THAN",
                        threshold=seuil,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            address=courriel, subscription_type="EMAIL"
                        )
                        for courriel in self.config.budget_alert_emails
                    ],
                )
                for seuil, type_notification in SEUILS
            ]
        return budget

    def ajouter_alarme_erreurs(self, fonction: cloudwatch.IMetric, nom: str) -> cloudwatch.Alarm:
        """Alarme sur les erreurs d'une fonction Lambda (ENF-OBS-01)."""
        return cloudwatch.Alarm(
            self,
            f"Alarm-{nom}",
            alarm_name=self.config.nom(f"{nom}-errors"),
            alarm_description=f"{nom}: Lambda errors above threshold",
            metric=fonction,
            threshold=5,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

    def ajouter_alarme_5xx(self, metrique: cloudwatch.IMetric) -> cloudwatch.Alarm:
        """Alarme sur les réponses 5XX de l'API Gateway."""
        return cloudwatch.Alarm(
            self,
            "Alarm-Api5xx",
            alarm_name=self.config.nom("api-5xx"),
            alarm_description="Jauge API: 5XX responses above threshold",
            metric=metrique,
            threshold=5,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
