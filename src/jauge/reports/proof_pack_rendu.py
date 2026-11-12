"""Rendus du dossier de preuve : JSON pour la machine, Markdown pour l'humain.

Les deux vues partagent **exactement** le même contenu : `rendre_json_pack`
sérialise les sections scellées, `rendre_markdown_pack` les met en forme en
anglais pour un lecteur non technique (auditeur, juge du hackathon, directeur
financier du client). Aucun chiffre n'est calculé ici (ENF-IA-01) : le rendu ne
fait que présenter des valeurs déjà fixées et scellées.

Le rendu Markdown inclut, quand on lui passe le résultat d'un rejeu, le tableau
des vérifications recalculées : c'est la partie « ne me croyez pas, vérifiez »
du dossier.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from jauge.reports.proof_pack import ORDRE_SECTIONS, ProofPack
from jauge.reports.proof_pack_verification import ResultatRejeu
from jauge.reports.scellement import serialiser_canonique

#: Contenu statique d'explication, en anglais (lu par l'auditeur).
INTRODUCTION = (
    "This proof pack is a self-contained record of one replay campaign. Every "
    "section is sealed by a SHA-256 hash chain, so any later edit is detectable, "
    "and every derived figure can be recomputed from this document alone: "
    "Wilson intervals from the counts, cost per request from the token profile "
    "and the dated price table, expected saving from the costs and the volume."
)
CONFIDENTIALITE = (
    "No request text and no answer text appear in this document. Only identifiers, "
    "fingerprints and aggregates leave the client account (ENF-CONF-01)."
)
MODE_VERIFICATION = (
    "1. Parse this document and keep the `sections` object as received.\n"
    "2. Recompute the SHA-256 fingerprint of each section from its canonical JSON "
    "(sorted keys, `,`/`:` separators, UTF-8, no NaN) and walk the chain: each "
    "cumulative hash is `sha256(previous|section)`.\n"
    "3. Compare the final fingerprint with the one published separately at approval "
    "time (audit log, sealed bucket, e-mail).\n"
    "4. Replay the arithmetic: Wilson intervals, Annex D costs, expected saving, "
    "judge/human agreement. In Python: `rejouer_verifications(pack)`."
)


def _tableau(entetes: Sequence[str], lignes: Iterable[Sequence[Any]]) -> list[str]:
    """Tableau Markdown : entêtes, alignement, puis une ligne par donnée."""
    sortie = ["| " + " | ".join(entetes) + " |", "|" + "|".join(["---"] * len(entetes)) + "|"]
    sortie += ["| " + " | ".join(str(valeur) for valeur in ligne) + " |" for ligne in lignes]
    return sortie


def _texte(valeur: Any) -> str:
    """Valeur affichable : `None` rendu en `n/a`, jamais en « None » Python."""
    return "n/a" if valeur is None else str(valeur)


def _pourcentage(valeur: Any) -> str:
    """Ratio (0–1) en pourcentage lisible ; `None` rendu en texte."""
    return "n/a" if valeur is None else f"{float(valeur) * 100.0:.2f}%"


def _montant(valeur: Any, devise: str = "USD") -> str:
    """Montant en USD, six décimales pour les coûts unitaires."""
    return "n/a" if valeur is None else f"{float(valeur):.6f} {devise}"


def _intervalle(candidat: dict[str, Any]) -> str:
    """Intervalle de Wilson publié : [bas ; haut] à 95 %."""
    bas = candidat.get("intervalle_confiance_bas")
    haut = candidat.get("intervalle_confiance_haut")
    confiance = candidat.get("confiance_pourcentage")
    if bas is None or haut is None:
        return "n/a"
    return f"[{_pourcentage(bas)} ; {_pourcentage(haut)}] @ {confiance}%"


def _entete(pack: ProofPack, identite: dict[str, Any], approbation: dict[str, Any]) -> list[str]:
    """Titre et fiche d'identité du dossier."""
    recommandation = pack.section("recommandation")
    return [
        f"# Proof pack — {identite.get('application_nom')} ({identite.get('campagne_id')})",
        "",
        f"**Pack ID:** `{pack.id}`  ",
        f"**Pack format version:** {pack.version}  ",
        f"**Generated:** {approbation.get('pack_genere_le')}  ",
        f"**Client:** {identite.get('client_nom') or identite.get('client_id')} "
        f"(`{identite.get('client_id')}`)  ",
        f"**Application:** {identite.get('application_nom')} "
        f"(`{identite.get('application_id')}`, team {identite.get('equipe')})  ",
        f"**Current model:** `{identite.get('modele_actuel')}` → "
        f"**recommended model:** `{recommandation.get('targetModel')}`  ",
        f"**Recommendation status:** {recommandation.get('status')}  ",
        f"**Approval:** {approbation.get('approuve_par') or 'pending'} "
        f"at {approbation.get('horodatage_approbation') or 'n/a'}",
        "",
        "## 1. What this document is",
        "",
        INTRODUCTION,
        "",
        f"> {CONFIDENTIALITE}",
        "",
    ]


def _bloc_protocole(protocole: dict[str, Any]) -> list[str]:
    """Protocole, versions et table des tarifs datée."""
    harnais = protocole.get("harnais", {})
    juge = protocole.get("juge", {})
    tarifs = protocole.get("tarifs", {})
    seuils = protocole.get("seuils", {})
    lignes = [
        "## 2. Protocol and versions",
        "",
    ]
    lignes += _tableau(
        ["Item", "Value"],
        [
            ("Replay harness", f"{harnais.get('paquet')} v{harnais.get('version')}"),
            ("Replay service tier", harnais.get("niveau_rejeu")),
            ("Runs of the current model", harnais.get("executions_reference")),
            ("Judge model", f"`{juge.get('modele_id')}`"),
            ("Judge metric", juge.get("metrique")),
            ("Judge prompt fingerprint", f"`sha256:{juge.get('empreinte_invite_juge')}`"),
            ("Equivalence threshold", juge.get("note_equivalente")),
            ("Application quality threshold", _pourcentage(seuils.get("equivalence"))),
            ("Confidence interval floor", _pourcentage(seuils.get("intervalle_bas"))),
            ("Wilson z-score", seuils.get("z_score")),
            ("Price table date", tarifs.get("date_reference")),
            ("Price table region / tier", f"{tarifs.get('region')} / {tarifs.get('niveau')}"),
            ("Price table fingerprint", f"`sha256:{tarifs.get('empreinte')}`"),
            ("Price table rows", tarifs.get("nombre_lignes")),
            (
                "Pre-registered protocol",
                _texte((protocole.get("pre_enregistre") or {}).get("empreinte")),
            ),
        ],
    )
    lignes += ["", protocole.get("regle_decision", ""), ""]
    return lignes


def _bloc_echantillon(echantillon: dict[str, Any]) -> list[str]:
    """Échantillon figé : effectifs et strates, jamais de texte."""
    lignes = [
        "## 3. Frozen sample",
        "",
        f"Requests replayed: **{echantillon.get('taille')}**, sampling seed "
        f"`{echantillon.get('graine')}`, sample fingerprint "
        f"`sha256:{echantillon.get('empreinte_echantillon')}`.",
        "",
    ]
    lignes += _tableau(["Stratum", "Requests"], sorted((echantillon.get("strates") or {}).items()))
    premier = (echantillon.get("requetes") or [{}])[0]
    lignes += [
        "",
        "Each request is listed by identifier and fingerprint only, for example "
        f"`{premier.get('requete_id')}` → `sha256:{premier.get('empreinte') or 'n/a'}`.",
        "",
        f"> {CONFIDENTIALITE}",
        "",
    ]
    return lignes


def _bloc_resultats(resultats: dict[str, Any]) -> list[str]:
    """Résultats par candidat : taux, intervalle, latences, coûts."""
    lignes = [
        "## 4. Candidate results",
        "",
    ]
    lignes += _tableau(
        [
            "Model",
            "Role",
            "n",
            "Equivalent",
            "Rate",
            "Wilson CI",
            "p50 (ms)",
            "p95 (ms)",
            "Cost/req",
        ],
        [
            (
                f"`{candidat.get('modele_id')}`",
                "reference" if candidat.get("est_reference") else "candidate",
                candidat.get("nombre_requetes_rejouees"),
                candidat.get("succes"),
                _pourcentage(candidat.get("taux_equivalence")),
                _intervalle(candidat),
                candidat.get("latence_p50_ms"),
                candidat.get("latence_p95_ms"),
                _montant(candidat.get("cout_par_requete_usd")),
            )
            for candidat in resultats.get("candidats", [])
        ],
    )
    lignes += ["", "Failing request families are listed per candidate in the JSON view.", ""]
    return lignes


def _bloc_controles(controles: dict[str, Any]) -> list[str]:
    """Contrôles déterministes et leurs résultats."""
    lignes = ["## 5. Deterministic checks", ""]
    for bloc in controles.get("par_candidat", []):
        lignes += [
            f"`{bloc.get('modele_id')}` — {bloc.get('nombre_requetes_rejouees')} pairs:",
            "",
        ]
        lignes += _tableau(
            ["Check", "Runs", "Failures", "Critical"],
            [
                (
                    controle.get("nom"),
                    controle.get("executions"),
                    controle.get("echecs"),
                    "yes" if controle.get("critique") else "no",
                )
                for controle in bloc.get("controles", [])
            ],
        )
        lignes += [""]
    return lignes


def _bloc_relecture(relecture: dict[str, Any]) -> list[str]:
    """Relecture humaine et accord juge/humain (§13.5)."""
    return [
        "## 6. Human review and judge agreement",
        "",
        f"Human review allowed: {'yes' if relecture.get('autorisee') else 'no'}. "
        f"Sub-sample size: {relecture.get('taille_sous_echantillon')}. "
        f"Pairs reviewed: {relecture.get('paires_relues')}, "
        f"agreeing with the judge: {relecture.get('paires_concordantes')}. "
        f"Judge/human agreement: **{_pourcentage(relecture.get('accord_juge_humain'))}** "
        f"(reviewer `{relecture.get('relecteur_id') or 'n/a'}`).",
        "",
    ]


def _bloc_economie(economie: dict[str, Any]) -> list[str]:
    """Économie attendue, son mode de calcul et la part du MSP (§7.2)."""
    lignes = ["## 7. Expected saving", ""]
    lignes += _tableau(
        ["Item", "Value"],
        [
            ("Formula", economie.get("mode_calcul")),
            ("Basis", economie.get("base")),
            ("Model before", f"`{economie.get('modele_avant')}`"),
            ("Model after", f"`{economie.get('modele_apres')}`"),
            ("Cost per request before", _montant(economie.get("cout_par_requete_avant_usd"))),
            ("Cost per request after", _montant(economie.get("cout_par_requete_apres_usd"))),
            ("Monthly requests", f"{economie.get('volume_mensuel'):,}"),
            (
                "Expected monthly saving",
                f"{economie.get('economie_mensuelle_attendue_usd'):,.2f} {economie.get('devise')}",
            ),
            ("Relative decrease", f"{economie.get('baisse_pourcentage')}%"),
            ("MSP share", f"{economie.get('part_msp_pourcentage')}%"),
            (
                "MSP monthly share",
                f"{economie.get('part_msp_mensuelle_usd'):,.2f} {economie.get('devise')}",
            ),
        ],
    )
    verification = economie.get("verifiee_sur_facture")
    lignes += [""]
    if verification:
        lignes += ["Saving verified on the invoice (spec section 7.2):", ""]
        lignes += _tableau(
            ["Item", "Value"],
            [
                ("Verified saving", f"{verification.get('economie_constatee_usd')} USD"),
                ("Expected saving", f"{verification.get('economie_attendue_usd')} USD"),
                ("Volume on the period", f"{verification.get('volume_periode'):,}"),
                ("Relative gap", f"{verification.get('ecart_pourcentage')}%"),
            ],
        )
    else:
        lignes += ["The saving has not been verified on the invoice yet."]
    return lignes + [""]


def _bloc_recommandation(recommandation: dict[str, Any]) -> list[str]:
    """Recommandation au format de l'Annexe B."""
    qualite = recommandation.get("quality", {})
    cout = recommandation.get("cost", {})
    latence = recommandation.get("latency", {})
    lignes = ["## 8. Recommendation (Annex B format)", ""]
    lignes += _tableau(
        ["Field", "Value"],
        [
            ("Application", recommandation.get("applicationId")),
            ("Current model", f"`{recommandation.get('currentModel')}`"),
            ("Target model", f"`{recommandation.get('targetModel')}`"),
            ("Campaign", recommandation.get("campaignId")),
            ("Sample size", recommandation.get("sampleSize")),
            ("Equivalence rate", _pourcentage(qualite.get("equivalenceRate"))),
            ("Confidence interval low", _pourcentage(qualite.get("confidenceIntervalLow"))),
            (
                "Self-agreement of current model",
                _pourcentage(qualite.get("selfAgreementOfCurrentModel")),
            ),
            ("Deterministic failures", qualite.get("deterministicFailures")),
            ("Judge/human agreement", _pourcentage(qualite.get("judgeHumanAgreement"))),
            ("Cost per request before", _montant(cout.get("costPerRequestBefore"))),
            ("Cost per request after", _montant(cout.get("costPerRequestAfter"))),
            ("Monthly requests", f"{cout.get('monthlyRequests'):,}"),
            ("Expected monthly saving", f"{cout.get('expectedMonthlySaving'):,.2f} USD"),
            (
                "Latency p50 before / after",
                f"{latence.get('p50Before')} / {latence.get('p50After')} ms",
            ),
            (
                "Latency p95 before / after",
                f"{latence.get('p95Before')} / {latence.get('p95After')} ms",
            ),
            (
                "Rollout plan",
                " → ".join(f"{palier}%" for palier in recommandation.get("rolloutPlan", [])),
            ),
            ("Risks", "; ".join(recommandation.get("risks") or []) or "none declared"),
            ("Status", recommandation.get("status")),
        ],
    )
    return lignes + [""]


def _bloc_approbation(approbation: dict[str, Any]) -> list[str]:
    """Auteur et horodatage de l'approbation (ENF-IA-02)."""
    return [
        "## 9. Approval",
        "",
        f"Status: **{approbation.get('statut')}**. "
        f"Approved by: {approbation.get('approuve_par') or 'pending'}. "
        f"Approved at: {approbation.get('horodatage_approbation') or 'n/a'}. "
        f"Pack generated at: {approbation.get('pack_genere_le')}.",
        "",
        f"> {approbation.get('note', '')}",
        "",
    ]


def _bloc_sceau(pack: ProofPack, verification: ResultatRejeu | None) -> list[str]:
    """Chaîne de hachage, signature et mode d'emploi de la vérification."""
    sceau = pack.scellement
    lignes = [
        "## 10. Seal: hash chain",
        "",
        f"Algorithm: `{sceau.get('algorithme')}`, genesis `{sceau.get('genese')}`. "
        "Each cumulative hash covers its own section **and every previous one**, so "
        "editing any section breaks the chain from that point on.",
        "",
    ]
    lignes += _tableau(
        ["#", "Section", "Section hash", "Cumulative hash"],
        [
            (rang, maillon.get("nom"), maillon.get("empreinte"), maillon.get("empreinte_cumulee"))
            for rang, maillon in enumerate(sceau.get("sections", []), start=1)
        ],
    )
    signature = sceau.get("signature") or {}
    lignes += [
        "",
        f"**Pack fingerprint:** `sha256:{pack.empreinte_pack}`",
        "",
        f"**Signature:** {signature.get('algorithme', 'none')} "
        f"(key `{signature.get('cle_id', 'n/a')}`) — `{signature.get('valeur', 'not signed')}`",
        "",
        "## 11. How a third party verifies this pack",
        "",
        MODE_VERIFICATION,
        "",
    ]
    if verification is not None:
        lignes += _bloc_verification(verification)
    return lignes


def _bloc_verification(verification: ResultatRejeu) -> list[str]:
    """Résultat du rejeu des vérifications, tel que produit par le vérificateur."""
    lignes = [
        "## 12. Replayed verifications",
        "",
        f"Recomputed checks: **{len(verification.verifications)}**, "
        f"failures: **{len(verification.echecs)}** — "
        f"{'the pack is internally consistent' if verification.conforme else 'INCONSISTENT PACK'}.",
        "",
    ]
    lignes += _tableau(
        ["Check", "Section", "Result", "Declared", "Recomputed"],
        [
            (
                item.nom,
                item.section,
                "OK" if item.conforme else "FAIL",
                _texte(item.declare),
                _texte(item.attendu),
            )
            for item in verification.verifications
        ],
    )
    return lignes + [""]


def rendre_markdown_pack(pack: ProofPack, verification: ResultatRejeu | None = None) -> str:
    """Rend le dossier de preuve en Markdown anglais, lisible par un non-technicien."""
    sections = {nom: pack.section(nom) for nom in ORDRE_SECTIONS if nom in pack.sections}
    lignes: list[str] = []
    lignes += _entete(pack, sections["identite"], sections["approbation"])
    lignes += _bloc_protocole(sections["protocole"])
    lignes += _bloc_echantillon(sections["echantillon"])
    lignes += _bloc_resultats(sections["resultats"])
    lignes += _bloc_controles(sections["controles"])
    lignes += _bloc_relecture(sections["relecture"])
    lignes += _bloc_economie(sections["economie"])
    lignes += _bloc_recommandation(sections["recommandation"])
    lignes += _bloc_approbation(sections["approbation"])
    lignes += _bloc_sceau(pack, verification)
    return "\n".join(lignes)


def rendre_json_pack(pack: ProofPack, *, indent: int | None = 2) -> str:
    """Vue JSON du pack : clés triées, UTF-8, prête à archiver ou à diffuser.

    `indent=None` rend la forme compacte ; la forme canonique (celle dont
    l'empreinte est scellée) est obtenue par `serialiser_canonique`.
    """
    return (
        json.dumps(
            pack.vers_dict(), sort_keys=True, ensure_ascii=False, indent=indent, allow_nan=False
        )
        + "\n"
    )


def serialiser_pack(pack: ProofPack) -> str:
    """Forme canonique du document complet (clé de comparaison entre machines)."""
    return serialiser_canonique(pack.vers_dict())
