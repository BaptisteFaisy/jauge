"""Jauge — démonstration hors ligne : trafic synthétique et amorçage du store.

Ce paquet fournit la matière de la démonstration (§18.1) :

- `demo.traffic` : générateur **déterministe** de journaux d'invocation
  Bedrock synthétiques (même graine ⇒ même trafic) ;
- `demo.cur` : lignes CUR 2.0 synthétiques, cohérentes avec ce trafic ;
- `demo.apps` : trois applications clientes de démonstration et un client de
  modèle hors ligne ;
- `demo.seed` : amorçage d'un `MemoryStore` prêt à être affiché.

Aucune donnée réelle n'est utilisée : tous les textes, volumes et montants
sont fabriqués par le code.
"""

from __future__ import annotations

from demo.profils import (
    DATE_FIN_DEFAUT,
    GRAINE_DEFAUT,
    JOURS_DEFAUT,
    PROFILS,
    ProfilApplication,
)

__all__ = [
    "DATE_FIN_DEFAUT",
    "GRAINE_DEFAUT",
    "JOURS_DEFAUT",
    "PROFILS",
    "ProfilApplication",
]
