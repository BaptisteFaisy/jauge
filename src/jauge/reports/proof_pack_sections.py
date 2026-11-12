"""Constructeurs des sections d'un dossier de preuve (« proof pack »).

Règle absolue de ce module : il n'invente **rien**. Chaque valeur provient soit
d'une entité du socle (`Campagne`, `Application`, `ResultatCandidat`,
`Recommandation`, `TarifModele`), soit d'un calcul déjà implémenté ailleurs
(`jauge.engine.calculator` pour le coût de l'Annexe D, `jauge.engine.stats` pour
l'économie mensuelle). Aucun chiffre produit par un modèle (ENF-IA-01), aucun
texte de requête ni de réponse (ENF-CONF-01) : les sections ne portent que des
identifiants, des empreintes et des nombres.

Les libellés destinés au lecteur du pack (notes de méthode, règles citées) sont
en **anglais** : le dossier de preuve est lu par un auditeur, un juge du
hackathon ou le client (BRIEF §2.8). Les commentaires de code restent en
français.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from jauge import __version__
from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.engine.stats import Z_95, baisse_pourcentage, economie_mensuelle
from jauge.evaluation.judge import NOM_METRIQUE, NOTE_EQUIVALENTE, PROMPT_EQUIVALENCE
from jauge.models.campaign import Campagne
from jauge.models.pricing import MetriquesRequete, ServiceTier, TarifModele
from jauge.models.recommendation import Recommandation, VerificationEconomie
from jauge.models.tenant import Application, Tenant
from jauge.replay.executor import EXECUTIONS_REFERENCE
from jauge.reports.proof_pack_entrees import (
    CHAMPS_METRIQUES,
    CHAMPS_PROFIL_JETONS,
    Approbation,
    EntreeCandidat,
    EntreeEchantillon,
    ResumeRelecture,
    normaliser_protocole,
)
from jauge.reports.scellement import empreinte

#: Modules dont la version fait partie du protocole scellé (reproductibilité).
PAQUET_HARNAIS = "jauge.replay.executor"
PAQUET_JUGE = "jauge.evaluation.judge"
PAQUET_CONTROLES = "jauge.evaluation.checks"

#: Confiance des intervalles publiés (§13.4 : intervalle bilatéral à 95 %).
CONFIANCE_POURCENTAGE = 95.0

#: Textes de méthode, en anglais (lus par l'auditeur, jamais calculés).
REGLE_DECISION = (
    "A candidate is proposed only if all three conditions of spec section 13.4 hold: "
    "its equivalence rate reaches the application threshold, the lower bound of its "
    "95% Wilson interval stays above the floor, and no critical deterministic check fails."
)
MODE_CALCUL_ECONOMIE = "(cost_per_request_before - cost_per_request_after) * monthly_requests"
BASE_ECONOMIE = "expected saving at constant request volume (spec sections 4 and 7.2)"
NOTE_CONFIDENTIALITE = (
    "Request and response texts never leave the client account (ENF-CONF-01): "
    "this pack carries identifiers, fingerprints and aggregates only."
)
NOTE_COUT_RECALCULE = (
    "cost_per_request_recomputed_usd applies the Annex D formula to the mean token "
    "profile of the candidate and to the dated price table sealed in the protocol "
    "section; a third party can recompute it from this pack alone."
)


def lignes_tarifs(tarifs: Sequence[TarifModele]) -> list[dict[str, Any]]:
    """Lignes de la table des tarifs, triées pour une empreinte reproductible.

    Le tri (modèle, niveau, date d'effet, région) rend l'empreinte indépendante
    de l'ordre de chargement du fichier de tarifs.
    """
    return [
        {
            "modele_id": tarif.modele_id,
            "region": tarif.region,
            "niveau": tarif.niveau.value,
            "date_effet": tarif.date_effet.isoformat(),
            "prix_entree_1k": tarif.prix_entree_1k,
            "prix_sortie_1k": tarif.prix_sortie_1k,
            "prix_cache_ecriture_1k": tarif.prix_cache_ecriture_1k,
            "prix_cache_lecture_1k": tarif.prix_cache_lecture_1k,
            "devise": tarif.devise,
        }
        for tarif in sorted(
            tarifs,
            key=lambda ligne: (
                ligne.modele_id,
                ligne.niveau.value,
                ligne.date_effet.isoformat(),
                ligne.region,
            ),
        )
    ]


def cout_recalcule(
    table: TableTarifs,
    modele_id: str,
    region: str,
    niveau: ServiceTier,
    a_la_date: date,
    profil: dict[str, int],
) -> float:
    """Coût par requête de l'Annexe D, appliqué au profil moyen de jetons.

    Même fonction côté construction et côté rejeu des vérifications : un tiers
    obtient donc **exactement** le même nombre depuis le pack seul.
    """
    tarif = table.trouver(modele_id, region, niveau, a_la_date)
    metriques = MetriquesRequete(
        **{CHAMPS_METRIQUES[champ]: int(profil[champ]) for champ in CHAMPS_PROFIL_JETONS}
    )
    return calculer_cout_requete(tarif, metriques).cout_total


def section_identite(
    *,
    campagne: Campagne,
    application: Application,
    tenant: Tenant | None,
    region: str,
    a_la_date: date,
) -> dict[str, Any]:
    """Identité de la campagne : qui, quoi, sur quel modèle, avec quelle graine."""
    return {
        "campagne_id": campagne.id,
        "client_id": campagne.tenant_id,
        "client_nom": tenant.nom if tenant else None,
        "application_id": application.id,
        "application_nom": application.nom,
        "equipe": application.equipe,
        "environnement": application.environnement,
        "tache": application.description_tache,
        "modele_actuel": application.modele_actuel,
        "modeles_candidats": list(campagne.modeles_candidats),
        "modele_juge": campagne.modele_juge_id,
        "graine": campagne.graine_aleatoire,
        "taille_echantillon": campagne.taille_echantillon,
        "statut_campagne": campagne.statut.value,
        "cout_estime_usd": campagne.cout_estime_usd,
        "cout_reel_usd": campagne.cout_reel_usd,
        "region": region,
        "devise": "USD",
        "date_reference": a_la_date.isoformat(),
    }


def section_protocole(
    *,
    campagne: Campagne,
    application: Application,
    tarifs: Sequence[TarifModele],
    region: str,
    niveau: ServiceTier,
    a_la_date: date,
    protocole_pre_enregistre: object | None = None,
) -> dict[str, Any]:
    """Protocole et versions : harnais, juge, contrôles, table des tarifs datée."""
    lignes = lignes_tarifs(tarifs)
    return {
        "pre_enregistre": normaliser_protocole(protocole_pre_enregistre),
        "harnais": {
            "paquet": PAQUET_HARNAIS,
            "version": __version__,
            "niveau_rejeu": niveau.value,
            "executions_reference": EXECUTIONS_REFERENCE,
        },
        "juge": {
            "paquet": PAQUET_JUGE,
            "version": __version__,
            "modele_id": campagne.modele_juge_id,
            "metrique": NOM_METRIQUE,
            "note_equivalente": NOTE_EQUIVALENTE,
            "empreinte_invite_juge": empreinte(PROMPT_EQUIVALENCE.template),
        },
        "controles": {"paquet": PAQUET_CONTROLES, "version": __version__},
        "tarifs": {
            "region": region,
            "niveau": niveau.value,
            "date_reference": a_la_date.isoformat(),
            "empreinte": empreinte(lignes),
            "nombre_lignes": len(lignes),
            "lignes": lignes,
        },
        "seuils": {
            "equivalence": application.seuil_qualite,
            "intervalle_bas": application.seuil_intervalle_bas,
            "confiance_pourcentage": CONFIANCE_POURCENTAGE,
            "z_score": Z_95,
        },
        "regle_decision": REGLE_DECISION,
        "confidentialite": NOTE_CONFIDENTIALITE,
    }


def section_echantillon(
    *, entrees: Sequence[EntreeEchantillon], campagne: Campagne
) -> dict[str, Any]:
    """Échantillon figé : identifiants, métadonnées et empreintes (§13.1).

    L'empreinte globale de l'échantillon permet à un tiers de vérifier que la
    liste n'a pas été recomposée après coup (ni triée, ni filtrée).
    """
    lignes = [entree.vers_dict() for entree in entrees]
    strates: dict[str, int] = {}
    for ligne in lignes:
        strates[ligne["strate"]] = strates.get(ligne["strate"], 0) + 1
    return {
        "taille": len(lignes),
        "graine": campagne.graine_aleatoire,
        "strates": dict(sorted(strates.items())),
        "empreinte_echantillon": empreinte(lignes),
        "requetes": lignes,
        "confidentialite": NOTE_CONFIDENTIALITE,
    }


def candidat_vers_dict(
    entree: EntreeCandidat,
    table: TableTarifs,
    region: str,
    niveau: ServiceTier,
    a_la_date: date,
) -> dict[str, Any]:
    """Un candidat tel que publié : effectifs, taux, intervalle, latences, coûts."""
    resultat = entree.resultat
    profil = entree.profil_jetons()
    ligne: dict[str, Any] = {
        "modele_id": entree.modele_id,
        "est_reference": resultat.est_reference,
        "nombre_requetes_rejouees": resultat.nombre_requetes_rejouees,
        "succes": entree.succes_declares(),
        "taux_equivalence": resultat.taux_equivalence,
        "intervalle_confiance_bas": resultat.intervalle_confiance_bas,
        "intervalle_confiance_haut": resultat.intervalle_confiance_haut,
        "confiance_pourcentage": CONFIANCE_POURCENTAGE,
        "z_score": Z_95,
        "auto_accord_modele_actuel": resultat.auto_accord_modele_actuel,
        "echecs_deterministes": resultat.echecs_deterministes,
        "accord_juge_humain": resultat.accord_juge_humain,
        "latence_p50_ms": resultat.latence_p50_ms,
        "latence_p95_ms": resultat.latence_p95_ms,
        "familles_en_echec": list(resultat.familles_en_echec),
        "conforme_seuils": resultat.conforme_seuils,
        "cout_par_requete_usd": resultat.cout_par_requete_usd,
        "cout_par_requete_mesure_usd": (
            None if entree.mesures is None else entree.mesures.cout_par_requete_usd
        ),
        "cout_recalculable": profil is not None,
    }
    ligne.update(profil or dict.fromkeys(CHAMPS_PROFIL_JETONS))
    if profil is not None:
        ligne["cout_par_requete_recalcule_usd"] = cout_recalcule(
            table, entree.modele_id, region, niveau, a_la_date, profil
        )
        ligne["note_cout"] = NOTE_COUT_RECALCULE
    else:
        ligne["cout_par_requete_recalcule_usd"] = None
    return ligne


def section_resultats(
    *,
    candidats: Sequence[EntreeCandidat],
    table: TableTarifs,
    region: str,
    niveau: ServiceTier,
    a_la_date: date,
) -> dict[str, Any]:
    """Résultats par candidat, dans l'ordre reçu (jamais triés : l'ordre scellé compte)."""
    if not candidats:
        raise ValidationError("aucun candidat : un dossier de preuve vide ne prouve rien")
    return {
        "candidats": [
            candidat_vers_dict(entree, table, region, niveau, a_la_date) for entree in candidats
        ]
    }


def section_controles(candidats: Sequence[EntreeCandidat]) -> dict[str, Any]:
    """Contrôles déterministes et leurs résultats agrégés (§13.3)."""
    par_candidat = [
        {
            "modele_id": entree.modele_id,
            "nombre_requetes_rejouees": entree.resultat.nombre_requetes_rejouees,
            "controles": [controle.vers_dict() for controle in entree.controles],
        }
        for entree in candidats
    ]
    return {
        "par_candidat": par_candidat,
        "total_executions": sum(
            controle.executions for entree in candidats for controle in entree.controles
        ),
        "note": "deterministic checks are code-only verdicts (spec section 13.3); failure reasons "
        "may quote answer fragments, so they stay in the client account (ENF-CONF-01)",
    }


def table_depuis_lignes(lignes: Sequence[Mapping[str, Any]]) -> TableTarifs:
    """Reconstruit la table des tarifs datée depuis les lignes scellées dans le pack.

    Fonction inverse de `lignes_tarifs` : c'est ainsi qu'un tiers relit la table
    des tarifs **depuis le pack seul**, sans accès au fichier de tarifs.
    """
    tarifs = [
        TarifModele(
            modele_id=str(ligne["modele_id"]),
            region=str(ligne["region"]),
            niveau=ServiceTier(str(ligne["niveau"])),
            date_effet=date.fromisoformat(str(ligne["date_effet"])),
            prix_entree_1k=float(ligne["prix_entree_1k"]),
            prix_sortie_1k=float(ligne["prix_sortie_1k"]),
            prix_cache_ecriture_1k=float(ligne.get("prix_cache_ecriture_1k", 0.0)),
            prix_cache_lecture_1k=float(ligne.get("prix_cache_lecture_1k", 0.0)),
            devise=str(ligne.get("devise", "USD")),
        )
        for ligne in lignes
    ]
    return TableTarifs(tarifs)


def part_msp(economie_usd: float, pourcentage: float) -> float:
    """Part du MSP sur l'économie (§7.2) : le pourcentage vient du contrat.

    Fonction partagée par la construction de la section et par le rejeu des
    vérifications : un tiers recalcule exactement la même valeur.
    """
    return round(economie_usd * pourcentage / 100.0, 2)


def accord_declare(concordantes: int, relues: int) -> float:
    """Accord juge/humain depuis les comptes de paires (même arrondi que le socle)."""
    if relues <= 0:
        return 0.0
    return round(concordantes / relues, 6)


def section_relecture(
    relecture: ResumeRelecture | None, application: Application
) -> dict[str, Any]:
    """Relecture humaine et accord juge/humain (§13.5, ENF-CONF-02)."""
    if relecture is None:
        return {
            "autorisee": application.relecture_humaine_autorisee,
            "taille_sous_echantillon": 0,
            "paires_relues": 0,
            "paires_concordantes": 0,
            "accord_juge_humain": None,
            "relecteur_id": "",
            "note": "no human review was performed for this campaign",
        }
    section = relecture.vers_dict(application.relecture_humaine_autorisee)
    if section["accord_juge_humain"] is None and relecture.paires_relues > 0:
        # Accord non fourni par l'appelant : le calcul du socle est rejoué ici.
        section["accord_juge_humain"] = accord_declare(
            relecture.paires_concordantes, relecture.paires_relues
        )
    return section


def section_economie(
    *,
    recommandation: Recommandation,
    part_msp_pourcentage: float,
    verification: VerificationEconomie | None = None,
) -> dict[str, Any]:
    """Économie attendue et son mode de calcul (§4, §7.2), plus la part du MSP.

    Tous les montants viennent du bloc `cost` de la recommandation (Annexe B),
    lui-même calculé par `jauge.integration.recommandation` avec
    `jauge.engine.stats.economie_mensuelle`. Rien n'est recalculé ici : la
    section **expose** le mode de calcul pour qu'un tiers le rejoue.
    """
    cout = recommandation.cost
    economie = economie_mensuelle(
        cout.costPerRequestBefore, cout.costPerRequestAfter, cout.monthlyRequests
    )
    if abs(economie - cout.expectedMonthlySaving) > 0.01:
        raise ValidationError(
            "économie attendue de la recommandation incohérente avec ses coûts",
            declaree=cout.expectedMonthlySaving,
            recalculee=economie,
        )
    section: dict[str, Any] = {
        "mode_calcul": MODE_CALCUL_ECONOMIE,
        "base": BASE_ECONOMIE,
        "modele_avant": recommandation.currentModel,
        "modele_apres": recommandation.targetModel,
        "cout_par_requete_avant_usd": cout.costPerRequestBefore,
        "cout_par_requete_apres_usd": cout.costPerRequestAfter,
        "volume_mensuel": cout.monthlyRequests,
        "economie_mensuelle_attendue_usd": cout.expectedMonthlySaving,
        "baisse_pourcentage": baisse_pourcentage(
            cout.costPerRequestBefore, cout.costPerRequestAfter
        ),
        "part_msp_pourcentage": part_msp_pourcentage,
        "part_msp_mensuelle_usd": part_msp(cout.expectedMonthlySaving, part_msp_pourcentage),
        "devise": cout.currency,
        "verifiee_sur_facture": verification.model_dump(mode="json") if verification else None,
    }
    return section


def section_recommandation(recommandation: Recommandation) -> dict[str, Any]:
    """Recommandation au format de l'Annexe B, recopiée telle quelle."""
    return recommandation.model_dump(mode="json")


def section_approbation(
    *,
    approbation: Approbation | None,
    recommandation: Recommandation,
    genere_le: str,
) -> dict[str, Any]:
    """Horodatage et auteur de l'approbation (EF-REC-02, ENF-IA-02, §16)."""
    return {
        "statut": approbation.statut if approbation else recommandation.status.value,
        "approuve_par": (
            approbation.auteur if approbation and approbation.auteur else recommandation.approvedBy
        ),
        "horodatage_approbation": approbation.horodatage if approbation else None,
        "notes": approbation.notes if approbation else (recommandation.approvalNotes or ""),
        "pack_genere_le": genere_le,
        "note": "no rollout happens without a human approval (ENF-IA-02)",
    }
