"""Traces d'exécution des agents et des tâches de rejeu (ENF-OBS-01).

Chaque exécution d'agent et chaque lancement de rejeu laisse une trace :
identifiant, agent, opération, client, campagne, statut, durée, détails
techniques. Les alarmes se branchent sur `Traceur.echecs()`.

Aucune trace ne contient de texte de requête ni de réponse : uniquement des
identifiants et des agrégats (ENF-CONF-01).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from jauge.common.ids import aleatoire

STATUT_OK = "ok"
STATUT_ECHEC = "echec"


@dataclass(frozen=True)
class TraceExecution:
    """Une exécution tracée : agent, opération, statut, durée, détails."""

    id: str
    agent: str
    operation: str
    statut: str
    duree_ms: int
    tenant_id: str = ""
    campagne_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def vers_dict(self) -> dict[str, Any]:
        """Forme sérialisable (journaux structurés, audit)."""
        return {
            "trace_id": self.id,
            "agent": self.agent,
            "operation": self.operation,
            "statut": self.statut,
            "duree_ms": self.duree_ms,
            "tenant_id": self.tenant_id,
            "campagne_id": self.campagne_id,
            "details": dict(self.details),
        }


class Traceur:
    """Collecteur de traces en mémoire (remplaçable par CloudWatch en production)."""

    def __init__(self) -> None:
        self._traces: list[TraceExecution] = []

    def enregistrer(
        self,
        agent: str,
        operation: str,
        statut: str = STATUT_OK,
        duree_ms: int = 0,
        tenant_id: str = "",
        campagne_id: str | None = None,
        **details: Any,
    ) -> TraceExecution:
        """Ajoute une trace et la renvoie."""
        trace = TraceExecution(
            id=aleatoire("trace"),
            agent=agent,
            operation=operation,
            statut=statut,
            duree_ms=duree_ms,
            tenant_id=tenant_id,
            campagne_id=campagne_id,
            details=dict(details),
        )
        self._traces.append(trace)
        return trace

    @contextmanager
    def tracer(
        self,
        agent: str,
        operation: str,
        tenant_id: str = "",
        campagne_id: str | None = None,
        **details: Any,
    ) -> Iterator[dict[str, Any]]:
        """Trace une exécution, succès comme échec (l'exception est relayée).

        Le dictionnaire rendu permet à l'appelant d'ajouter des détails connus
        seulement en fin d'opération (identifiant de campagne, par exemple).
        """
        contexte: dict[str, Any] = dict(details)
        debut = time.perf_counter()
        try:
            yield contexte
        except Exception as erreur:
            self.enregistrer(
                agent=agent,
                operation=operation,
                statut=STATUT_ECHEC,
                duree_ms=int((time.perf_counter() - debut) * 1000),
                tenant_id=tenant_id,
                campagne_id=campagne_id,
                erreur=type(erreur).__name__,
                message=str(erreur)[:200],
                **contexte,
            )
            raise
        self.enregistrer(
            agent=agent,
            operation=operation,
            statut=STATUT_OK,
            duree_ms=int((time.perf_counter() - debut) * 1000),
            tenant_id=tenant_id,
            campagne_id=campagne_id,
            **contexte,
        )

    def traces(self) -> list[TraceExecution]:
        """Toutes les traces, dans l'ordre d'émission."""
        return list(self._traces)

    def echecs(self) -> list[TraceExecution]:
        """Traces en échec : c'est là que se branchent les alarmes ENF-OBS-01."""
        return [trace for trace in self._traces if trace.statut == STATUT_ECHEC]

    def pour_campagne(self, campagne_id: str) -> list[TraceExecution]:
        """Traces d'une campagne (session persistante, §12.4)."""
        return [trace for trace in self._traces if trace.campagne_id == campagne_id]
