"""Agent lecteur de résultats (§12.1, §13.3) : notes et familles en échec.

Il lit les résultats consolidés d'une campagne (`get_replay_results`) et repère
les familles de requêtes où un candidat échoue (`get_failure_clusters`). Il
interprète, il ne recalcule pas : le taux d'équivalence et son intervalle sont
calculés par le code (EF-EVA-03), et la règle de décision du §13.4 est appliquée
sur les valeurs fournies.
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils
from jauge.agents.schemas import LectureResultats
from jauge.agents.sdk import creer_agent_specialise
from jauge.agents.tools import OUTILS_LECTEUR, outils_pour

NOM = "lecteur_resultats"
DESCRIPTION = (
    "Lecteur de résultats Jauge : lit les notes d'une campagne de rejeu, compare les "
    "candidats au modèle actuel et repère les familles de requêtes en échec."
)

PROMPT = """Tu es le lecteur de résultats de Jauge, un service FinOps pour Amazon Bedrock.

Ton périmètre :
- lire les résultats consolidés d'une campagne (`get_replay_results`) ;
- repérer les familles de requêtes où un candidat échoue (`get_failure_clusters`).

Règles absolues :
1. Tu ne calcules aucun taux, aucun intervalle, aucune moyenne : tout te vient
   des outils, et tu les recopies tels quels.
2. Tu compares toujours le candidat au **modèle actuel comparé à lui-même** :
   un candidat à 95 % face à un modèle actuel auto-équivalent à 96 % est
   excellent ; un candidat à 97 % face à un modèle actuel à 99,5 % l'est moins.
3. La règle de décision du §13.4 se lit dans les valeurs fournies : taux au
   seuil, borne basse de l'intervalle au-dessus du seuil bas, aucun échec
   déterministe critique. Tu cites le champ `conforme_seuils` plutôt que de
   refaire le calcul.
4. Tu ne demandes ni ne cites jamais le texte d'une requête ou d'une réponse :
   les familles en échec sont des étiquettes, pas des extraits.
5. Tu n'approuves rien et tu ne bascules rien.
6. Tu réponds en français, en nommant les identifiants de campagne, de modèle et
   de famille.
"""


def creer_lecteur_resultats(
    modele: Any, ctx: ContexteOutils, sortie_structuree: type[Any] | None = LectureResultats
) -> Any:
    """Crée l'agent lecteur de résultats, outillé selon le §12.1."""
    return creer_agent_specialise(
        nom=NOM,
        description=DESCRIPTION,
        prompt=PROMPT,
        modele=modele,
        outils=outils_pour(ctx, OUTILS_LECTEUR),
        sortie_structuree=sortie_structuree,
    )
