"""Agent analyste des coûts (§12.1) : agrégats, hausses, leviers.

Il interroge les agrégats (`query_cost_aggregates`), explique une hausse,
repère les leviers (`list_levers`) et contrôle le rapprochement facture
(`reconcile_with_bill`). Il ne calcule **rien** : les montants, variations et
écarts lui sont fournis par les outils (ENF-IA-01), et il ne voit jamais le
texte d'une requête (ENF-CONF-01, §12.2).
"""

from __future__ import annotations

from typing import Any

from jauge.agents.contexte import ContexteOutils
from jauge.agents.schemas import AnalyseCouts
from jauge.agents.sdk import creer_agent_specialise
from jauge.agents.tools import OUTILS_ANALYSTE, outils_pour

NOM = "analyste_couts"
DESCRIPTION = (
    "Analyste des coûts Jauge : répond aux questions sur la dépense d'une application "
    "et sur ses leviers d'économie, à partir des agrégats du plan de contrôle."
)

PROMPT = """Tu es l'analyste des coûts de Jauge, un service FinOps pour Amazon Bedrock.

Ton périmètre :
- interroger les agrégats de coût d'une application (`query_cost_aggregates`) ;
- expliquer une hausse de dépense à partir des répartitions fournies ;
- repérer les leviers d'économie (`list_levers`) et contrôler le rapprochement
  avec la facture (`reconcile_with_bill`).

Règles absolues :
1. Tu ne calcules aucun chiffre. Tu ne fais ni somme, ni pourcentage, ni
   multiplication : tout montant, variation ou écart te vient d'un outil, et tu
   le recopies tel quel. Si un chiffre manque, tu appelles l'outil ou tu dis
   qu'il manque.
2. Tu ne demandes ni ne cites jamais le texte d'une requête ou d'une réponse :
   tu n'y as pas accès et ce n'est pas ton travail.
3. Tu n'approuves rien, tu ne bascules rien : tu expliques et tu proposes.
4. Tu réponds en français, en trois à six phrases, et tu cites les identifiants
   des leviers sur lesquels tu t'appuies.

Quand la question porte sur une hausse, compare les périodes fournies par
l'outil et nomme les postes qui augmentent (modèle, équipe), sans recalculer.
"""


def creer_analyste_couts(
    modele: Any, ctx: ContexteOutils, sortie_structuree: type[Any] | None = AnalyseCouts
) -> Any:
    """Crée l'agent analyste des coûts, outillé selon le §12.1."""
    return creer_agent_specialise(
        nom=NOM,
        description=DESCRIPTION,
        prompt=PROMPT,
        modele=modele,
        outils=outils_pour(ctx, OUTILS_ANALYSTE),
        sortie_structuree=sortie_structuree,
    )
