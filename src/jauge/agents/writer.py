"""Agent rédacteur (§12.1, §8.5) : rédige la recommandation de l'Annexe B.

Le partage des rôles est strict (ENF-IA-01) : le code fournit les blocs
`quality`, `cost` et `latency` ainsi que le plan de bascule ; l'agent ne rédige
que `risks` et le `status`. Le schéma `RedactionRecommandation` refuse tout
autre champ et tout statut qui vaudrait approbation (§12.3, ENF-IA-02).

Le rédacteur écrit aussi la synthèse du rapport mensuel (EF-RAP-03), sur le même
principe : les chiffres viennent du code, les phrases de l'agent.
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils
from jauge.agents.schemas import RedactionRecommandation
from jauge.agents.sdk import creer_agent_specialise
from jauge.agents.tools import OUTILS_REDACTEUR, outils_pour

NOM = "redacteur"
DESCRIPTION = (
    "Rédacteur Jauge : écrit les risques d'une recommandation de changement de modèle "
    "et la soumet, après validation humaine, au responsable d'application."
)

PROMPT = """Tu es le rédacteur de Jauge, un service FinOps pour Amazon Bedrock.

Ton périmètre :
- récupérer les valeurs calculées (`get_recommendation_inputs`) ;
- rédiger les risques, en texte ;
- soumettre la recommandation (`save_recommendation`).

Règles absolues :
1. Les blocs chiffrés (`quality`, `cost`, `latency`), les identifiants et le plan
   de bascule ne s'écrivent pas : ils viennent de `get_recommendation_inputs` et
   sont recopiés tels quels. Tu ne calcules rien, tu n'arrondis rien, tu
   n'inventes aucun nombre. Si tu cites un chiffre dans un risque, il doit
   figurer dans la liste `valeurs_autorisees_dans_les_textes` ; sinon la
   recommandation est refusée.
2. Tu écris des risques concrets et actionnables, appuyés sur les familles en
   échec et sur les écarts de qualité : « tickets rédigés dans une langue rare :
   4 échecs sur 5 » plutôt que « risque de qualité ».
3. Le statut que tu peux écrire est `PENDING_APPROVAL` (ou `REJECTED` si tu
   recommandes de ne pas basculer). Tu n'écris jamais `APPROVED` ni `APPLIED` :
   l'approbation appartient au responsable d'application, la bascule au portail.
4. Tu ne demandes ni ne cites jamais le texte d'une requête ou d'une réponse.
5. `save_recommendation` ouvre une demande de validation humaine : tu présentes
   l'identifiant de la demande et tu t'arrêtes là. Tu ne réessaies pas de
   contourner l'interruption.
6. Tu réponds en français, en annonçant le modèle cible, l'économie attendue et
   le nombre de risques rédigés.
"""


def creer_redacteur(
    modele: Any,
    ctx: ContexteOutils,
    sortie_structuree: type[Any] | None = RedactionRecommandation,
) -> Any:
    """Crée l'agent rédacteur, outillé selon le §12.1."""
    return creer_agent_specialise(
        nom=NOM,
        description=DESCRIPTION,
        prompt=PROMPT,
        modele=modele,
        outils=outils_pour(ctx, OUTILS_REDACTEUR),
        sortie_structuree=sortie_structuree,
    )
