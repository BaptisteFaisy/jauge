"""Construction du plan de données à partir des journaux d'invocation bruts.

Le plan de données se remplit depuis les **lignes JSON** des journaux
d'invocation Bedrock, que le module d'attribution (M1) lit déjà
(`jauge.attribution.logs.analyser_enregistrement`). Les deux lectures partagent
la même source : les agrégats du plan de contrôle et le profil rejouable ne
peuvent pas diverger.

Trois hypothèses sont explicites ici (elles n'engagent que la démonstration) :

1. un appel est **non interactif** s'il est de fond (type de tâche) ou s'il
   tombe en heures creuses — l'étiquette d'interactivité du client n'existe pas
   encore dans les journaux ;
2. une sortie est **structurée** si la réponse est un document JSON ;
3. une sortie est **longue** si elle dépasse `ratio_sortie_longue` fois la
   médiane de la tâche.

Exigences visées : EF-ATT-02, EF-LEV-01..06, EF-REJ-01, ENF-CONF-01.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from jauge.attribution.logs import EnregistrementInexploitable, analyser_enregistrement
from jauge.common.ids import deterministic
from jauge.engine.stats import percentile
from jauge.integration.plan_de_donnees import PlanDeDonnees, RequeteRejouable
from jauge.levers.base import HEURES_NOCTURNES, ProfilRequetes
from jauge.models.campaign import ParametresRejeu

#: Types de requêtes considérés comme du travail de fond (non interactif).
TYPES_NON_INTERACTIFS = frozenset({"resume", "batch", "backfill", "entrainement"})

#: Ratio par lequel une sortie dépasse nettement la médiane de la tâche.
RATIO_SORTIE_LONGUE = 1.5

#: Part des requêtes qui doivent partager un préfixe pour qu'il soit commun.
SEUIL_PART_PREFIXE = 0.9


# --- Lecture d'une ligne de journal (plan de données) --------------------------


def corps_json(ligne: Mapping[str, object], cle: str) -> dict[str, object]:
    """Corps JSON d'une ligne de journal (chaîné ou déjà décodé)."""
    brut = ligne.get(cle)
    if isinstance(brut, Mapping):
        return dict(brut)
    if isinstance(brut, str) and brut.strip()[:1] in ("{", "["):
        try:
            charge = json.loads(brut)
        except json.JSONDecodeError:
            return {}
        return dict(charge) if isinstance(charge, dict) else {}
    return {}


def _blocs_texte(contenus: object) -> list[str]:
    """Textes des blocs de contenu d'un message Bedrock."""
    if not isinstance(contenus, list):
        return []
    return [str(bloc["text"]) for bloc in contenus if isinstance(bloc, dict) and "text" in bloc]


def texte_de_requete(corps: Mapping[str, object]) -> str:
    """Texte de la requête (messages utilisateur) — ne sort jamais du plan."""
    morceaux: list[str] = []
    for message in corps.get("messages") or []:
        if isinstance(message, Mapping):
            morceaux.extend(_blocs_texte(message.get("content")))
    return "\n".join(morceaux)


def prefixe_systeme(corps: Mapping[str, object]) -> str:
    """Préfixe système commun à plusieurs requêtes."""
    return "\n".join(_blocs_texte(corps.get("system")))


def parametres_de_ligne(corps: Mapping[str, object]) -> ParametresRejeu:
    """Paramètres d'inférence d'origine (rejoués à l'identique, EF-REJ-02)."""
    config = corps.get("inferenceConfig")
    config = config if isinstance(config, Mapping) else {}
    return ParametresRejeu(
        temperature=float(config.get("temperature") or 0.0),
        max_tokens=int(config.get("maxTokens") or 1024),
        top_p=float(config.get("topP") or 1.0),
        stop_sequences=[str(item) for item in (config.get("stopSequences") or [])],
    )


def texte_de_reponse(ligne: Mapping[str, object]) -> str:
    """Texte de la réponse du modèle (data plane)."""
    sortie = ligne.get("output")
    if isinstance(sortie, str):
        sortie = corps_json(ligne, "output")
    if not isinstance(sortie, Mapping):
        return ""
    message = sortie.get("message")
    if not isinstance(message, Mapping):
        return ""
    return "\n".join(_blocs_texte(message.get("content")))


def ligne_en_erreur(ligne: Mapping[str, object]) -> bool:
    """Vrai si l'appel s'est terminé en erreur."""
    code = ligne.get("errorCode")
    return bool(code) and str(code).lower() not in ("none", "null")


def signature_de_ligne(ligne: Mapping[str, object]) -> str:
    """Empreinte stable du corps de requête (jamais le texte lui-même)."""
    return deterministic(str(ligne.get("input", "")))


# --- Observations et profil agrégé --------------------------------------------


@dataclass
class Observations:
    """Observations brutes d'une application, côté plan de données."""

    application_id: str
    entrees: list[int] = field(default_factory=list)
    sorties: list[int] = field(default_factory=list)
    types: Counter[str] = field(default_factory=Counter)
    heures: Counter[int] = field(default_factory=Counter)
    niveaux: Counter[str] = field(default_factory=Counter)
    heures_non_interactives: Counter[int] = field(default_factory=Counter)
    niveaux_non_interactifs: Counter[str] = field(default_factory=Counter)
    erreurs: int = 0
    relances: int = 0
    doublons: int = 0
    sorties_structurees: int = 0
    sorties_distinctes: set[str] = field(default_factory=set)
    prefixes: Counter[str] = field(default_factory=Counter)
    limites: list[int] = field(default_factory=list)
    requetes: list[RequeteRejouable] = field(default_factory=list)
    textes: dict[str, str] = field(default_factory=dict)
    vus: set[str] = field(default_factory=set)
    signatures_en_erreur: set[str] = field(default_factory=set)


def construire_plan_de_donnees(
    lignes: Iterable[Mapping[str, object]],
    tenant_id: str,
    *,
    ratio_sortie_longue: float = RATIO_SORTIE_LONGUE,
) -> PlanDeDonnees:
    """Remplit un plan de données neuf d'un client à partir de ses journaux."""
    return remplir_plan_de_donnees(lignes, tenant_id, None, ratio_sortie_longue=ratio_sortie_longue)


def remplir_plan_de_donnees(
    lignes: Iterable[Mapping[str, object]],
    tenant_id: str,
    plan: PlanDeDonnees | None = None,
    *,
    ratio_sortie_longue: float = RATIO_SORTIE_LONGUE,
) -> PlanDeDonnees:
    """Remplit le plan de données d'un client (plan partagé ou nouveau)."""
    plan = plan if plan is not None else PlanDeDonnees()
    observations: dict[str, Observations] = {}
    for ligne in lignes:
        _observer(ligne, observations)
    for application_id, obs in observations.items():
        plan.enregistrer_profil(tenant_id, application_id, _profil_de(obs, ratio_sortie_longue))
        plan.enregistrer_requetes(tenant_id, application_id, obs.requetes, obs.textes)
    return plan


def _observer(ligne: Mapping[str, object], observations: dict[str, Observations]) -> None:
    """Intègre une ligne de journal dans les observations de son application."""
    try:
        enregistrement = analyser_enregistrement(ligne)
    except EnregistrementInexploitable:
        return
    application_id = enregistrement.etiquettes.application or "non-attribue"
    obs = observations.setdefault(application_id, Observations(application_id=application_id))
    signature = signature_de_ligne(ligne)
    if ligne_en_erreur(ligne):
        obs.erreurs += 1
        obs.signatures_en_erreur.add(signature)
        return
    if signature in obs.signatures_en_erreur:
        obs.relances += 1
        # Une relance n'est comptée qu'une fois : l'erreur est consommée.
        obs.signatures_en_erreur.discard(signature)
    _ajouter(obs, enregistrement, ligne, signature)


def _ajouter(
    obs: Observations,
    enregistrement: object,
    ligne: Mapping[str, object],
    signature: str,
) -> None:
    """Enregistre une requête réussie dans les observations."""
    metriques = enregistrement.metriques  # type: ignore[attr-defined]
    type_requete = enregistrement.etiquettes.type_requete or "general"  # type: ignore[attr-defined]
    corps = corps_json(ligne, "input")
    texte = texte_de_requete(corps)
    prefixe = prefixe_systeme(corps)
    parametres = parametres_de_ligne(corps)
    heure = enregistrement.horodatage.hour  # type: ignore[attr-defined]
    niveau = enregistrement.niveau_service.value  # type: ignore[attr-defined]
    obs.entrees.append(metriques.jetons_entree)
    obs.sorties.append(metriques.jetons_sortie)
    obs.types[type_requete] += 1
    obs.heures[heure] += 1
    obs.niveaux[niveau] += 1
    if _non_interactif(heure, type_requete):
        obs.heures_non_interactives[heure] += 1
        obs.niveaux_non_interactifs[niveau] += 1
    if signature in obs.vus:
        obs.doublons += 1
    obs.vus.add(signature)
    if prefixe:
        obs.prefixes[prefixe] += 1
    if texte_de_reponse(ligne).strip().startswith("{"):
        obs.sorties_structurees += 1
    obs.sorties_distinctes.add(deterministic(signature, texte_de_reponse(ligne)))
    obs.limites.append(parametres.max_tokens)
    obs.requetes.append(
        RequeteRejouable(
            requete_id=enregistrement.request_id,  # type: ignore[attr-defined]
            application_id=obs.application_id,
            type_requete=type_requete,
            longueur_caracteres=len(texte),
            jetons_entree_origine=metriques.jetons_entree,
            parametres=parametres,
        )
    )
    obs.textes[enregistrement.request_id] = texte  # type: ignore[attr-defined]


def _non_interactif(heure: int, type_requete: str) -> bool:
    """Vrai si l'appel est du travail de fond (hypothèse documentée)."""
    return heure in HEURES_NOCTURNES or type_requete.lower() in TYPES_NON_INTERACTIFS


def _profil_de(obs: Observations, ratio_sortie_longue: float) -> ProfilRequetes:
    """Construit le profil agrégé d'une application (aucun texte)."""
    total = len(obs.entrees)
    mediane = percentile(obs.sorties, 50)
    seuil_long = ratio_sortie_longue * mediane
    longues = sum(1 for sortie in obs.sorties if sortie >= seuil_long)
    prefixe, occurrences = obs.prefixes.most_common(1)[0] if obs.prefixes else ("", 0)
    part_prefixe = occurrences / total if total else 0.0
    return ProfilRequetes(
        longueur_entree_moyenne=_moyenne(obs.entrees),
        longueur_entree_p95=int(percentile(obs.entrees, 95)),
        longueur_sortie_moyenne=_moyenne(obs.sorties),
        longueur_sortie_mediane=int(mediane),
        longueur_sortie_p95=int(percentile(obs.sorties, 95)),
        part_requetes_sortie_longue=longues / total if total else 0.0,
        limite_sortie_modele=max(obs.limites) if obs.limites else 0,
        requetes_par_type_tache=dict(obs.types),
        part_sortie_structuree=obs.sorties_structurees / total if total else 0.0,
        diversite_sorties=len(obs.sorties_distinctes) / total if total else 1.0,
        part_prefixe_long=part_prefixe,
        longueur_prefixe_long=len(prefixe) // 4 if part_prefixe >= SEUIL_PART_PREFIXE else 0,
        factor_reutilisation_prefixe=float(occurrences) if occurrences else 1.0,
        requetes_par_heure=_par_heure(obs.heures),
        requetes_non_interactive_par_heure=_par_heure(obs.heures_non_interactives),
        nombre_erreurs=obs.erreurs,
        nombre_relances_apres_erreur=obs.relances,
        requetes_par_niveau=dict(obs.niveaux),
        requetes_non_interactive_par_niveau=dict(obs.niveaux_non_interactifs),
        part_requetes_identiques=obs.doublons / total if total else 0.0,
    )


def _moyenne(valeurs: list[int]) -> float:
    """Moyenne d'une série de compteurs."""
    return sum(valeurs) / len(valeurs) if valeurs else 0.0


def _par_heure(compteurs: Counter[int]) -> tuple[int, ...]:
    """Répartition sur 24 heures (index 0 = minuit)."""
    return tuple(compteurs.get(heure, 0) for heure in range(24))
