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

__all__ = [
    "ETATS_PRINCIPAUX",
    "BedrockModeleClient",
    "DemandeRejeu",
    "Echantillon",
    "EstimationCampagne",
    "ExecuteurRejeu",
    "FakeModeleClient",
    "HypothesesCout",
    "MesuresCandidat",
    "ModeleClient",
    "ParametresMachine",
    "RequeteSource",
    "ResultatInvocation",
    "RejeuComplet",
    "ResumeRejeu",
    "TextesEnMemoire",
    "classe_longueur",
    "definition",
    "estimer_cout_campagne",
    "estimer_et_verifier",
    "export_json",
    "tirer_echantillon",
    "verifier_plafond",
]
