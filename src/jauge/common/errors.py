"""Erreurs métier de Jauge.

Hiérarchie volontairement plate : le plan API traduit ces exceptions en codes
HTTP, les agents Strands en messages utilisateur, la machine de rejeu en états
de campagne. Lever une exception standard (`ValueError`, `KeyError`) dans le
code métier est interdit : on perd le code HTTP et le message traduisible.
"""

from __future__ import annotations


class JaugeError(Exception):
    """Erreur métier de base. `code` est stable et public (contrat d'API)."""

    code: str = "jauge_error"
    statut_http: int = 500

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def vers_dict(self) -> dict[str, object]:
        """Corps d'erreur normalisé renvoyé par l'API."""
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class IntrouvableError(JaugeError):
    """Entité inexistante (client, application, campagne, recommandation…)."""

    code = "not_found"
    statut_http = 404


class ValidationError(JaugeError):
    """Donnée fournie invalide : schéma, seuil, identifiant de modèle inconnu…"""

    code = "validation_error"
    statut_http = 422


class CloisonnementError(JaugeError):
    """Tentative d'accès aux données d'un autre client (ENF-SEC-02).

    Toujours journalisée dans l'audit : c'est un événement de sécurité, pas un
    simple 403. Les tests négatifs R11 s'appuient sur cette exception.
    """

    code = "tenant_isolation_violation"
    statut_http = 403


class ApprobationRequiseError(JaugeError):
    """Action refusée faute d'approbation humaine (ENF-IA-02)."""

    code = "approval_required"
    statut_http = 409


class PlafondDepasseError(JaugeError):
    """Coût estimé d'une campagne au-dessus du plafond du client (ENF-COUT-01)."""

    code = "campaign_cost_cap_exceeded"
    statut_http = 422


class TarifInconnuError(JaugeError):
    """Aucune ligne de tarif pour (modèle, région, niveau, date).

    Levée plutôt que de facturer 0 en silence : un coût faux casse le
    rapprochement avec la facture (EF-ATT-06) sans prévenir personne.
    """

    code = "pricing_missing"
    statut_http = 422


class EtatIncompatibleError(JaugeError):
    """Transition d'état interdite (campagne terminée, bascule déjà annulée…)."""

    code = "invalid_state_transition"
    statut_http = 409


class ServiceIndisponibleError(JaugeError):
    """Dépendance externe indisponible (Bedrock, Athena, passerelle AgentCore)."""

    code = "upstream_unavailable"
    statut_http = 502
