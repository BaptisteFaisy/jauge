"""Tests du gabarit CloudFormation d'onboarding client (module M11).

Le gabarit `infra/client-account/template.yaml` porte les exigences
EF-ATT-01 (§8.1), ENF-SEC-01 (rôle inter-comptes à moindre privilège avec
identifiant externe), ENF-SEC-03 (chiffrement KMS), ENF-REP-01 (un client =
un modèle) et §16 (le rôle du MSP n'a aucun droit de lecture sur les
compartiments de journaux et de résultats).

Ces tests lisent le YAML et vérifient les propriétés de sécurité : ils ne
parlent à aucun service AWS et ne créent aucune ressource. Marqueur `infra`
pour rester hors du chemin critique de la CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.infra

RACINE = Path(__file__).resolve().parents[2]
GABARIT = RACINE / "infra" / "client-account" / "template.yaml"

# Actions S3 qui donnent accès au contenu d'un objet ou à la liste d'un bucket.
ACTIONS_LECTURE_S3 = (
    "s3:GetObject",
    "s3:GetObjectVersion",
    "s3:ListBucket",
    "s3:ListBucketVersions",
)


class _Chargeur(yaml.SafeLoader):
    """Chargeur YAML qui conserve les étiquettes CloudFormation (!Ref, !Sub…)."""


def _construire_etiquette(chargeur: _Chargeur, etiquette: str, noeud: yaml.Node) -> dict[str, Any]:
    """Rend une étiquette inconnue sous la forme {« !Sub »: valeur}."""
    cle = f"!{etiquette}"
    if isinstance(noeud, yaml.ScalarNode):
        return {cle: chargeur.construct_scalar(noeud)}
    if isinstance(noeud, yaml.SequenceNode):
        return {cle: chargeur.construct_sequence(noeud, deep=True)}
    return {cle: chargeur.construct_mapping(noeud, deep=True)}


_Chargeur.add_multi_constructor("!", _construire_etiquette)


def charger_gabarit() -> dict[str, Any]:
    """Charge le gabarit en conservant les fonctions intrinsèques."""
    return yaml.load(GABARIT.read_text(encoding="utf-8"), Loader=_Chargeur)  # noqa: S506


def ressources(gabarit: dict[str, Any], type_attendu: str) -> dict[str, Any]:
    """Rend {nom logique: propriétés} des ressources du type demandé."""
    return {
        nom: definition.get("Properties", {})
        for nom, definition in gabarit["Resources"].items()
        if definition.get("Type") == type_attendu
    }


def seule_ressource(gabarit: dict[str, Any], type_attendu: str) -> dict[str, Any]:
    """Rend les propriétés de l'unique ressource du type demandé."""
    trouvees = ressources(gabarit, type_attendu)
    assert len(trouvees) == 1, f"attendu 1 {type_attendu}, trouvé {len(trouvees)}"
    return next(iter(trouvees.values()))


def statements(role: dict[str, Any], document: dict[str, Any]) -> list[dict[str, Any]]:
    """Concatène les déclarations d'un document de politique."""
    trouvees = document.get("Statement", [])
    return trouvees if isinstance(trouvees, list) else [trouvees]


def role_msp(gabarit: dict[str, Any]) -> dict[str, Any]:
    """Cherche le rôle dont la politique d'approbation nomme un compte AWS mandant."""
    candidats = [
        proprietes
        for proprietes in ressources(gabarit, "AWS::IAM::Role").values()
        if "MspAccountId" in texte(proprietes.get("AssumeRolePolicyDocument", ""))
    ]
    assert len(candidats) == 1, "un seul rôle inter-comptes attendu"
    return candidats[0]


def politiques_du_role(role: dict[str, Any]) -> list[dict[str, Any]]:
    """Rend les déclarations des politiques de permissions du rôle (hors approbation)."""
    declarations: list[dict[str, Any]] = []
    for politique in role.get("Policies", []):
        declarations.extend(statements(role, politique["PolicyDocument"]))
    return declarations


def texte(valeur: Any) -> str:
    """Aplatit une valeur YAML en texte pour les recherches de sous-chaînes."""
    return str(valeur)


def actions(declaration: dict[str, Any]) -> list[str]:
    """Normalise le champ Action d'une déclaration en liste de chaînes."""
    brut = declaration.get("Action", [])
    return [brut] if isinstance(brut, str) else list(brut)


def ressources_de(declaration: dict[str, Any]) -> list[str]:
    """Normalise le champ Resource d'une déclaration en liste de chaînes."""
    brut = declaration.get("Resource", [])
    return [texte(brut)] if isinstance(brut, str) else [texte(item) for item in brut]


def declarations_autorisees(role: dict[str, Any]) -> list[dict[str, Any]]:
    """Rend les déclarations en Allow du rôle."""
    return [item for item in politiques_du_role(role) if item.get("Effect") == "Allow"]


def declarations_refusees(role: dict[str, Any]) -> list[dict[str, Any]]:
    """Rend les déclarations en Deny du rôle."""
    return [item for item in politiques_du_role(role) if item.get("Effect") == "Deny"]


def lectures_s3(role: dict[str, Any]) -> list[tuple[str, str]]:
    """Rend (action, ressource) de chaque droit de lecture S3 accordé au rôle."""
    couples: list[tuple[str, str]] = []
    for declaration in declarations_autorisees(role):
        for action in actions(declaration):
            if action not in ACTIONS_LECTURE_S3:
                continue
            couples.extend((action, cible) for cible in ressources_de(declaration))
    return couples


# ---------------------------------------------------------------------------
# Un client = un modèle (ENF-REP-01)
# ---------------------------------------------------------------------------


def test_un_seul_gabarit_pour_un_client() -> None:
    """Le dossier ne contient qu'un gabarit : l'onboarding tient en un fichier."""
    gabarits = sorted(
        chemin.name
        for chemin in (RACINE / "infra" / "client-account").iterdir()
        if chemin.suffix in {".yaml", ".yml", ".json"} and chemin.is_file()
    )
    assert gabarits == ["template.yaml"]


def test_gabarit_declare_les_entrees_attendues() -> None:
    """Les paramètres d'entrée couvrent compte MSP, identifiant externe, rétention, bucket."""
    parametres = charger_gabarit()["Parameters"]
    for nom in ("MspAccountId", "ExternalId", "EnvironmentName", "DataRegion"):
        assert nom in parametres, f"paramètre {nom} absent"
    assert parametres["MspAccountId"]["AllowedPattern"] == r"^\d{12}$"
    assert parametres["ExternalId"]["MinLength"] >= 16
    assert parametres["RetentionDays"]["AllowedValues"] == [7, 14, 30, 60, 90, 180, 365, 400, 731]
    assert "LogsBucketName" in parametres


def test_identifiant_externe_jamais_en_clair_dans_le_gabarit() -> None:
    """Aucune valeur d'identifiant externe n'est écrite en dur : c'est un paramètre."""
    contenu = GABARIT.read_text(encoding="utf-8")
    assert "!Ref ExternalId" in contenu
    assert "sts:ExternalId" in contenu


# ---------------------------------------------------------------------------
# Rôle inter-comptes (ENF-SEC-01, §16)
# ---------------------------------------------------------------------------


def test_role_inter_comptes_exige_l_identifiant_externe() -> None:
    """Le compte MSP est le seul mandant et doit fournir l'identifiant externe."""
    role = role_msp(charger_gabarit())
    approbation = role["AssumeRolePolicyDocument"]["Statement"]
    declaration = next(item for item in approbation if item["Action"] == "sts:AssumeRole")
    principal = texte(declaration["Principal"]["AWS"])
    assert ":iam::${MspAccountId}:root" in principal, principal
    condition = declaration["Condition"]["StringEquals"]
    assert condition["sts:ExternalId"] == {"!Ref": "ExternalId"}


def test_role_msp_n_a_aucun_droit_de_lecture_sur_les_journaux() -> None:
    """Point de sécurité central : ni GetObject ni ListBucket sur les journaux."""
    role = role_msp(charger_gabarit())
    for action, cible in lectures_s3(role):
        assert "-athena-results" in cible, f"{action} accordé hors du bucket d'agrégats : {cible}"
        assert "-replay-results" not in cible, f"{action} accordé sur les résultats : {cible}"


def test_role_msp_refuse_explicitement_les_compartiments_sensibles() -> None:
    """Un Deny explicite couvre journaux et résultats de rejeu, en plus de l'absence d'Allow."""
    role = role_msp(charger_gabarit())
    refus = [item for item in declarations_refusees(role) if "s3:*" in actions(item)]
    assert refus, "aucun refus explicite sur les compartiments de journaux"
    cibles = " ".join(ressources_de(refus[0]))
    assert "${LogsBucketName}/*" in cibles
    assert "-replay-results" in cibles


def test_role_msp_ne_peut_pas_invoquer_de_modele() -> None:
    """Le rejeu tourne dans le compte client : le MSP n'appelle aucun modèle."""
    role = role_msp(charger_gabarit())
    toutes = " ".join(action for item in politiques_du_role(role) for action in actions(item))
    assert "bedrock:" not in toutes


def test_role_msp_limite_a_athena_glue_states_et_cost_explorer() -> None:
    """Aucun droit S3 générique, aucun droit d'écriture hors sortie de requête."""
    role = role_msp(charger_gabarit())
    for declaration in declarations_autorisees(role):
        for action in actions(declaration):
            assert action != "s3:*", "droit S3 générique interdit"
            assert not action.startswith(("iam:", "ec2:", "organizations:")), action
    prefixes = {
        action.split(":")[0] for item in declarations_autorisees(role) for action in actions(item)
    }
    assert prefixes == {"athena", "glue", "s3", "states", "ce"}, prefixes


def test_role_msp_lance_la_machine_de_rejeu() -> None:
    """Le seul pouvoir d'action du MSP : démarrer une exécution de rejeu."""
    role = role_msp(charger_gabarit())
    demarrages = [
        item for item in declarations_autorisees(role) if "states:StartExecution" in actions(item)
    ]
    assert len(demarrages) == 1
    assert "jauge-replay-${EnvironmentName}" in " ".join(ressources_de(demarrages[0]))


# ---------------------------------------------------------------------------
# Chiffrement et compartiment des journaux (ENF-SEC-03, ENF-CONF-03)
# ---------------------------------------------------------------------------


def _bucket_journaux(gabarit: dict[str, Any]) -> dict[str, Any]:
    """Rend les propriétés du bucket des journaux (celui dont le nom est le paramètre)."""
    for proprietes in ressources(gabarit, "AWS::S3::Bucket").values():
        if proprietes.get("BucketName") == {"!Ref": "LogsBucketName"}:
            return proprietes
    raise AssertionError("bucket des journaux introuvable")


def test_bucket_journaux_chiffre_par_une_cle_kms() -> None:
    """Chiffrement au repos par une clé KMS dédiée, clé de bucket activée."""
    bucket = _bucket_journaux(charger_gabarit())
    chiffrement = bucket["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
    assert chiffrement["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"
    assert "KMSMasterKeyID" in chiffrement["ServerSideEncryptionByDefault"]
    assert chiffrement["BucketKeyEnabled"] is True


def test_bucket_journaux_bloque_tout_acces_public() -> None:
    """Les quatre verrous PublicAccessBlock sont armés, propriété des objets imposée."""
    bucket = _bucket_journaux(charger_gabarit())
    verrous = bucket["PublicAccessBlockConfiguration"]
    assert all(
        verrous[cle] is True
        for cle in (
            "BlockPublicAcls",
            "BlockPublicPolicy",
            "IgnorePublicAcls",
            "RestrictPublicBuckets",
        )
    )
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"
    assert bucket["OwnershipControls"]["Rules"][0]["ObjectOwnership"] == "BucketOwnerEnforced"


def test_bucket_journaux_expire_selon_la_retention() -> None:
    """La durée de conservation est un paramètre (ENF-CONF-03)."""
    bucket = _bucket_journaux(charger_gabarit())
    regle = bucket["LifecycleConfiguration"]["Rules"][0]
    assert regle["Status"] == "Enabled"
    assert regle["ExpirationInDays"] == {"!Ref": "RetentionDays"}


def test_cle_kms_rotation_et_administration() -> None:
    """Rotation activée et administration réservée au compte client."""
    cle = seule_ressource(charger_gabarit(), "AWS::KMS::Key")
    assert cle["EnableKeyRotation"] is True
    admin = cle["KeyPolicy"]["Statement"][0]
    assert admin["Action"] == "kms:*"
    assert ":iam::${AWS::AccountId}:root" in texte(admin["Principal"]["AWS"])


# ---------------------------------------------------------------------------
# Analyse : Glue, Athena, rejeu, budget, alarmes
# ---------------------------------------------------------------------------


def test_glue_expose_journaux_cur2_et_agregats() -> None:
    """Trois tables : journaux d'invocation, export CUR 2.0 et agrégats."""
    gabarit = charger_gabarit()
    assert len(ressources(gabarit, "AWS::Glue::Database")) == 1
    tables = ressources(gabarit, "AWS::Glue::Table")
    noms = {table["TableInput"]["Name"] for table in tables.values()}
    assert noms == {"invocation_logs", "cur2", "aggregates"}
    colonnes = {
        c["Name"]
        for c in tables["InvocationLogsTable"]["TableInput"]["StorageDescriptor"]["Columns"]
    }
    assert {"application", "team", "environment", "input_tokens", "output_tokens"} <= colonnes


def test_athena_workgroup_borne_les_octets_lus() -> None:
    """Le groupe de travail impose ses réglages et plafonne les données lues."""
    groupe = seule_ressource(charger_gabarit(), "AWS::Athena::WorkGroup")
    configuration = groupe["WorkGroupConfiguration"]
    assert configuration["EnforceWorkGroupConfiguration"] is True
    assert configuration["BytesScannedCutoffPerQuery"] == {"!Ref": "AthenaBytesScannedCutoff"}
    assert "-athena-results" in texte(configuration["ResultConfiguration"]["OutputLocation"])


def test_table_d_agregats_seulement_hors_des_journaux() -> None:
    """La table que lit le MSP ne pointe pas sur les journaux : agrégats uniquement."""
    tables = ressources(charger_gabarit(), "AWS::Glue::Table")
    agregats = tables["AggregatesTable"]["TableInput"]["StorageDescriptor"]["Location"]
    emplacement = texte(agregats)
    assert "-athena-results" in emplacement, emplacement
    assert "BedrockModelInvocationLogs" not in emplacement
    journaux = tables["InvocationLogsTable"]["TableInput"]["StorageDescriptor"]["Location"]
    assert "BedrockModelInvocationLogs" in texte(journaux)


def test_machine_de_rejeu_et_son_role() -> None:
    """La machine d'états rejoue par candidat et échoue explicitement sinon."""
    gabarit = charger_gabarit()
    machine = seule_ressource(gabarit, "AWS::StepFunctions::StateMachine")
    definition = machine["DefinitionString"]["!Sub"]
    for attendu in ('"Type": "Map"', '"Type": "Fail"', '"Type": "Succeed"', "PreparerEchantillon"):
        assert attendu in definition, attendu
    assert machine["TracingConfiguration"]["Enabled"] is True
    assert "ReplayExecutionRole" in texte(machine["RoleArn"])
    assert len(ressources(gabarit, "AWS::Lambda::Function")) == 1


def test_definition_step_functions_est_un_json_valide() -> None:
    """La définition inline reste du JSON une fois la substitution faite."""
    machine = seule_ressource(charger_gabarit(), "AWS::StepFunctions::StateMachine")
    definition = machine["DefinitionString"]["!Sub"]
    # Remplace les pseudo-paramètres par des valeurs plausibles avant analyse.
    for marqueur in ("${ReplayWorkerFunction.Arn}",):
        definition = definition.replace(
            marqueur, "arn:aws:lambda:eu-west-1:123456789012:function:x"
        )
    etats = json.loads(definition)["States"]
    assert etats["RejouerModeles"]["Type"] == "Map"
    assert etats["Termine"] == {"Type": "Succeed"}
    assert etats["Echec"]["Type"] == "Fail"


def test_alarme_budgets_presente() -> None:
    """Alerte AWS Budgets du §17.3 : budget mensuel, seuils 80 % réel et 100 % prévu."""
    budget = seule_ressource(charger_gabarit(), "AWS::Budgets::Budget")
    corps = budget["Budget"]
    assert corps["BudgetType"] == "COST"
    assert corps["TimeUnit"] == "MONTHLY"
    assert corps["BudgetLimit"]["Amount"] == {"!Ref": "MonthlyBudgetUsd"}
    seuils = {
        (n["Notification"]["NotificationType"], n["Notification"]["Threshold"])
        for n in budget["NotificationsWithSubscribers"]
    }
    assert seuils == {("ACTUAL", 80), ("FORECASTED", 100)}


def test_alarmes_cloudwatch_sur_les_echecs() -> None:
    """ENF-OBS-01 : échec d'exécution et erreur du moteur de rejeu sont alarmés."""
    gabarit = charger_gabarit()
    alarmes = ressources(gabarit, "AWS::CloudWatch::Alarm")
    metriques = {(a["Namespace"], a["MetricName"]) for a in alarmes.values()}
    assert ("AWS/States", "ExecutionsFailed") in metriques
    assert ("AWS/Lambda", "Errors") in metriques
    for alarme in alarmes.values():
        assert alarme["AlarmActions"], "alarme sans action"
    journaux = seule_ressource(gabarit, "AWS::Logs::LogGroup")
    assert journaux["RetentionInDays"] == {"!Ref": "RetentionDays"}


def test_sorties_pour_le_plan_de_controle() -> None:
    """Les sorties donnent au MSP tout ce dont il a besoin, sans aucun secret."""
    sorties = charger_gabarit()["Outputs"]
    for nom in ("RoleArn", "LogsBucketName", "AthenaWorkGroupName", "ReplayStateMachineArn"):
        assert nom in sorties, nom
    assert "ExternalId" not in {cle for cle in sorties if cle != "ExternalIdParameter"}
