"""Modèle juge : notation de l'équivalence fonctionnelle d'une réponse (EF-EVA-01).

Références du cahier des charges :
- §13.3 : la mesure d'équivalence fonctionnelle est une métrique personnalisée
  posée à un modèle juge — « la réponse candidate rend-elle le même service que
  la réponse de référence ? » — qui rend un verdict et une explication.
- §13.5 : le juge est un modèle **différent du candidat** et **au moins aussi
  capable que le modèle actuel**. La contrainte est encodée ici (sélection et
  validation) et vérifiée par `tests/evaluation/test_judge.py`.
- §3.2 : côté Bedrock, la notation passe par une tâche d'évaluation ; quand le
  jeu de données fournit déjà les réponses, Bedrock saute l'invocation. C'est ce
  que construit `BedrockJugeClient`.

ENF-IA-01 : le juge ne rend **qu'un verdict booléen et une explication**. Aucun
nombre produit par un LLM n'entre dans un calcul : les taux, intervalles et
décisions sont calculés par `jauge.evaluation.scoring` et
`jauge.evaluation.decision`.

ENF-CONF-01 : les textes manipulés ici (`DemandeNotation`) vivent dans le plan
de données (compte client). Rien de ce module ne rend un texte au plan de
contrôle.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from string import Template
from typing import Protocol

from jauge.common.errors import ServiceIndisponibleError, ValidationError

# Note de la barre d'évaluation à partir de laquelle une réponse est jugée
# équivalente. Le seuil est dans le code (déterministe), jamais dans le prompt.
NOTE_EQUIVALENTE = 0.5

NOM_METRIQUE = "equivalence-fonctionnelle"

# Prompt de la métrique personnalisée (§13.3). Rédigé en anglais : c'est un
# message adressé au modèle juge, pas un commentaire de code.
PROMPT_EQUIVALENCE = Template(
    """You are an impartial evaluator. Compare the CANDIDATE answer with the
REFERENCE answer produced for the same user request.

Question: does the candidate answer provide the same service as the reference
answer?

Judge functional equivalence only: same decision, same extracted values, same
meaning for the end user. Ignore wording, style and length differences when the
service rendered is identical. A candidate that is wrong, incomplete, off-topic
or that breaks the expected output format is NOT equivalent.

Task type: $type_tache

<request>
$requete
</request>

<reference_answer>
$reponse_reference
</reference_answer>

<candidate_answer>
$reponse_candidate
</candidate_answer>

Answer with a JSON object and nothing else:
{"equivalent": true or false, "explication": "<one or two sentences>"}

Do not compute any rate, percentage, cost or score: only the verdict and the
explanation."""
)


@dataclass(frozen=True)
class VerdictJuge:
    """Verdict du juge sur une paire (§13.3). `explication` est conservée."""

    equivalent: bool
    explication: str = ""


@dataclass(frozen=True)
class DemandeNotation:
    """Paire soumise au juge. Reste dans le compte client (ENF-CONF-01)."""

    requete_id: str
    requete: str
    reponse_reference: str
    reponse_candidate: str
    type_tache: str = "general"


def construire_prompt(demande: DemandeNotation) -> str:
    """Prompt de notation d'une paire (métrique personnalisée, §13.3)."""
    return PROMPT_EQUIVALENCE.substitute(
        type_tache=demande.type_tache,
        requete=demande.requete,
        reponse_reference=demande.reponse_reference,
        reponse_candidate=demande.reponse_candidate,
    )


class RegistreCapacite:
    """Rangs de capacité des modèles connus (catalogue du MSP).

    Un rang plus élevé signifie « plus capable ». Le registre est **injecté** :
    le module n'embarque aucune connaissance des modèles du marché, et les
    tests peuvent fixer leur propre échelle.
    """

    def __init__(self, rangs: Mapping[str, int]) -> None:
        if not rangs:
            raise ValidationError("registre de capacité vide : impossible de choisir un juge")
        self._rangs = {modele: int(rang) for modele, rang in rangs.items()}

    def modeles(self) -> tuple[str, ...]:
        """Modèles connus, ordre stable (alphabétique)."""
        return tuple(sorted(self._rangs))

    def rang(self, modele_id: str) -> int:
        """Rang de capacité d'un modèle ; modèle inconnu → refus explicite."""
        if modele_id not in self._rangs:
            raise ValidationError("modèle absent du registre de capacité", modele_id=modele_id)
        return self._rangs[modele_id]

    def au_moins_aussi_capable(self, modele_id: str, reference_id: str) -> bool:
        """Vrai si `modele_id` est au moins aussi capable que `reference_id`."""
        return self.rang(modele_id) >= self.rang(reference_id)


def valider_juge(
    juge_id: str,
    modele_actuel: str,
    candidats: Sequence[str],
    registre: RegistreCapacite,
) -> None:
    """Vérifie la contrainte §13.5 sur un juge donné.

    Deux règles : le juge n'est jamais un candidat (sinon il se noterait
    lui-même) et il est au moins aussi capable que le modèle actuel.
    """
    if juge_id in set(candidats):
        raise ValidationError(
            "le modèle juge ne peut pas être un candidat de la campagne",
            modele_juge_id=juge_id,
        )
    if not registre.au_moins_aussi_capable(juge_id, modele_actuel):
        raise ValidationError(
            "le modèle juge doit être au moins aussi capable que le modèle actuel",
            modele_juge_id=juge_id,
            modele_actuel=modele_actuel,
        )


def choisir_juge(modele_actuel: str, candidats: Sequence[str], registre: RegistreCapacite) -> str:
    """Choisit le juge conforme à §13.5 : distinct des candidats, au moins aussi
    capable que le modèle actuel.

    Préférence : un modèle distinct du modèle actuel (un modèle qui se compare
    à lui-même n'est pas un juge indépendant), puis le plus capable — marge de
    sécurité maximale sur la notation —, puis l'ordre alphabétique pour rester
    reproductible. Si aucun autre modèle ne convient, le modèle actuel est
    retenu comme dernier recours : il satisfait « au moins aussi capable que le
    modèle actuel » et n'est jamais un candidat.
    """
    rang_actuel = registre.rang(modele_actuel)
    exclus = set(candidats)
    eligibles = [
        modele
        for modele in registre.modeles()
        if modele not in exclus and registre.rang(modele) >= rang_actuel
    ]
    if not eligibles:
        raise ValidationError(
            "aucun modèle juge conforme : tous les modèles au moins aussi "
            "capables que le modèle actuel sont des candidats",
            modele_actuel=modele_actuel,
            candidats=list(candidats),
        )
    eligibles.sort(key=lambda modele: (modele == modele_actuel, -registre.rang(modele), modele))
    return eligibles[0]


class JugeClient(Protocol):
    """Contrat du juge. Les tâches d'évaluation Bedrock notent par lots."""

    modele_id: str

    def noter_lot(self, demandes: Sequence[DemandeNotation]) -> list[VerdictJuge]: ...


class FakeJugeClient:
    """Juge fictif pour les tests et la démonstration hors AWS.

    `verdicts` fixe le verdict par `requete_id` ; à défaut, `verdict_defaut`
    s'applique. Aucun appel réseau, aucun modèle.
    """

    def __init__(
        self,
        modele_id: str = "juge-fictif",
        verdicts: Mapping[str, VerdictJuge] | None = None,
        verdict_defaut: VerdictJuge | None = None,
        strategie: Callable[[DemandeNotation], VerdictJuge] | None = None,
    ) -> None:
        self.modele_id = modele_id
        self.verdicts = dict(verdicts or {})
        self.verdict_defaut = verdict_defaut or VerdictJuge(
            equivalent=True, explication="verdict fictif de test"
        )
        self.strategie = strategie
        self.appels: list[DemandeNotation] = []

    def noter(self, demande: DemandeNotation) -> VerdictJuge:
        """Note une paire : stratégie, puis table, puis verdict par défaut."""
        self.appels.append(demande)
        if self.strategie is not None:
            return self.strategie(demande)
        return self.verdicts.get(demande.requete_id, self.verdict_defaut)

    def noter_lot(self, demandes: Sequence[DemandeNotation]) -> list[VerdictJuge]:
        """Note un lot, dans l'ordre reçu."""
        return [self.noter(demande) for demande in demandes]


def lignes_jeu_donnees(demandes: Sequence[DemandeNotation]) -> list[dict[str, object]]:
    """Enregistrements du jeu de données d'évaluation (§3.2).

    Les réponses candidates sont fournies dans le jeu : Bedrock saute alors
    l'étape d'invocation.
    """
    return [
        {
            "prompt": demande.requete,
            "referenceResponse": demande.reponse_reference,
            "category": demande.type_tache,
            "modelResponses": [{"output": demande.reponse_candidate}],
        }
        for demande in demandes
    ]


def verdict_depuis_note(note: float, explication: str = "") -> VerdictJuge:
    """Traduit une note de la barre d'évaluation en verdict (seuil dans le code)."""
    return VerdictJuge(equivalent=note >= NOTE_EQUIVALENTE, explication=explication)


class BedrockJugeClient:
    """Adaptateur réel : tâche d'évaluation Bedrock avec réponses fournies (§3.2).

    Les clients Bedrock et S3 sont **injectables** : les tests passent des faux
    clients et vérifient le déroulé complet (création de la tâche, attente,
    lecture des notes) sans réseau. Le format exact du payload et des fichiers de
    sortie se confirme en intégration (M15), pas en test unitaire.
    """

    def __init__(
        self,
        modele_id: str,
        region: str,
        modele_candidat_id: str,
        role_arn: str,
        uri_sortie_s3: str,
        nom_dataset: str,
        client: object | None = None,
        client_s3: object | None = None,
        delai_max_s: int = 3600,
        periode_sondage_s: int = 30,
    ) -> None:
        self.modele_id = modele_id
        self.region = region
        self.modele_candidat_id = modele_candidat_id
        self.role_arn = role_arn
        self.uri_sortie_s3 = uri_sortie_s3
        self.nom_dataset = nom_dataset
        self._client = client
        self._client_s3 = client_s3
        self.delai_max_s = delai_max_s
        self.periode_sondage_s = periode_sondage_s

    def preparer_tache_evaluation(self, demandes: Sequence[DemandeNotation]) -> dict[str, object]:
        """Payload de `CreateEvaluationJob` (aucun appel réseau ici)."""
        if not demandes:
            raise ValidationError("aucune paire à noter : tâche d'évaluation vide")
        return {
            "jobName": f"jauge-{self.nom_dataset}",
            "roleArn": self.role_arn,
            "evaluationConfig": {
                "automated": {
                    "datasetMetricConfigs": [
                        {
                            "taskType": "General",
                            "dataset": {"name": self.nom_dataset},
                            "metricNames": [NOM_METRIQUE],
                            "customMetric": {
                                "metricName": NOM_METRIQUE,
                                "metricDefinition": PROMPT_EQUIVALENCE.template,
                                "ratingScale": [
                                    {"definition": "different", "value": {"floatValue": 0.0}},
                                    {"definition": "equivalent", "value": {"floatValue": 1.0}},
                                ],
                            },
                        }
                    ]
                }
            },
            "inferenceConfig": {
                "models": [
                    {
                        "precomputedInferenceSource": {
                            "inferenceSourceIdentifier": self.modele_candidat_id
                        }
                    }
                ]
            },
            "outputDataConfig": {"s3Uri": self.uri_sortie_s3},
        }

    def noter_lot(self, demandes: Sequence[DemandeNotation]) -> list[VerdictJuge]:
        """Crée la tâche, attend sa fin, lit les notes (réseau : hors tests)."""
        client = self._client or self._creer_client()
        try:
            job = client.create_evaluation_job(**self.preparer_tache_evaluation(demandes))
            arn = job["jobArn"]
            self._attendre_fin(client, arn)
            return self._lire_verdicts(arn)
        except ServiceIndisponibleError:
            raise
        except Exception as erreur:  # boto3 lève des erreurs opaques : on traduit
            raise ServiceIndisponibleError(
                "tâche d'évaluation Bedrock en échec", cause=str(erreur)
            ) from erreur

    def _creer_client(self) -> object:
        import boto3  # import tardif : aucun boto3 chargé en test

        return boto3.client("bedrock", region_name=self.region)

    def _attendre_fin(self, client: object, arn: str) -> None:
        echeance = time.monotonic() + self.delai_max_s
        while time.monotonic() < echeance:
            etat = client.get_evaluation_job(jobIdentifier=arn)["status"]
            if etat == "Completed":
                return
            if etat in {"Failed", "Stopped"}:
                raise ServiceIndisponibleError(
                    "tâche d'évaluation Bedrock interrompue", statut=etat
                )
            time.sleep(self.periode_sondage_s)
        raise ServiceIndisponibleError("délai d'attente de la tâche d'évaluation dépassé")

    def _lire_verdicts(self, arn: str) -> list[VerdictJuge]:
        """Lit les notes écrites par la tâche d'évaluation dans S3."""
        s3 = self._client_s3 or self._creer_client_s3()
        prefixe = self.uri_sortie_s3.removeprefix("s3://")
        bucket, _, chemin = prefixe.partition("/")
        reponse = s3.list_objects_v2(Bucket=bucket, Prefix=f"{chemin}/{arn.split('/')[-1]}")
        verdicts: list[VerdictJuge] = []
        for objet in reponse.get("Contents", []):
            corps = s3.get_object(Bucket=bucket, Key=objet["Key"])["Body"].read()
            for ligne in corps.decode("utf-8").splitlines():
                if ligne.strip():
                    verdicts.append(verdict_depuis_enregistrement(json.loads(ligne)))
        return verdicts

    def _creer_client_s3(self) -> object:
        import boto3  # import tardif : aucun boto3 chargé en test

        return boto3.client("s3", region_name=self.region)


def verdict_depuis_enregistrement(enregistrement: Mapping[str, object]) -> VerdictJuge:
    """Traduit un enregistrement de sortie d'évaluation en verdict (§13.3).

    Bedrock rend une note par métrique ; l'explication éventuelle est conservée.
    """
    resultats = enregistrement.get("evaluationResults") or []
    premier = resultats[0] if resultats else {}
    note = float(premier.get("result", 0.0))
    return verdict_depuis_note(note, str(premier.get("explanation", "")))
