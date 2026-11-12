"""Vérifications croisées d'un dossier de preuve : les sections doivent se répondre.

Un pack n'est pas seulement une pile de nombres recalculables (c'est le rôle de
`proof_pack_verification`) : c'est un dossier **cohérent**. Une recommandation
qui ne reflète pas les résultats mesurés, une économie qui ne suit pas les coûts,
des contrôles déterministes qui n'auraient pas tourné sur tout l'échantillon —
tout cela se détecte en comparant les sections entre elles, sans aucun accès au
système qui a produit le pack.

Chaque fonction rend une liste de `Verification` ; aucune ne lève d'exception sur
un pack falsifié : un vérificateur qui plante ne prouve rien, il rend un verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jauge.engine.stats import baisse_pourcentage, economie_mensuelle
from jauge.reports.proof_pack_sections import accord_declare, part_msp
from jauge.reports.proof_pack_verif_base import (
    TOLERANCE_COUT,
    TOLERANCE_MENSUELLE,
    TOLERANCE_TAUX,
    Verification,
    candidat_par_modele,
    candidats,
    egal,
)
from jauge.reports.scellement import empreinte


def verifier_controles(sections: Mapping[str, Any]) -> list[Verification]:
    """Contrôles déterministes : population, bornes, cohérence des échecs (§13.3)."""
    verifications: list[Verification] = []
    resultats = {candidat.get("modele_id"): candidat for candidat in candidats(sections)}
    for bloc in sections.get("controles", {}).get("par_candidat", []):
        modele = bloc.get("modele_id", "?")
        effectif = bloc.get("nombre_requetes_rejouees")
        echecs_critiques = 0
        for controle in bloc.get("controles", []):
            nom = f"controles.{modele}.{controle.get('nom')}"
            executions = controle.get("executions")
            echecs = controle.get("echecs")
            verifications.append(
                Verification(
                    nom=f"{nom}.population",
                    section="controles",
                    conforme=executions == effectif,
                    attendu=effectif,
                    declare=executions,
                    detail="un contrôle déterministe tourne sur toutes les paires rejouées",
                )
            )
            verifications.append(
                Verification(
                    nom=f"{nom}.bornes",
                    section="controles",
                    conforme=isinstance(echecs, int) and 0 <= echecs <= (executions or 0),
                    attendu=f"0 <= echecs <= {executions}",
                    declare=echecs,
                    detail="le nombre d'échecs ne peut pas dépasser la population",
                )
            )
            if controle.get("critique"):
                echecs_critiques += int(echecs or 0)
        declare = (resultats.get(modele) or {}).get("echecs_deterministes")
        if isinstance(declare, int):
            verifications.append(
                Verification(
                    nom=f"controles.{modele}.echecs_deterministes",
                    section="controles",
                    conforme=declare <= echecs_critiques,
                    attendu=f"<= {echecs_critiques}",
                    declare=declare,
                    detail="les paires en échec critique ne peuvent pas dépasser les échecs "
                    "critiques comptés",
                )
            )
    return verifications


def verifier_economie(sections: Mapping[str, Any]) -> list[Verification]:
    """Économie attendue, baisse relative et part du MSP (§4, §7.2)."""
    economie = sections.get("economie", {})
    avant = economie.get("cout_par_requete_avant_usd")
    apres = economie.get("cout_par_requete_apres_usd")
    volume = economie.get("volume_mensuel")
    if not isinstance(avant, int | float) or not isinstance(apres, int | float):
        return []
    if not isinstance(volume, int):
        return []
    attendue = economie_mensuelle(float(avant), float(apres), volume)
    baisse = baisse_pourcentage(float(avant), float(apres))
    part = part_msp(attendue, float(economie.get("part_msp_pourcentage", 0.0)))
    return [
        Verification(
            nom="economie.attendue",
            section="economie",
            conforme=egal(
                economie.get("economie_mensuelle_attendue_usd"), attendue, TOLERANCE_MENSUELLE
            ),
            attendu=attendue,
            declare=economie.get("economie_mensuelle_attendue_usd"),
            detail="économie recalculée : (coût avant − coût après) × volume mensuel",
        ),
        Verification(
            nom="economie.baisse",
            section="economie",
            conforme=egal(economie.get("baisse_pourcentage"), baisse, TOLERANCE_MENSUELLE),
            attendu=baisse,
            declare=economie.get("baisse_pourcentage"),
            detail="baisse relative recalculée à partir des deux coûts par requête",
        ),
        Verification(
            nom="economie.part_msp",
            section="economie",
            conforme=egal(economie.get("part_msp_mensuelle_usd"), part, TOLERANCE_MENSUELLE),
            attendu=part,
            declare=economie.get("part_msp_mensuelle_usd"),
            detail="part du MSP recalculée sur l'économie attendue (§7.2)",
        ),
    ]


def paires_recommandation(sections: Mapping[str, Any]) -> list[tuple[str, Any, Any]]:
    """Couples (nom, valeur mesurée du pack, valeur de la recommandation) à confronter."""
    reco = sections.get("recommandation", {})
    identite = sections.get("identite", {})
    echantillon = sections.get("echantillon", {})
    economie = sections.get("economie", {})
    qualite = reco.get("quality", {})
    cout = reco.get("cost", {})
    latence = reco.get("latency", {})
    cible = candidat_par_modele(sections, reco.get("targetModel"))
    avant = candidat_par_modele(sections, reco.get("currentModel"))
    return [
        ("campagne", identite.get("campagne_id"), reco.get("campaignId")),
        ("application", identite.get("application_id"), reco.get("applicationId")),
        ("modele_actuel", identite.get("modele_actuel"), reco.get("currentModel")),
        ("taille_echantillon", echantillon.get("taille"), reco.get("sampleSize")),
        ("taux_equivalence", cible.get("taux_equivalence"), qualite.get("equivalenceRate")),
        (
            "intervalle_bas",
            cible.get("intervalle_confiance_bas"),
            qualite.get("confidenceIntervalLow"),
        ),
        (
            "auto_accord",
            avant.get("auto_accord_modele_actuel"),
            qualite.get("selfAgreementOfCurrentModel"),
        ),
        (
            "echecs_deterministes",
            cible.get("echecs_deterministes"),
            qualite.get("deterministicFailures"),
        ),
        ("cout_avant", avant.get("cout_par_requete_usd"), cout.get("costPerRequestBefore")),
        ("cout_apres", cible.get("cout_par_requete_usd"), cout.get("costPerRequestAfter")),
        ("latence_p50_avant", avant.get("latence_p50_ms"), latence.get("p50Before")),
        ("latence_p50_apres", cible.get("latence_p50_ms"), latence.get("p50After")),
        ("latence_p95_avant", avant.get("latence_p95_ms"), latence.get("p95Before")),
        ("latence_p95_apres", cible.get("latence_p95_ms"), latence.get("p95After")),
        ("volume", economie.get("volume_mensuel"), cout.get("monthlyRequests")),
        (
            "economie_attendue",
            economie.get("economie_mensuelle_attendue_usd"),
            cout.get("expectedMonthlySaving"),
        ),
    ]


def verifier_recommandation(sections: Mapping[str, Any]) -> list[Verification]:
    """La recommandation (Annexe B) doit refléter les sections mesurées du pack."""
    reco = sections.get("recommandation", {})
    verifications = [
        Verification(
            nom=f"recommandation.{nom}",
            section="recommandation",
            conforme=egal(attendu, declare, TOLERANCE_COUT),
            attendu=attendu,
            declare=declare,
            detail="la recommandation (Annexe B) doit refléter les sections mesurées du pack",
        )
        for nom, attendu, declare in paires_recommandation(sections)
    ]
    verifications.append(
        Verification(
            nom="recommandation.modele_cible_present",
            section="recommandation",
            conforme=bool(candidat_par_modele(sections, reco.get("targetModel"))),
            attendu="un candidat portant cet identifiant dans les résultats",
            declare=reco.get("targetModel"),
            detail="le modèle cible doit figurer parmi les candidats mesurés",
        )
    )
    return verifications


def verifier_relecture(sections: Mapping[str, Any]) -> list[Verification]:
    """Accord juge/humain recalculé depuis les comptes de paires (§13.5)."""
    relecture = sections.get("relecture", {})
    relues = relecture.get("paires_relues")
    concordantes = relecture.get("paires_concordantes")
    if not isinstance(relues, int) or relues <= 0 or not isinstance(concordantes, int):
        return []
    attendu = accord_declare(concordantes, relues)
    declare = relecture.get("accord_juge_humain")
    return [
        Verification(
            nom="relecture.accord",
            section="relecture",
            conforme=egal(declare, attendu, TOLERANCE_TAUX),
            attendu=attendu,
            declare=declare,
            detail="accord juge/humain recalculé : paires concordantes / paires relues",
        )
    ]


def verifier_tarifs(sections: Mapping[str, Any]) -> list[Verification]:
    """Table des tarifs : empreinte recalculée et nombre de lignes déclaré."""
    tarifs = sections.get("protocole", {}).get("tarifs", {})
    lignes = tarifs.get("lignes", [])
    empreinte_calculee = empreinte(lignes)
    return [
        Verification(
            nom="tarifs.empreinte",
            section="protocole",
            conforme=egal(tarifs.get("empreinte"), empreinte_calculee),
            attendu=empreinte_calculee,
            declare=tarifs.get("empreinte"),
            detail="l'empreinte de la table des tarifs se recalcule depuis ses lignes",
        ),
        Verification(
            nom="tarifs.nombre_lignes",
            section="protocole",
            conforme=tarifs.get("nombre_lignes") == len(lignes),
            attendu=len(lignes),
            declare=tarifs.get("nombre_lignes"),
            detail="le nombre de lignes déclaré doit égaler la table listée",
        ),
    ]
