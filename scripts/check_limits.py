#!/usr/bin/env python3
"""Garde-fou de taille : fichiers < 500 lignes, fonctions < 50 lignes.

Règle user 2026-10-24. S'applique aux fichiers Python suivis par le dépôt
(source, tests, scripts, infra). Sort en erreur avec la liste des dépassements.

Usage : python3 scripts/check_limits.py [--max-file 500] [--max-func 50]
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
EXCLUS = {
    ".git",
    ".venv",
    "node_modules",
    "cdk.out",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".github",  # fichiers portés de la fabrique : vérifiés dans leurs propres tests
}


@dataclass(frozen=True)
class Depassement:
    chemin: str
    ligne: int
    genre: str
    taille: int
    limite: int

    def __str__(self) -> str:
        return f"{self.chemin}:{self.ligne} {self.genre} de {self.taille} lignes (limite {self.limite})"


def fichiers_python(racine: Path) -> list[Path]:
    """Liste les fichiers .py à contrôler, hors répertoires générés."""
    sortie: list[Path] = []
    for chemin in sorted(racine.rglob("*.py")):
        if any(partie in EXCLUS for partie in chemin.parts):
            continue
        sortie.append(chemin)
    return sortie


def taille_fichier(chemin: Path) -> int:
    """Nombre de lignes du fichier (dernière ligne sans \\n comptée)."""
    with chemin.open("rb") as brut:
        return brut.read().count(b"\n") + (0 if brut.seek(0, 2) == 0 else 1)


def fonctions(taille_limite: int, chemin: Path, source: str) -> list[Depassement]:
    """Mesure chaque fonction/méthode (du `def` à sa dernière ligne)."""
    depassements: list[Depassement] = []
    try:
        arbre = ast.parse(source)
    except SyntaxError as erreur:  # un fichier non compilable est signalé ailleurs
        print(f"ERREUR de syntaxe : {chemin} : {erreur}", file=sys.stderr)
        return depassements
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        fin = noeud.end_lineno or noeud.lineno
        taille = fin - noeud.lineno + 1
        if taille > taille_limite:
            depassements.append(
                Depassement(
                    str(chemin), noeud.lineno, f"fonction `{noeud.name}`", taille, taille_limite
                )
            )
    return depassements


def controler(max_file: int, max_func: int) -> list[Depassement]:
    """Parcourt le dépôt et rend la liste des dépassements."""
    depassements: list[Depassement] = []
    for chemin in fichiers_python(RACINE):
        relatif = chemin.relative_to(RACINE)
        lignes = taille_fichier(chemin)
        if lignes > max_file:
            depassements.append(Depassement(str(relatif), lignes, "fichier", lignes, max_file))
        source = chemin.read_text(encoding="utf-8", errors="replace")
        for item in fonctions(max_func, relatif, source):
            depassements.append(item)
    return depassements


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--max-file", type=int, default=500)
    analyseur.add_argument("--max-func", type=int, default=50)
    options = analyseur.parse_args()

    depassements = controler(options.max_file, options.max_func)
    total = len(list(fichiers_python(RACINE)))
    if not depassements:
        print(f"OK — {total} fichier(s) Python contrôlé(s), aucun dépassement.")
        return 0
    print(f"KO — {len(depassements)} dépassement(s) sur {total} fichier(s) :", file=sys.stderr)
    for item in depassements:
        print(f"  {item}", file=sys.stderr)
    print("Découper : module, helper, sous-composant (règle user 2026-10-24).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
