"""Module de rejeu : échantillon reproductible, exécution, coût, machine à états.

Exigences couvertes par ce paquet :
- EF-REJ-01 : échantillon stratifié et reproductible (§13.1) — `sampling` ;
- EF-REJ-02 : rejeu à paramètres identiques sur référence et candidats — `client`,
  `executor` ;
- EF-REJ-03 : exécution dans le compte client au niveau Flex — `executor`,
  `statemachine` ;
- EF-REJ-04 : jetons, coût et latence mesurés par le code — `executor` ;
- EF-EVA-04 : variabilité naturelle (double exécution du modèle actuel) —
  `executor` ;
- ENF-COUT-01 : estimation et plafond avant lancement — `cost` ;
- ENF-CONF-01 : le texte reste dans le plan de données — `executor`
  (`PaireRejoueeDataPlane`) contre `ResumeRejeu` (sans texte).

Aucun chiffre n'est produit par un modèle de langage (ENF-IA-01).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from jauge.replay.backtest import (
    HypothesesBacktest,
    ResultatBacktest,
    VentilationBacktest,
    VolumeBacktest,
    backtester,
    volumes_depuis_agregats,
)
from jauge.replay.client import (
    BedrockModeleClient,
    DemandeRejeu,
    FakeModeleClient,
    ModeleClient,
    ResultatInvocation,
)
from jauge.replay.cost import (
    EstimationCampagne,
    HypothesesCout,
    estimer_cout_campagne,
    estimer_et_verifier,
    verifier_plafond,
)
from jauge.replay.executor import (
    ExecuteurRejeu,
    MesuresCandidat,
    RejeuComplet,
    ResumeRejeu,
    TextesEnMemoire,
)
from jauge.replay.sampling import (
    Echantillon,
    RequeteSource,
    classe_longueur,
    tirer_echantillon,
)
from jauge.replay.statemachine import (
    ETATS_PRINCIPAUX,
    ParametresMachine,
    definition,
    export_json,
)
from jauge.replay.variantes import (
    VariantePrompt,
    appliquer_transformation,
    contraintes_presentes,
    diff_texte,
    est_contrainte,
    generer_variantes,
    normaliser,
    verifier_integrite,
)

__all__ = [
    "ETATS_PRINCIPAUX",
    "BedrockModeleClient",
    "ClientVariantePrompt",
    "ContextePreuve",
    "DemandeRejeu",
    "Echantillon",
    "EntreePreuve",
    "EstimationCampagne",
    "ExecuteurRejeu",
    "FakeModeleClient",
    "HypothesesBacktest",
    "HypothesesCout",
    "MesuresCandidat",
    "ModeleClient",
    "ParametresMachine",
    "PreuveVariante",
    "RejeuComplet",
    "RequeteSource",
    "ResultatBacktest",
    "ResultatInvocation",
    "ResumeRejeu",
    "TextesEnMemoire",
    "VariantePrompt",
    "VentilationBacktest",
    "VolumeBacktest",
    "appliquer_transformation",
    "backtester",
    "classe_longueur",
    "contraintes_presentes",
    "definition",
    "diff_texte",
    "est_contrainte",
    "estimer_cout_campagne",
    "estimer_et_verifier",
    "export_json",
    "generer_variantes",
    "normaliser",
    "prouver_variante",
    "tirer_echantillon",
    "verifier_integrite",
    "verifier_plafond",
    "volumes_depuis_agregats",
]


#: Modules satellites exportés **différément** : `variantes_preuve` dépend de
#: `jauge.integration`, qui importe `jauge.api.app` — un import ici créerait un
#: cycle (API → routes → replay → integration → API). Même motif que
#: `jauge.reports.proof_pack`.
_EXPORTS_DIFFERES = {
    "ClientVariantePrompt": "variantes_preuve",
    "ContextePreuve": "variantes_preuve",
    "EntreePreuve": "variantes_preuve",
    "PreuveVariante": "variantes_preuve",
    "prouver_variante": "variantes_preuve",
}


def __getattr__(nom: str) -> Any:
    """Exporte à la demande les symboles de `variantes_preuve` (sans cycle)."""
    module = _EXPORTS_DIFFERES.get(nom)
    if module is None:
        raise AttributeError(f"le module {__name__!r} n'a pas d'attribut {nom!r}")
    return getattr(import_module(f"jauge.replay.{module}"), nom)
