"""Définition de la machine Step Functions du rejeu (EF-REJ-03, EF-REP/OBS).

La définition vit ici, en Python, et **pas** dans `infra/` : un fichier
`*.asl.json` posé sous `infra/` serait passé à `cfn-lint` par le job CI `iac`,
qui n'attend là que des gabarits CloudFormation.

Déroulé (§8.4, §11.4) :
    Echantillonnage → Rejeu (Map sur les requêtes, niveau Flex) → Evaluation
    → StockageResultats

Points structurants :
- la machine tourne **dans le compte du client**, sous le rôle que lui passe le
  gabarit CloudFormation (ENF-SEC-01) ;
- chaque état `Task` a un `Retry` (erreurs transitoires Bedrock/Lambda) et un
  `Catch` vers l'état d'échec : une panne ne laisse jamais la campagne pendue ;
- les `ResultSelector` du rejeu ne retiennent que des **mesures et des
  identifiants** — jamais le texte d'une requête ou d'une réponse
  (ENF-CONF-01) ;
- le niveau de service `flex` est porté explicitement par l'état de rejeu
  (EF-REJ-03).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jauge.common.errors import ValidationError

# Nom du niveau de service imposé au rejeu (§13.2, EF-REJ-03).
NIVEAU_SERVICE_REJEU = "flex"

# États de la machine, dans l'ordre du parcours.
ETAT_ECHANTILLONNAGE = "Echantillonnage"
ETAT_REJEU = "Rejeu"
ETAT_EVALUATION = "Evaluation"
ETAT_STOCKAGE = "StockageResultats"
ETAT_SUCCES = "Succes"
ETAT_ECHEC = "Echec"
ETAT_REJOUER_REQUETE = "RejouerRequete"

ETATS_PRINCIPAUX = (
    ETAT_ECHANTILLONNAGE,
    ETAT_REJEU,
    ETAT_EVALUATION,
    ETAT_STOCKAGE,
    ETAT_SUCCES,
    ETAT_ECHEC,
)

# Politique de reprise par défaut : trois tentatives, intervalle exponentiel.
REPRISE_DEFAUT: tuple[dict[str, Any], ...] = (
    {
        "ErrorEquals": ["States.TaskFailed", "Bedrock.ThrottlingException"],
        "IntervalSeconds": 2,
        "MaxAttempts": 3,
        "BackoffRate": 2.0,
        "JitterStrategy": "FULL",
    },
)


@dataclass(frozen=True)
class ParametresMachine:
    """Ce que le gabarit du compte client injecte dans la définition.

    Les valeurs par défaut sont des **placeholders de documentation** : le
    déploiement réel les remplace par les ARN produits par CloudFormation.
    """

    role_arn: str = "arn:aws:iam::123456789012:role/jauge-replay-state-machine"
    fonction_echantillonnage: str = (
        "arn:aws:lambda:eu-west-1:123456789012:function:jauge-echantillonnage"
    )
    fonction_rejeu: str = "arn:aws:lambda:eu-west-1:123456789012:function:jauge-rejeu-requete"
    fonction_evaluation: str = "arn:aws:lambda:eu-west-1:123456789012:function:jauge-evaluation"
    fonction_stockage: str = (
        "arn:aws:lambda:eu-west-1:123456789012:function:jauge-stockage-resultats"
    )
    table_resultats: str = "jauge-resultats-rejeu"
    max_concurrence: int = 8
    reprise: tuple[dict[str, Any], ...] = field(default=REPRISE_DEFAUT)


def _etat_echantillonnage(parametres: ParametresMachine) -> dict[str, Any]:
    """État 1 : tirer l'échantillon (EF-REJ-01), dans le compte client."""
    return {
        "Type": "Task",
        "Comment": "Tirage stratifie reproductible (graine de la campagne).",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {
            "FunctionName": parametres.fonction_echantillonnage,
            "Payload": {
                "tenant_id.$": "$.tenant_id",
                "campagne_id.$": "$.campagne_id",
                "application_id.$": "$.application_id",
                "taille_echantillon.$": "$.taille_echantillon",
                "graine_aleatoire.$": "$.graine_aleatoire",
            },
        },
        # Seules les métadonnées remontent : aucun texte de requête (ENF-CONF-01).
        "ResultSelector": {
            "campagne_id.$": "$.Payload.campagne_id",
            "taille.$": "$.Payload.taille",
            "requetes.$": "$.Payload.requetes",
        },
        "ResultPath": "$.echantillon",
        "Retry": list(parametres.reprise),
        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.erreur", "Next": ETAT_ECHEC}],
        "Next": ETAT_REJEU,
    }


def _etat_rejouer_requete(parametres: ParametresMachine) -> dict[str, Any]:
    """Corps du Map : une requête rejouée sur la référence puis les candidats."""
    return {
        "Type": "Task",
        "Comment": "Rejeu d'une requete : 2 executions de reference + 1 par candidat.",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {
            "FunctionName": parametres.fonction_rejeu,
            "Payload": {
                "tenant_id.$": "$.tenant_id",
                "campagne_id.$": "$.campagne_id",
                "requete_id.$": "$.requete_id",
                "modele_actuel.$": "$.modele_actuel",
                "candidats.$": "$.candidats",
                "parametres_rejeu.$": "$.parametres_rejeu",
                "niveau_service": NIVEAU_SERVICE_REJEU,
            },
        },
        "ResultSelector": {
            "requete_id.$": "$.Payload.requete_id",
            "mesures.$": "$.Payload.mesures",
        },
        "ResultPath": "$.resultat_requete",
        "Retry": list(parametres.reprise),
        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.erreur", "Next": ETAT_ECHEC}],
        "End": True,
    }


def _etat_rejeu(parametres: ParametresMachine) -> dict[str, Any]:
    """État 2 : Map sur les requêtes de l'échantillon, au niveau Flex."""
    return {
        "Type": "Map",
        "Comment": f"Rejeu distribue au niveau de service {NIVEAU_SERVICE_REJEU} (EF-REJ-03).",
        "ItemsPath": "$.echantillon.requetes",
        "MaxConcurrency": parametres.max_concurrence,
        "ItemProcessor": {
            "ProcessorConfig": {"Mode": "INLINE"},
            "StartAt": ETAT_REJOUER_REQUETE,
            "States": {ETAT_REJOUER_REQUETE: _etat_rejouer_requete(parametres)},
        },
        "ResultPath": "$.rejeu",
        "Retry": list(parametres.reprise),
        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.erreur", "Next": ETAT_ECHEC}],
        "Next": ETAT_EVALUATION,
    }


def _etat_evaluation(parametres: ParametresMachine) -> dict[str, Any]:
    """État 3 : jugement des paires et contrôles déterministes (M5)."""
    return {
        "Type": "Task",
        "Comment": "Evaluation des paires rejouees (modele juge + controles deterministes).",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {
            "FunctionName": parametres.fonction_evaluation,
            "Payload": {
                "campagne_id.$": "$.campagne_id",
                "modele_juge_id.$": "$.modele_juge_id",
                "rejeu.$": "$.rejeu",
            },
        },
        "ResultSelector": {"synthese.$": "$.Payload.synthese"},
        "ResultPath": "$.evaluation",
        "Retry": list(parametres.reprise),
        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.erreur", "Next": ETAT_ECHEC}],
        "Next": ETAT_STOCKAGE,
    }


def _etat_stockage(parametres: ParametresMachine) -> dict[str, Any]:
    """État 4 : écriture des résultats (plan de contrôle) et de la trace (client)."""
    return {
        "Type": "Task",
        "Comment": "Ecriture des resultats agreges ; la trace textuelle reste chez le client.",
        "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {
            "FunctionName": parametres.fonction_stockage,
            "Payload": {
                "campagne_id.$": "$.campagne_id",
                "table_resultats": parametres.table_resultats,
                "evaluation.$": "$.evaluation",
                "rejeu.$": "$.rejeu",
            },
        },
        "ResultSelector": {"campagne_id.$": "$.Payload.campagne_id"},
        "ResultPath": "$.stockage",
        "Retry": list(parametres.reprise),
        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.erreur", "Next": ETAT_ECHEC}],
        "Next": ETAT_SUCCES,
    }


def _etats_finaux() -> dict[str, Any]:
    """États terminaux : succès explicite et échec tracé (ENF-OBS-01)."""
    return {
        ETAT_SUCCES: {"Type": "Succeed"},
        ETAT_ECHEC: {
            "Type": "Fail",
            "Error": "RejeuEchoue",
            "Cause": "Echec de la campagne de rejeu : voir la trace d'execution.",
        },
    }


def definition(parametres: ParametresMachine | None = None) -> dict[str, Any]:
    """Définition complète de la machine à états (structure ASL, en Python)."""
    machine = parametres or ParametresMachine()
    etats: dict[str, Any] = {
        ETAT_ECHANTILLONNAGE: _etat_echantillonnage(machine),
        ETAT_REJEU: _etat_rejeu(machine),
        ETAT_EVALUATION: _etat_evaluation(machine),
        ETAT_STOCKAGE: _etat_stockage(machine),
    }
    etats.update(_etats_finaux())
    return {
        "Comment": "Jauge - campagne de rejeu (echantillon, rejeu Flex, evaluation, stockage)",
        "StartAt": ETAT_ECHANTILLONNAGE,
        "TimeoutSeconds": 3600,
        "States": etats,
    }


def role_arn(parametres: ParametresMachine | None = None) -> str:
    """Rôle IAM sous lequel la machine s'exécute (fourni par le gabarit client)."""
    return (parametres or ParametresMachine()).role_arn


def export_json(
    parametres: ParametresMachine | None = None, destination: Path | None = None
) -> str:
    """Rend la définition en JSON ; l'écrit aussi si `destination` est fourni.

    `destination` ne doit jamais pointer sous `infra/` (cf. docstring du module).
    """
    texte = json.dumps(definition(parametres), ensure_ascii=False, indent=2, sort_keys=False)
    if destination is not None:
        chemin = Path(destination)
        if "infra" in chemin.parts:
            raise ValidationError("La definition Step Functions ne va pas sous infra/.")
        chemin.write_text(texte + "\n", encoding="utf-8")
    return texte
