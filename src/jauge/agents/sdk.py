"""Accès au SDK Strands, avec repli si la dépendance n'est pas installée.

`strands-agents` est déclaré dans le groupe `agents` de `pyproject.toml`. Ce
module encapsule l'import : les tests de logique (choix d'outil, validation de
schéma, garde-fous) tournent même sans SDK, et la création d'agents passe
toujours par ici (factory `jauge.agents.orchestrator.creer_agents`).

Ce qui est réellement utilisé du SDK (vérifié sur strands-agents 1.64.0) :

- `strands.Agent` — boucle d'agent, `structured_output_model`, `session_manager` ;
- `strands.tool` — décorateur qui transforme une fonction typée en outil ;
- `Agent.as_tool(...)` — motif « agents comme outils » du §12.1 ;
- `strands.session.FileSessionManager` — sessions persistantes par campagne (§12.4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def _version(paquet: Any) -> str | None:
    """Version du SDK Strands installée, si elle est exposée."""
    directe = getattr(paquet, "__version__", None)
    if directe:
        return str(directe)
    try:  # pragma: no cover - dépend de la distribution installée
        from importlib.metadata import version

        return version("strands-agents")
    except Exception:  # pragma: no cover
        return None


try:  # pragma: no cover - dépend de l'environnement d'installation
    import strands
    from strands import Agent, tool
    from strands.models.model import Model
    from strands.session import FileSessionManager

    STRANDS_DISPONIBLE = True
    VERSION_STRANDS: str | None = _version(strands)
except ImportError:  # pragma: no cover - chemin de repli
    Agent = None  # type: ignore[assignment]
    FileSessionManager = None  # type: ignore[assignment]
    Model = object  # type: ignore[assignment,misc]
    STRANDS_DISPONIBLE = False
    VERSION_STRANDS = None


@dataclass
class OutilSimule:
    """Repli minimal d'un outil Strands quand le SDK est absent.

    Expose la même surface utile aux tests : nom, description, schéma
    d'entrée et fonction d'origine appelable.
    """

    original_function: Callable[..., Any]
    tool_name: str
    tool_spec: dict[str, Any] = field(default_factory=dict)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.original_function(*args, **kwargs)


def _outil_simule(fonction: Callable[..., Any], nom: str, description: str) -> OutilSimule:
    """Construit le repli d'outil (SDK absent)."""
    return OutilSimule(
        original_function=fonction,
        tool_name=nom,
        tool_spec={"name": nom, "description": description},
    )


def creer_outil(fonction: Callable[..., Any], nom: str, description: str) -> Any:
    """Décorer une fonction en outil Strands, ou en repli si le SDK manque."""
    if STRANDS_DISPONIBLE:
        return tool(name=nom, description=description)(fonction)
    return _outil_simule(fonction, nom, description)


def nom_outil(outil: Any) -> str:
    """Nom exposé d'un outil, quel que soit le chemin (SDK ou repli)."""
    return getattr(outil, "tool_name", None) or getattr(outil, "name", "")


def description_outil(outil: Any) -> str:
    """Description exposée d'un outil."""
    spec = getattr(outil, "tool_spec", {}) or {}
    return spec.get("description", "") if isinstance(spec, dict) else ""


def creer_agent(**parametres: Any) -> Any:
    """Crée un agent Strands, en refusant explicitement si le SDK est absent."""
    if not STRANDS_DISPONIBLE:
        raise RuntimeError(
            "strands-agents n'est pas installé : `uv sync --group agents` "
            "(la logique des agents reste testable via jauge.agents.sdk.OutilSimule)"
        )
    return Agent(**parametres)


def creer_agent_specialise(
    nom: str,
    description: str,
    prompt: str,
    modele: Any,
    outils: list[Any],
    sortie_structuree: type[Any] | None = None,
) -> Any:
    """Crée un agent spécialisé prêt à être employé comme outil (§12.1).

    `description` est ce que le chef d'orchestre voit de l'agent : elle dit
    quand le solliciter, pas comment il travaille.
    """
    parametres: dict[str, Any] = {
        "name": nom,
        "description": description,
        "model": modele,
        "system_prompt": prompt,
        "tools": list(outils),
    }
    if sortie_structuree is not None:
        parametres["structured_output_model"] = sortie_structuree
    return creer_agent(**parametres)


def creer_gestionnaire_session(campagne_id: str, repertoire: str | None) -> Any:
    """Gestionnaire de session Strands par campagne, ou `None` si indisponible."""
    if not STRANDS_DISPONIBLE or FileSessionManager is None:
        return None
    return FileSessionManager(session_id=campagne_id, storage_dir=repertoire)
