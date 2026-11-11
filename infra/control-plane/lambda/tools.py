"""Outils des agents exposés par AgentCore Gateway (paquetage, aucun calcul).

La passerelle appelle cette fonction pour chaque outil déclaré (MCP). Le corps
de chaque outil — lecture d'agrégats, classement des leviers, résultats de
rejeu — appartient aux modules applicatifs (M7 agents, M8 API) : ici on se
contient de déléguer à la fonction d'exécution qui expose ces outils.

**Point d'intégration à confirmer** : le chemin d'import ci-dessous est une
hypothèse. Le code applicatif peut être exposé sous l'un de ces noms, selon le
module qui le livre :

- `jauge.api.tools:execute_tool` (API du plan de contrôle) ;
- `jauge.agents.tools:execute_tool` (agents Strands).

Si aucun n'est présent, l'outil répond `501` avec un message explicite — la
panne est visible dans les journaux et dans les alarmes, pas silencieuse.

Aucun chiffre n'est calculé par un modèle (ENF-IA-01) et aucun outil ne renvoie
le texte des requêtes (ENF-CONF-01) : le filtrage est fait par le code
applicatif, pas par cette couche.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

CANDIDATS = (
    ("jauge.api.tools", "execute_tool"),
    ("jauge.agents.tools", "execute_tool"),
)


def handler(event: dict, context: Any) -> dict:
    """Point d'entrée de l'outil : `{"name": ..., "arguments": {...}}`."""
    nom = _nom_outil(event)
    arguments = _arguments(event)
    if nom is None:
        return _resultat({"error": "invalid_request", "detail": "tool name is missing"}, True)
    try:
        executeur = _executeur()
    except Exception as erreur:
        return _resultat({"error": "not_implemented", "detail": str(erreur)}, erreur=True)
    try:
        return _resultat(executeur(nom, arguments))
    except Exception as erreur:  # l'agent reçoit l'erreur, la passerelle la trace
        return _resultat({"error": type(erreur).__name__, "detail": str(erreur)}, erreur=True)


def _executeur() -> Any:
    """Import paresseux de l'implémentation applicative des outils."""
    erreurs: list[str] = []
    for module, attribut in CANDIDATS:
        try:
            importlib.import_module(module)
        except ImportError as erreur:
            erreurs.append(f"{module}: {erreur}")
            continue
        fonction = getattr(importlib.import_module(module), attribut, None)
        if fonction is not None:
            return fonction
    raise ImportError(
        "aucun exécuteur d'outils disponible ; trucs attendus : "
        + ", ".join(f"{module}.{attribut}" for module, attribut in CANDIDATS)
        + f" ({'; '.join(erreurs)})"
    )


def _nom_outil(event: dict) -> str | None:
    """Nom de l'outil demandé (formats MCP et « tool » simples acceptés)."""
    for cle in ("name", "tool", "toolName"):
        valeur = event.get(cle)
        if isinstance(valeur, str) and valeur:
            return valeur
    return None


def _arguments(event: dict) -> dict:
    """Arguments de l'outil, déjà désérialisés par la passerelle."""
    arguments = event.get("arguments") or event.get("input") or {}
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
    return dict(arguments) if isinstance(arguments, dict) else {"raw": arguments}


def _resultat(charge: Any, erreur: bool = False) -> dict:
    """Résultat au format attendu par AgentCore Gateway (contenu MCP)."""
    return {
        "isError": erreur,
        "content": [{"type": "text", "text": json.dumps(charge, default=str)}],
    }
