"""Recette R10 et R13 — sécurité du rôle inter-comptes et installation documentée.

- **R10** « Rôle du MSP sur le compartiment des journaux » → **Accès refusé**.
  Le scénario ne se joue pas sur AWS : il se vérifie sur le **gabarit
  CloudFormation** livré au client (`infra/client-account/template.yaml`). Le
  rôle inter-comptes du MSP ne doit porter **aucun** droit
  `s3:GetObject`/`s3:ListBucket` sur le compartiment des journaux ni sur celui
  des résultats de rejeu (§16, ENF-SEC-01, ENF-CONF-01).
- **R13** « Installation dans un compte vierge » → le README et
  `docs/deploy-client.md` permettent l'installation. Ce qui est testable est
  vérifié : présence et cohérence des **gabarits**, des **scripts**, des
  **commandes documentées** — chaque commande `aws`/`cdk`/`uv` citée correspond
  à un fichier qui existe, et les paramètres du script correspondent à ceux du
  gabarit.

Ce qui reste **manuel** (déploiement réel dans un compte AWS) est listé dans
`docs/RECETTE.md`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

RACINE = Path(__file__).resolve().parents[2]
GABARIT = RACINE / "infra" / "client-account" / "template.yaml"
SCRIPT = RACINE / "infra" / "client-account" / "deploy.sh"
README = RACINE / "README.md"
DOC_INSTALLATION = RACINE / "docs" / "deploy-client.md"

#: Droits de lecture du contenu d'un objet ou de la liste d'un bucket.
ACTIONS_LECTURE_S3 = (
    "s3:GetObject",
    "s3:GetObjectVersion",
    "s3:ListBucket",
    "s3:ListBucketVersions",
)

#: Répertoires de premier niveau dont les chemins cités doivent exister.
DOSSIERS_REPO = ("docs/", "infra/", "scripts/", "src/", "data/", "portal/", "tests/", "demo/")

#: (extrait de documentation, fichier ou dossier attendu) — §22 R13.
COMMANDES_DOCUMENTEES = (
    ("uv sync --all-groups", "pyproject.toml"),
    ("uv run pytest", "tests"),
    ("bash scripts/ci-local.sh", "scripts/ci-local.sh"),
    ("uv run python -m demo.seed", "demo/seed.py"),
    ("uv run uvicorn jauge.api.app:app", "src/jauge/api/app.py"),
    ("aws cloudformation deploy", "infra/client-account/template.yaml"),
    ("./infra/client-account/deploy.sh", "infra/client-account/deploy.sh"),
    ("cdk deploy", "infra/control-plane/app.py"),
    ("uv run cfn-lint infra/client-account/template.yaml", "infra/client-account/template.yaml"),
)


class _Chargeur(yaml.SafeLoader):
    """Chargeur YAML qui conserve les fonctions intrinsèques CloudFormation."""


def _etiquette_inconnue(chargeur: _Chargeur, etiquette: str, noeud: yaml.Node) -> dict[str, Any]:
    """Rend une étiquette inconnue (`!Sub`, `!Ref`…) sous forme de dictionnaire."""
    cle = f"!{etiquette}"
    if isinstance(noeud, yaml.ScalarNode):
        return {cle: chargeur.construct_scalar(noeud)}
    if isinstance(noeud, yaml.SequenceNode):
        return {cle: chargeur.construct_sequence(noeud, deep=True)}
    return {cle: chargeur.construct_mapping(noeud, deep=True)}


_Chargeur.add_multi_constructor("!", _etiquette_inconnue)


def charger_gabarit() -> dict[str, Any]:
    """Charge le gabarit client en conservant `!Ref` et `!Sub`."""
    return yaml.load(GABARIT.read_text(encoding="utf-8"), Loader=_Chargeur)  # noqa: S506


def _ressources(gabarit: dict[str, Any], type_attendu: str) -> dict[str, dict[str, Any]]:
    """Rend {nom logique: propriétés} des ressources du type demandé."""
    return {
        nom: definition.get("Properties", {})
        for nom, definition in gabarit["Resources"].items()
        if definition.get("Type") == type_attendu
    }


def _role_msp(gabarit: dict[str, Any]) -> dict[str, Any]:
    """Rend le rôle inter-comptes du MSP (celui dont l'approbation cite le compte MSP)."""
    candidats = [
        proprietes
        for proprietes in _ressources(gabarit, "AWS::IAM::Role").values()
        if "MspAccountId" in str(proprietes.get("AssumeRolePolicyDocument", ""))
    ]
    assert len(candidats) == 1, "un seul rôle inter-comptes attendu dans le gabarit"
    return candidats[0]


def _declarations(role: dict[str, Any]) -> list[dict[str, Any]]:
    """Toutes les déclarations des politiques de permissions du rôle."""
    declarations: list[dict[str, Any]] = []
    for politique in role.get("Policies", []):
        brutes = politique["PolicyDocument"].get("Statement", [])
        declarations.extend(brutes if isinstance(brutes, list) else [brutes])
    return declarations


def _actions(declaration: dict[str, Any]) -> list[str]:
    """Normalise le champ `Action` d'une déclaration en liste."""
    brut = declaration.get("Action", [])
    return [brut] if isinstance(brut, str) else list(brut)


def _cibles(declaration: dict[str, Any]) -> list[str]:
    """Normalise le champ `Resource` en liste de chaînes (les `!Sub` sont dépliés)."""
    brut = declaration.get("Resource", [])
    elements = [brut] if isinstance(brut, str) else list(brut)
    return [_texte_cible(element) for element in elements]


def _texte_cible(cible: Any) -> str:
    """Aplatit une cible CloudFormation (`!Sub`, `!Ref`) en texte comparable."""
    if isinstance(cible, dict):
        return " ".join(str(valeur) for valeur in cible.values())
    return str(cible)


def test_r10_role_msp_sans_droit_de_lecture_sur_les_journaux() -> None:
    """R10 : aucun droit de lecture S3 du rôle du MSP sur les journaux (§16)."""
    role = _role_msp(charger_gabarit())

    for declaration in _declarations(role):
        if declaration.get("Effect") != "Allow":
            continue
        for action in _actions(declaration):
            if action not in ACTIONS_LECTURE_S3:
                continue
            for cible in _cibles(declaration):
                # Le seul compartiment lisible est celui des agrégats.
                assert "-athena-results" in cible, (
                    f"droit {action} accordé hors du bucket d'agrégats : {cible}"
                )
                assert "-replay-results" not in cible, (
                    f"droit {action} accordé sur les résultats de rejeu : {cible}"
                )
                assert not cible.rstrip("/*").endswith("${LogsBucketName}"), (
                    f"droit {action} accordé sur le compartiment des journaux : {cible}"
                )


def test_r10_role_msp_refuse_explicitement_les_compartiments_sensibles() -> None:
    """R10 : un refus explicite couvre journaux et résultats, même en cas d'évolution."""
    role = _role_msp(charger_gabarit())
    refus = [
        declaration
        for declaration in _declarations(role)
        if declaration.get("Effect") == "Deny" and "s3:*" in _actions(declaration)
    ]
    assert refus, "aucun refus explicite sur les compartiments de journaux"
    cibles = " ".join(_cibles(refus[0]))
    assert "${LogsBucketName}/*" in cibles
    assert "-replay-results" in cibles


def test_r10_role_msp_ne_peut_pas_invoquer_de_modele_ni_lire_le_texte() -> None:
    """R10 : le rejeu tourne chez le client ; le MSP n'appelle aucun modèle."""
    role = _role_msp(charger_gabarit())
    allow = [d for d in _declarations(role) if d.get("Effect") == "Allow"]
    actions = " ".join(action for declaration in allow for action in _actions(declaration))
    assert "bedrock:" not in actions
    # Aucun droit S3 générique : pas de s3:* sur le rôle du MSP.
    for declaration in allow:
        assert "s3:*" not in _actions(declaration), "droit S3 générique interdit au MSP"
        assert not any(
            action.startswith(("iam:", "ec2:", "organizations:"))
            for action in _actions(declaration)
        )


def test_r10_approbation_du_role_exige_l_identifiant_externe() -> None:
    """R10 (complément) : le rôle inter-comptes exige un identifiant externe (ENF-SEC-01)."""
    role = _role_msp(charger_gabarit())
    approbation = role["AssumeRolePolicyDocument"]["Statement"]
    declaration = next(item for item in approbation if item["Action"] == "sts:AssumeRole")
    assert ":iam::${MspAccountId}:root" in str(declaration["Principal"]["AWS"])
    assert declaration["Condition"]["StringEquals"]["sts:ExternalId"] == {"!Ref": "ExternalId"}


def _chemins_cites(texte: str) -> set[str]:
    """Chemins de fichiers cités dans un document (hors URL et noms nus)."""
    jetons = re.findall(
        r"[A-Za-z0-9_./-]+\.(?:yaml|yml|sh|py|json|md|toml|mjs|html|css|js|svg|mmd)", texte
    )
    return {
        jeton for jeton in jetons if "/" in jeton and jeton.startswith(DOSSIERS_REPO + ("../",))
    }


def test_r13_les_fichiers_cites_dans_la_documentation_existent() -> None:
    """R13 : chaque fichier cité par le README et le guide d'installation existe."""
    for document in (README, DOC_INSTALLATION):
        texte = document.read_text(encoding="utf-8")
        for chemin in sorted(_chemins_cites(texte)):
            candidats = [
                (document.parent / chemin).resolve(),
                (RACINE / chemin).resolve(),
                (RACINE / chemin.lstrip("./")).resolve(),
            ]
            assert any(candidat.exists() for candidat in candidats), (
                f"{document.name} cite {chemin}, absent du dépôt"
            )


@pytest.mark.parametrize(("extrait", "attendu"), COMMANDES_DOCUMENTEES)
def test_r13_chaque_commande_documentee_passe_par_un_fichier_existant(
    extrait: str, attendu: str
) -> None:
    """R13 : chaque commande `aws`/`cdk`/`uv` documentée correspond à un fichier réel."""
    for document in (README, DOC_INSTALLATION):
        texte = document.read_text(encoding="utf-8")
        if extrait not in texte:
            continue
        assert (RACINE / attendu).exists(), f"{attendu} cité par {document.name} est absent"
        return
    pytest.fail(f"la commande « {extrait} » n'est citée ni par le README ni par le guide")


def test_r13_le_script_de_deploiement_est_executable_et_coherent() -> None:
    """R13 : `deploy.sh` existe, est exécutable et passe les bons paramètres."""
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK), "deploy.sh doit être exécutable"
    script = SCRIPT.read_text(encoding="utf-8")
    gabarit = charger_gabarit()
    parametres = set(gabarit["Parameters"])

    # Chaque paramètre du gabarit est alimenté par le script.
    for nom in ("MspAccountId", "ExternalId", "EnvironmentName", "DataRegion", "LogsBucketName"):
        assert nom in parametres, f"paramètre {nom} absent du gabarit"
        assert f"{nom}=" in script, f"le script ne passe pas {nom}"
    assert "template.yaml" in script
    assert "aws cloudformation deploy" in script


def test_r13_le_guide_dinstallation_decrit_chaque_parametre_du_gabarit() -> None:
    """R13 : le guide d'installation décrit tous les paramètres du gabarit."""
    guide = DOC_INSTALLATION.read_text(encoding="utf-8")
    parametres = set(charger_gabarit()["Parameters"])
    assert len(parametres) >= 9
    for nom in sorted(parametres):
        assert nom in guide, f"le paramètre {nom} n'est pas décrit dans deploy-client.md"


def test_r13_le_readme_pointe_vers_le_gabarit_et_le_plan_de_controle() -> None:
    """R13 : le README renvoie vers le gabarit client et le plan de contrôle (ENF-REP-01)."""
    readme = README.read_text(encoding="utf-8")
    assert "infra/client-account/template.yaml" in readme
    assert "infra/control-plane" in readme
    assert (RACINE / "infra" / "control-plane" / "app.py").is_file()
