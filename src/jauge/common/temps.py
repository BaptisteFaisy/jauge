"""Horloge métier du domaine : une seule source pour « aujourd'hui ».

Le portail statique publie un instantané **reproductible** : la fenêtre de
démonstration y est figée (`DATE_FIN_INSTANTANE`), mais les points d'API qui
consultent la période courante retombaient chacun sur `date.today()` —
l'instantané dérivait donc d'un jour sur l'autre et le test de fraîcheur
rougissait à minuit. Ce module donne au domaine une horloge unique :

- par défaut, `aujourdhui()` est la date réelle (comportement de production
  inchangé) ;
- l'export statique (et tout harnais de rejeu) **fige** la date le temps d'un
  calcul : deux exécutions donnent le même instantané, où qu'elles tournent.

Le plan de contrôle en production n'appelle jamais `figer_date` : il voit la
date réelle. L'horloge est un état de process, pas une donnée client.
"""

from __future__ import annotations

from datetime import date

#: Date figée (`None` = date réelle). Cellule unique : l'état est de process.
_DATE_FIGEE: list[date | None] = [None]


def aujourdhui() -> date:
    """Date métier courante : la date figée si elle l'est, sinon la date réelle."""
    figee = _DATE_FIGEE[0]
    return figee if figee is not None else date.today()


def figer_date(moment: date | None) -> None:
    """Fige la date métier (`None` = rétablit la date réelle).

    À utiliser en paire avec le rétablissement, en `try/finally`, pour ne pas
    laisser une date figée fuiter au reste du process.
    """
    _DATE_FIGEE[0] = moment
