"""Portes déterministes à vérité terrain (piste n° 3, EF-EVA-02, §13.3, §21).

Là où une réponse objective existe, on ne demande pas au juge : on exécute, on
valide, on compare. Chaque porte rend son verdict **au format des contrôles
déterministes existants** (`jauge.evaluation.checks.ResultatControle`) : elle
alimente donc la condition 3 de la règle de décision (§13.4) sans la modifier, et
un échec compte comme un échec déterministe.

Trois portes :

``code``
    le code produit par le modèle est exécuté avec les tests de la tâche dans le
    bac à sable (`jauge.evaluation.bac_a_sable.executer_tests`) ;
``schema``
    la réponse est validée contre les clés attendues et les types de la
    référence — la porte **délègue** à `checks.controle_schema_respecte`, aucune
    logique n'est dupliquée ;
``numerique``
    la valeur lue dans la réponse est comparée à la référence calculée, avec une
    tolérance relative explicite.

Aucune porte n'invente de vérité. Sans attendu configuré, ou quand le bac à sable
n'a pas pu trancher (désactivé, isolation refusée, source trop volumineuse), la
porte est **non applicable** : `ok=True, critique=False`, avec un motif qui le
dit. Elle ne bloque rien et ne prétend rien prouver — le juge reprend la main,
exactement comme avant.

Les motifs ne contiennent **jamais** le texte de la réponse (ENF-CONF-01) : seuls
des noms de tests issus de la configuration de la tâche, des noms de clés et des
nombres y figurent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from jauge.evaluation.bac_a_sable import ParametresBacASable, executer_tests
from jauge.evaluation.checks import (
    ContexteControle,
    ResultatControle,
    controle_schema_respecte,
)

#: Tolérance relative par défaut de la porte numérique (0,1 % — arrondi d'affichage).
TOLERANCE_RELATIVE_DEFAUT = 0.001

#: Noms de champs acceptés quand la réponse est un objet JSON (ordre de priorité).
CHAMPS_NUMERIQUES = (
    "valeur",
    "value",
    "resultat",
    "result",
    "reponse",
    "answer",
    "montant",
    "total",
)

#: Un nombre décimal, avec exposant éventuel (la virgule décimale est acceptée).
_MOTIF_NOMBRE = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


class TypePorte(StrEnum):
    """Les trois familles de vérité terrain vérifiables sans juge."""

    CODE = "code"
    SCHEMA = "schema"
    NUMERIQUE = "numerique"


@dataclass(frozen=True)
class AttenduVerite:
    """La vérité terrain d'une tâche : ce qui permet de trancher sans juge.

    Elle vient de la **configuration de la tâche** (jamais du modèle) : tests à
    exécuter, clés attendues, valeur de référence et tolérance.
    """

    porte: TypePorte
    tests: str = ""
    cles_attendues: Sequence[str] = field(default_factory=tuple)
    valeur: float | None = None
    tolerance_relative: float = TOLERANCE_RELATIVE_DEFAUT


def non_applicable(motif: str) -> ResultatControle:
    """Verdict d'une porte qui ne peut pas trancher : elle ne bloque rien."""
    return ResultatControle(nom="verite_terrain", ok=True, critique=False, motif=motif)


def extraire_nombre(reponse: str) -> float | None:
    """Lit la valeur numérique d'une réponse, de façon déterministe.

    Règle explicite (aucun modèle, ENF-IA-01) : si la réponse est un objet JSON,
    le premier champ numérique parmi `CHAMPS_NUMERIQUES` est retenu, sinon le
    premier champ numérique quel que soit son nom ; à défaut d'objet JSON, le
    **dernier** nombre du texte est retenu — une réponse rédigée donne son
    résultat à la fin. `None` si aucun nombre n'est lisible.
    """
    texte = reponse.strip()
    if not texte:
        return None
    nombre = _nombre_dans_json(texte)
    if nombre is not None:
        return nombre
    trouves = _MOTIF_NOMBRE.findall(texte)
    return _convertir(trouves[-1]) if trouves else None


def _nombre_dans_json(texte: str) -> float | None:
    """Valeur numérique d'un objet JSON, par nom de champ connu puis au premier."""
    try:
        document = json.loads(texte)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return (
            float(document)
            if isinstance(document, int | float) and not isinstance(document, bool)
            else None
        )
    for cle in CHAMPS_NUMERIQUES:
        if isinstance(document.get(cle), int | float) and not isinstance(document[cle], bool):
            return float(document[cle])
    for valeur in document.values():
        if isinstance(valeur, int | float) and not isinstance(valeur, bool):
            return float(valeur)
    return None


def _convertir(brut: str) -> float | None:
    """Convertit un nombre écrit à la française ou à l'anglaise."""
    try:
        return float(brut.replace(",", "."))
    except ValueError:  # pragma: no cover - le motif garanti un nombre lisible
        return None


def porte_code(
    code: str, attendu: AttenduVerite, parametres: ParametresBacASable | None = None
) -> ResultatControle:
    """Exécute le code produit avec les tests de la tâche, dans le bac à sable."""
    resultat = executer_tests(code, attendu.tests, parametres)
    if not resultat.execute:
        return non_applicable(f"porte code non exécutable : {resultat.motif}")
    if resultat.ok:
        return ResultatControle(
            nom="verite_terrain_code",
            ok=True,
            motif=(
                f"{resultat.tests_total} test(s) exécuté(s) dans le bac à sable "
                f"(isolation {resultat.isolation})"
            ),
        )
    return ResultatControle(
        nom="verite_terrain_code",
        ok=False,
        critique=True,
        motif=(
            f"tests en échec dans le bac à sable : {', '.join(resultat.tests_echoues) or 'voir le harnais'}"
            f" ({resultat.tests_total - len(resultat.tests_echoues)}/{resultat.tests_total} passent)"
        ),
    )


def porte_schema(
    reponse_reference: str, reponse_candidate: str, attendu: AttenduVerite
) -> ResultatControle:
    """Valide la réponse contre les clés attendues et les types de la référence.

    Délégation à `checks.controle_schema_respecte` : une seule implémentation de
    la validation de schéma dans le dépôt.
    """
    if not attendu.cles_attendues:
        return non_applicable("aucune clé attendue configurée : la porte schéma ne tranche pas")
    contexte = ContexteControle(cles_attendues=tuple(attendu.cles_attendues))
    return controle_schema_respecte(reponse_reference, reponse_candidate, contexte)


def porte_numerique(reponse: str, attendu: AttenduVerite) -> ResultatControle:
    """Compare la valeur de la réponse à la référence calculée, à tolérance donnée."""
    if attendu.valeur is None:
        return non_applicable("aucune valeur de référence configurée : la porte ne tranche pas")
    lu = extraire_nombre(reponse)
    if lu is None:
        return ResultatControle(
            nom="verite_terrain_numerique",
            ok=False,
            critique=True,
            motif="aucune valeur numérique lisible dans la réponse",
        )
    ecart = abs(lu - attendu.valeur)
    seuil = max(abs(attendu.valeur) * attendu.tolerance_relative, 1e-9)
    if ecart <= seuil:
        return ResultatControle(
            nom="verite_terrain_numerique",
            ok=True,
            motif=f"valeur conforme à la référence (tolérance {attendu.tolerance_relative:.4%})",
        )
    return ResultatControle(
        nom="verite_terrain_numerique",
        ok=False,
        critique=True,
        motif=f"valeur écartée de la référence : écart relatif {ecart / max(abs(attendu.valeur), 1e-9):.4%}",
    )


def evaluer_porte(
    attendu: AttenduVerite | None,
    reponse_reference: str,
    reponse_candidate: str,
    parametres: ParametresBacASable | None = None,
) -> ResultatControle:
    """Tranche une réponse par la vérité terrain, sans consulter le juge.

    `attendu` vaut `None` quand la tâche n'a pas de vérité vérifiable : la porte
    est alors non applicable et le juge reprend la main. Le code produit est
    traité comme une donnée hostile (ENF-IA-03) : il n'est exécuté que dans le
    bac à sable.
    """
    if attendu is None:
        return non_applicable("aucune vérité terrain configurée : le juge tranche")
    if attendu.porte is TypePorte.CODE:
        return porte_code(reponse_candidate, attendu, parametres)
    if attendu.porte is TypePorte.SCHEMA:
        return porte_schema(reponse_reference, reponse_candidate, attendu)
    return porte_numerique(reponse_candidate, attendu)


def echecs_de_verite(resultats: Sequence[ResultatControle]) -> list[ResultatControle]:
    """Portes à vérité terrain en échec : elles bloquent la décision (§13.4)."""
    return [
        resultat
        for resultat in resultats
        if not resultat.ok and resultat.critique and resultat.nom.startswith("verite_terrain")
    ]


def verdicts_non_applicables(resultats: Sequence[ResultatControle]) -> list[ResultatControle]:
    """Portes qui n'ont pas pu trancher — à publier telles quelles, sans les compter."""
    return [resultat for resultat in resultats if not resultat.critique and resultat.motif]


def portes_par_tache(taches: Mapping[str, AttenduVerite | None]) -> dict[str, str]:
    """Résumé publiable : quelle porte s'applique à quelle tâche (aucun chiffre)."""
    return {
        tache: (attendu.porte.value if attendu is not None else "juge")
        for tache, attendu in taches.items()
    }
