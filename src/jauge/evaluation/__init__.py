"""Évaluation par modèle juge et règle de décision (M5).

Chaîne complète d'un candidat, du rejeu à la proposition :

1. `judge` — le **modèle juge** note l'équivalence fonctionnelle de chaque paire
   (§13.3). Le juge est distinct des candidats et au moins aussi capable que le
   modèle actuel (§13.5). En production, la notation passe par une tâche
   d'évaluation Bedrock alimentée par un jeu de données à réponses fournies
   (§3.2) ; `FakeJugeClient` sert aux tests et à la démonstration hors AWS.
2. `checks` — les **contrôles déterministes** de la tâche (EF-EVA-02) : JSON
   valide, schéma respecté, étiquette identique, longueur bornée.
3. `scoring` — le **taux d'équivalence** et son intervalle de Wilson (EF-EVA-03),
   l'**auto-accord** du modèle actuel (EF-EVA-04), les familles en échec et
   l'accord juge/humain (EF-EVA-05) ; produit un `ResultatCandidat` du socle.
4. `decision` — la **règle de décision** du §13.4 : trois conditions, rejet
   motivé, et lecture du taux à côté de l'auto-accord du modèle actuel.
5. `human_review` — le **sous-échantillon de 30 paires** relu par un humain et
   l'accord juge/humain (§13.5), sans déplacer aucun texte.

ENF-CONF-01 : rien de ce paquet ne fait sortir un texte de requête ou de réponse
du compte client ; les objets rendus au plan de contrôle ne portent que des
identifiants, des verdicts et des nombres calculés ici (ENF-IA-01).
"""

from __future__ import annotations

from jauge.evaluation.checks import (
    CONTROLES_PAR_TACHE,
    ContexteControle,
    ResultatControle,
    controles_pour,
    echecs_critiques,
    etiquette_de,
    executer_controles,
    resume_controles,
)
from jauge.evaluation.decision import (
    Condition,
    DecisionCandidat,
    Qualification,
    SeuilsDecision,
    candidats_proposes,
    decider,
    decider_lot,
    lecture_relative,
    qualifier,
)
from jauge.evaluation.human_review import (
    TAILLE_ECHANTILLON,
    RegistreRelecture,
    SousEchantillon,
    VerdictHumain,
    preparer_relecture,
    tirer_sous_echantillon,
)
from jauge.evaluation.judge import (
    NOTE_EQUIVALENTE,
    BedrockJugeClient,
    DemandeNotation,
    FakeJugeClient,
    JugeClient,
    RegistreCapacite,
    VerdictJuge,
    choisir_juge,
    construire_prompt,
    lignes_jeu_donnees,
    valider_juge,
    verdict_depuis_enregistrement,
    verdict_depuis_note,
)
from jauge.evaluation.scoring import (
    MesuresCoutLatence,
    PaireEvaluee,
    accord_juge_humain,
    auto_accord,
    consolider_resultat,
    familles_en_echec,
    nombre_echecs_deterministes,
    taux_equivalence,
    taux_par_famille,
)

__all__ = [
    "CONTROLES_PAR_TACHE",
    "NOTE_EQUIVALENTE",
    "TAILLE_ECHANTILLON",
    "BedrockJugeClient",
    "Condition",
    "ContexteControle",
    "DecisionCandidat",
    "DemandeNotation",
    "FakeJugeClient",
    "JugeClient",
    "MesuresCoutLatence",
    "PaireEvaluee",
    "Qualification",
    "RegistreCapacite",
    "RegistreRelecture",
    "ResultatControle",
    "SeuilsDecision",
    "SousEchantillon",
    "VerdictHumain",
    "VerdictJuge",
    "accord_juge_humain",
    "auto_accord",
    "candidats_proposes",
    "choisir_juge",
    "consolider_resultat",
    "construire_prompt",
    "controles_pour",
    "decider",
    "decider_lot",
    "echecs_critiques",
    "etiquette_de",
    "executer_controles",
    "familles_en_echec",
    "lecture_relative",
    "lignes_jeu_donnees",
    "nombre_echecs_deterministes",
    "preparer_relecture",
    "qualifier",
    "resume_controles",
    "taux_equivalence",
    "taux_par_famille",
    "tirer_sous_echantillon",
    "valider_juge",
    "verdict_depuis_enregistrement",
    "verdict_depuis_note",
]
