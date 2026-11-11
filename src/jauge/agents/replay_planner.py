"""Agent planificateur de rejeu (§12.1, §8.4) : candidats, échantillon, contrôles.

Il choisit **quoi tester** : les candidats (`list_candidate_models`), la taille
d'échantillon et les contrôles déterministes selon le profil de la tâche
(`get_task_profile`), puis vérifie le coût (`estimate_campaign_cost`) avant de
lancer le rejeu (`start_replay`). Le plafond du client et la liste des modèles
autorisés sont contrôlés par les outils eux-mêmes (§12.3, ENF-COUT-01).
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils
from jauge.agents.schemas import PlanRejeu
from jauge.agents.sdk import creer_agent_specialise
from jauge.agents.tools import OUTILS_PLANIFICATEUR, outils_pour

NOM = "planificateur_rejeu"
DESCRIPTION = (
    "Planificateur de rejeu Jauge : choisit les modèles candidats, la taille "
    "d'échantillon et les contrôles, estime le coût et lance la campagne de rejeu."
)

PROMPT = """Tu es le planificateur de rejeu de Jauge, un service FinOps pour Amazon Bedrock.

Ton périmètre :
- lire le profil de la tâche (`get_task_profile`) : type, contrôles, seuils, volumes ;
- lister les candidats autorisés (`list_candidate_models`) ;
- estimer le coût de la campagne (`estimate_campaign_cost`) ;
- lancer le rejeu (`start_replay`) une fois le plan arrêté.

Règles absolues :
1. Tu ne calcules aucun chiffre : le coût estimé vient de `estimate_campaign_cost`,
   la taille d'échantillon de référence est de 200 requêtes (§13.1). Si tu
   proposes une autre taille, tu expliques pourquoi en t'appuyant sur le profil.
2. Tu ne proposes que des candidats renvoyés par `list_candidate_models` : la
   liste d'autorisation du client est la seule source. Un candidat absent de
   cette liste ne doit jamais être testé.
3. Tu ne lances jamais un rejeu dont le coût estimé dépasse le plafond du
   client : si l'outil refuse, tu proposes un échantillon plus petit ou moins de
   candidats, et tu dis pourquoi.
4. Tu ne demandes ni ne cites le texte des requêtes : tu n'y as pas accès.
5. Tu n'approuves rien et tu ne bascules rien : la bascule est une action
   déterministe du portail, après approbation humaine.
6. Tu réponds en français, en citant les identifiants de campagne et de modèles.
"""


def creer_planificateur_rejeu(
    modele: Any, ctx: ContexteOutils, sortie_structuree: type[Any] | None = PlanRejeu
) -> Any:
    """Crée l'agent planificateur de rejeu, outillé selon le §12.1."""
    return creer_agent_specialise(
        nom=NOM,
        description=DESCRIPTION,
        prompt=PROMPT,
        modele=modele,
        outils=outils_pour(ctx, OUTILS_PLANIFICATEUR),
        sortie_structuree=sortie_structuree,
    )
