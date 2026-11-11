"""Contrôles déterministes par type de tâche (EF-EVA-02, §13.3).

Le juge note l'équivalence fonctionnelle ; il ne dit rien du **format**. Les
contrôles ci-dessous sont déterministes : même entrée, même verdict, aucun
modèle. Ils portent sur ce que le client attend d'une sortie exploitable :

- `json_valide` : la réponse est un document JSON lisible ;
- `schema_respecte` : le document porte les clés attendues avec le bon type ;
- `etiquette_identique` : l'étiquette produite est celle de la référence ;
- `longueur_bornee` : la longueur reste dans les bornes de l'application.

Un contrôle rend `ok` + `motif` (vide si ok). Chaque type de tâche déclare ses
contrôles dans `CONTROLES_PAR_TACHE` ; un type inconnu tombe sur
`CONTROLES_PAR_TACHE["general"]`, qui n'impose que la longueur.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from jauge.common.errors import ValidationError

# Bornes par défaut de la longueur d'une réponse (caractères).
LONGUEUR_MIN_DEFAUT = 1
LONGUEUR_MAX_DEFAUT = 4000

# Criticité : un échec critique bloque la proposition de candidat (§13.4).
CRITIQUE = "critique"
AVERTISSEMENT = "avertissement"


@dataclass(frozen=True)
class ResultatControle:
    """Verdict d'un contrôle déterministe. `motif` explique un échec."""

    nom: str
    ok: bool
    critique: bool = True
    motif: str = ""


@dataclass(frozen=True)
class ContexteControle:
    """Ce dont un contrôle a besoin pour trancher (aucune donnée de coût)."""

    type_tache: str = "general"
    cles_attendues: Sequence[str] = field(default_factory=tuple)
    longueur_min: int = LONGUEUR_MIN_DEFAUT
    longueur_max: int = LONGUEUR_MAX_DEFAUT


def _extraire_json(reponse: str) -> tuple[bool, object, str]:
    """Lit la réponse comme un document JSON. Rend (ok, document, motif)."""
    texte = reponse.strip()
    if not texte:
        return False, None, "réponse vide"
    try:
        return True, json.loads(texte), ""
    except json.JSONDecodeError as erreur:
        return False, None, f"JSON invalide : {erreur.msg} (position {erreur.pos})"


def controle_json_valide(
    reponse_reference: str, reponse_candidate: str, ctx: ContexteControle
) -> ResultatControle:
    """La réponse candidate est un document JSON lisible."""
    ok, _, motif = _extraire_json(reponse_candidate)
    return ResultatControle(nom="json_valide", ok=ok, motif=motif)


def controle_schema_respecte(
    reponse_reference: str, reponse_candidate: str, ctx: ContexteControle
) -> ResultatControle:
    """Le document porte les clés attendues, et leur type est celui de la référence."""
    if not ctx.cles_attendues:
        return ResultatControle(nom="schema_respecte", ok=True)
    ok, document, motif = _extraire_json(reponse_candidate)
    if not ok:
        return ResultatControle(nom="schema_respecte", ok=False, motif=motif)
    if not isinstance(document, dict):
        return ResultatControle(
            nom="schema_respecte", ok=False, motif="un objet JSON est attendu, pas un scalaire"
        )
    manquantes = [cle for cle in ctx.cles_attendues if cle not in document]
    if manquantes:
        return ResultatControle(
            nom="schema_respecte", ok=False, motif=f"clés manquantes : {', '.join(manquantes)}"
        )
    _, reference, _ = _extraire_json(reponse_reference)
    if isinstance(reference, dict):
        return _verifier_types(document, reference)
    return ResultatControle(nom="schema_respecte", ok=True)


def _verifier_types(
    document: Mapping[str, object], reference: Mapping[str, object]
) -> ResultatControle:
    """Compare le type Python des clés communes à la référence."""
    ecarts = [
        f"`{cle}` : {type(document[cle]).__name__} au lieu de {type(reference[cle]).__name__}"
        for cle in reference
        if cle in document and type(document[cle]) is not type(reference[cle])
    ]
    if ecarts:
        return ResultatControle(
            nom="schema_respecte", ok=False, motif=f"types inattendus : {'; '.join(ecarts)}"
        )
    return ResultatControle(nom="schema_respecte", ok=True)


def etiquette_de(reponse: str) -> str | None:
    """Étiquette d'une réponse : champ `etiquette`/`label`/`categorie` d'un JSON,
    sinon la première ligne non vide, en minuscules.

    Rend `None` si la réponse est vide : une étiquette absente n'est jamais
    « identique » à celle de la référence.
    """
    ok, document, _ = _extraire_json(reponse)
    if ok and isinstance(document, dict):
        for cle in ("etiquette", "label", "categorie", "category"):
            if cle in document:
                return str(document[cle]).strip().lower()
    for ligne in reponse.splitlines():
        if ligne.strip():
            return ligne.strip().lower()
    return None


def controle_etiquette_identique(
    reponse_reference: str, reponse_candidate: str, ctx: ContexteControle
) -> ResultatControle:
    """La réponse candidate porte la même étiquette que la référence."""
    attendue = etiquette_de(reponse_reference)
    obtenue = etiquette_de(reponse_candidate)
    if attendue is None:
        return ResultatControle(
            nom="etiquette_identique", ok=True, critique=False, motif="référence sans étiquette"
        )
    if obtenue is None:
        return ResultatControle(
            nom="etiquette_identique", ok=False, motif="aucune étiquette dans la réponse candidate"
        )
    if attendue != obtenue:
        return ResultatControle(
            nom="etiquette_identique",
            ok=False,
            motif=f"étiquette « {obtenue} » au lieu de « {attendue} »",
        )
    return ResultatControle(nom="etiquette_identique", ok=True)


def controle_longueur_bornee(
    reponse_reference: str, reponse_candidate: str, ctx: ContexteControle
) -> ResultatControle:
    """La longueur de la réponse reste dans les bornes de l'application."""
    taille = len(reponse_candidate)
    if taille < ctx.longueur_min:
        return ResultatControle(
            nom="longueur_bornee", ok=False, motif=f"réponse trop courte : {taille} caractères"
        )
    if taille > ctx.longueur_max:
        return ResultatControle(
            nom="longueur_bornee",
            ok=False,
            critique=False,
            motif=f"réponse trop longue : {taille} > {ctx.longueur_max} caractères",
        )
    return ResultatControle(nom="longueur_bornee", ok=True)


Controle = Callable[[str, str, ContexteControle], ResultatControle]

# Registre : type de tâche → contrôles appliqués dans cet ordre.
CONTROLES_PAR_TACHE: dict[str, tuple[Controle, ...]] = {
    "classification": (
        controle_json_valide,
        controle_etiquette_identique,
        controle_longueur_bornee,
    ),
    "extraction": (controle_json_valide, controle_schema_respecte, controle_longueur_bornee),
    "redaction": (controle_longueur_bornee,),
    "general": (controle_longueur_bornee,),
}


def controles_pour(type_tache: str) -> tuple[Controle, ...]:
    """Contrôles du type de tâche ; type inconnu → contrôles généraux."""
    return CONTROLES_PAR_TACHE.get(type_tache, CONTROLES_PAR_TACHE["general"])


def executer_controles(
    type_tache: str,
    reponse_reference: str,
    reponse_candidate: str,
    ctx: ContexteControle | None = None,
) -> list[ResultatControle]:
    """Applique les contrôles du type de tâche à une paire."""
    contexte = ctx or ContexteControle(type_tache=type_tache)
    if not type_tache.strip():
        raise ValidationError("type de tâche vide : aucun contrôle applicable")
    return [
        controle(reponse_reference, reponse_candidate, contexte)
        for controle in controles_pour(type_tache)
    ]


def echecs_critiques(resultats: Sequence[ResultatControle]) -> list[ResultatControle]:
    """Contrôles en échec qui bloquent la décision (§13.4, condition 3)."""
    return [resultat for resultat in resultats if not resultat.ok and resultat.critique]


def resume_controles(resultats: Sequence[ResultatControle]) -> str:
    """Motifs d'échec, joints pour le rapport (aucun texte de réponse)."""
    motifs = [f"{resultat.nom} : {resultat.motif}" for resultat in resultats if not resultat.ok]
    return " ; ".join(motifs)
