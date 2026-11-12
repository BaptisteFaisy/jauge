"""Preuve par rejeu d'une variante de prompt (piste n° 8 du top 10).

Une variante de prompt est un **candidat** comme un autre : ce qui change est
le prompt, pas le modèle. La preuve réutilise donc la machinerie existante :

- le rejeu (`jauge.replay.executor.ExecuteurRejeu`) rejoue l'échantillon sur
  le modèle courant (référence, deux exécutions) et sur la variante, avec les
  **mêmes paramètres** (§13.2) ;
- l'évaluation (`jauge.integration.evaluation.evaluer_rejeu`) note les paires
  par le modèle juge, exécute les **contrôles déterministes** de la tâche et
  applique la règle de décision du §13.4 ;
- le gain net est chiffré par le code : économie mensuelle mesurée par le
  rejeu, moins le coût de la campagne de preuve (§17.1).

ENF-CONF-01 : ni le prompt, ni son diff, ni les réponses ne sortent du plan
de données ; `PreuveVariante.vers_controle()` ne rend que des identifiants,
des nombres et des motifs.
ENF-IA-03 : le contenu rejoué est traité comme une donnée ; aucune
transformation n'est déléguée à un modèle, aucune consigne du client n'est
interprétée comme une instruction.

Exigences visées : Annexe A (preuve exigée « campagne de rejeu »), §13.4,
EF-LEV-06, EF-EVA-01..03, EF-REJ-02..04.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from jauge.engine.calculator import TableTarifs
from jauge.engine.stats import economie_mensuelle
from jauge.evaluation.decision import DecisionCandidat, SeuilsDecision, decider
from jauge.evaluation.judge import JugeClient
from jauge.integration.evaluation import evaluer_rejeu
from jauge.models.campaign import EchantillonRequete, ParametresRejeu
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.tenant import Application
from jauge.replay.client import (
    JETONS_PAR_CARACTERE,
    DemandeRejeu,
    ModeleClient,
    ResultatInvocation,
)
from jauge.replay.cost import JETONS_SORTIE_MOYENS_DEFAUT, HypothesesCout, estimer_cout_campagne
from jauge.replay.executor import ExecuteurRejeu, RejeuComplet, TextesEnMemoire
from jauge.replay.variantes import VariantePrompt


@dataclass(frozen=True)
class PreuveVariante:
    """Résultat de la preuve par rejeu d'une variante (aucun texte de prompt)."""

    variante_id: str
    transformation: str
    jetons_origine: int
    jetons_variante: int
    jetons_economises: int
    cout_par_requete_avant: float
    cout_par_requete_apres: float
    economie_mensuelle_usd: float
    cout_preuve_usd: float
    gain_net_12_mois_usd: float
    proposee: bool
    motifs: tuple[str, ...] = ()
    decision: DecisionCandidat | None = None
    refus: str = ""

    def vers_controle(self) -> dict[str, object]:
        """Vue destinée au plan de contrôle : aucun fragment de prompt (ENF-CONF-01)."""
        return {
            "variante_id": self.variante_id,
            "transformation": self.transformation,
            "jetons_origine": self.jetons_origine,
            "jetons_variante": self.jetons_variante,
            "jetons_economises": self.jetons_economises,
            "cout_par_requete_avant_usd": self.cout_par_requete_avant,
            "cout_par_requete_apres_usd": self.cout_par_requete_apres,
            "economie_mensuelle_usd": self.economie_mensuelle_usd,
            "cout_preuve_usd": self.cout_preuve_usd,
            "gain_net_12_mois_usd": self.gain_net_12_mois_usd,
            "proposee": self.proposee,
            "motifs": list(self.motifs),
            "refus": self.refus,
            "decision": self.decision.vers_dict() if self.decision else None,
        }


class ClientVariantePrompt:
    """Adaptateur de rejeu : la variante remplace la consigne du candidat.

    Le **modèle** ne change pas, c'est le **prompt** qui change : l'adaptateur
    substitue la consigne système de la variante puis délègue au client réel.
    """

    def __init__(
        self,
        client: ModeleClient,
        variante: VariantePrompt,
        modele_reel: str,
        *,
        ajuster_jetons: bool = False,
    ) -> None:
        self.client = client
        self.variante = variante
        self.modele_reel = modele_reel
        self.ajuster_jetons = ajuster_jetons

    def invoquer(self, demande: DemandeRejeu) -> ResultatInvocation:
        """Rejoue la demande ; la variante remplace la consigne du candidat."""
        if demande.modele_id != self.variante.variante_id:
            return self.client.invoquer(demande)
        substituee = replace(
            demande,
            modele_id=self.modele_reel,
            consigne_systeme=self.variante.consigne_variante,
        )
        invocation = self.client.invoquer(substituee)
        return self._ajuster(invocation, demande)

    def _ajuster(self, invocation: ResultatInvocation, demande: DemandeRejeu) -> ResultatInvocation:
        """Corrige les jetons d'entrée d'un adaptateur fictif qui ignore la consigne.

        Les clients réels (Bedrock) rapportent la consigne dans l'usage mesuré :
        ce réglage doit rester désactivé avec eux.
        """
        if not self.ajuster_jetons:
            return invocation
        delta = int(
            round(
                (len(self.variante.consigne_variante) - len(demande.consigne_systeme))
                * JETONS_PAR_CARACTERE
            )
        )
        return replace(invocation, jetons_entree=max(0, invocation.jetons_entree + delta))


class TableTarifsVariante(TableTarifs):
    """Table des tarifs où la variante est tarifée comme le modèle qu'elle remplace."""

    def __init__(self, table: TableTarifs, variante_id: str, modele_reel: str) -> None:
        super().__init__()
        self._table = table
        self._variante_id = variante_id
        self._modele_reel = modele_reel

    def trouver(
        self, modele_id: str, region: str, niveau: ServiceTier, a_la_date: date
    ) -> TarifModele:
        """Tarif du modèle réel quand on interroge une variante."""
        if modele_id == self._variante_id:
            modele_id = self._modele_reel
        return self._table.trouver(modele_id, region, niveau, a_la_date)


def _cout_preuve(contexte: ContextePreuve, variante: VariantePrompt) -> float:
    """Coût estimé de la campagne de preuve (rejeu + jugements), §17.1."""
    entrees = [requete.jetons_entree_origine for requete in contexte.echantillon]
    hypotheses = HypothesesCout(
        jetons_entree_moyens=int(sum(entrees) / len(entrees)) if entrees else 0,
        jetons_sortie_moyens=contexte.jetons_sortie_moyens,
    )
    estimation = estimer_cout_campagne(
        tarifs=contexte.table,
        region=contexte.region,
        a_la_date=contexte.a_la_date,
        modele_actuel=contexte.application.modele_actuel,
        candidats=[variante.variante_id],
        taille_echantillon=len(contexte.echantillon),
        hypotheses=hypotheses,
        modele_juge_id=contexte.juge.modele_id,
    )
    return estimation.cout_total_usd


@dataclass(frozen=True)
class ContextePreuve:
    """Ce dont la preuve a besoin : application, rejeu, tarifs, volume."""

    application: Application
    rejeu: RejeuComplet
    types: dict[str, str]
    juge: JugeClient
    table: TableTarifs
    region: str
    a_la_date: date
    echantillon: tuple[EchantillonRequete, ...]
    volume_mensuel: int
    jetons_sortie_moyens: int


def _preuve_refusee(variante: VariantePrompt) -> PreuveVariante:
    """Preuve d'une variante refusée à la génération : rien n'est rejoué."""
    return PreuveVariante(
        variante_id=variante.variante_id,
        transformation=variante.transformation,
        jetons_origine=variante.jetons_origine,
        jetons_variante=variante.jetons_variante,
        jetons_economises=0,
        cout_par_requete_avant=0.0,
        cout_par_requete_apres=0.0,
        economie_mensuelle_usd=0.0,
        cout_preuve_usd=0.0,
        gain_net_12_mois_usd=0.0,
        proposee=False,
        motifs=(f"variante refusée à la génération : {variante.refus}",),
        refus=variante.refus,
    )


def _couts_mesures(
    rejeu: RejeuComplet, application: Application, variante_id: str
) -> tuple[float, float]:
    """Coût par requête mesuré par le rejeu : référence puis variante."""
    mesure_ref = rejeu.resume.pour_modele(application.modele_actuel)
    mesure_var = rejeu.resume.pour_modele(variante_id)
    return (
        mesure_ref.cout_par_requete_usd if mesure_ref else 0.0,
        mesure_var.cout_par_requete_usd if mesure_var else 0.0,
    )


def _motifs(
    decision: DecisionCandidat | None, gain_net: float, cout_preuve: float
) -> tuple[str, ...]:
    """Motifs de non-proposition : décision du §13.4 puis critère de gain net."""
    motifs = list(decision.motifs) if decision else ["rejeu sans résultat mesurable"]
    if gain_net <= 0:
        motifs.append(
            f"gain net à 12 mois {gain_net:.2f} $ nul ou négatif "
            f"(coût de preuve {cout_preuve:.2f} $)"
        )
    return tuple(motifs)


def _trancher(variante: VariantePrompt, contexte: ContextePreuve) -> PreuveVariante:
    """Applique la règle du §13.4 puis calcule le gain net de la variante."""
    resultats, _clusters, _paires = evaluer_rejeu(
        contexte.application,
        contexte.rejeu,
        contexte.types,
        contexte.juge,
        contexte.juge.modele_id,
    )
    resultat = next((r for r in resultats if r.modele_id == variante.variante_id), None)
    decision = (
        decider(resultat, SeuilsDecision.depuis_application(contexte.application))
        if resultat
        else None
    )
    cout_avant, cout_apres = _couts_mesures(
        contexte.rejeu, contexte.application, variante.variante_id
    )
    economie = economie_mensuelle(cout_avant, cout_apres, contexte.volume_mensuel)
    cout_preuve = _cout_preuve(contexte, variante)
    gain_net = round(economie * 12 - cout_preuve, 2)
    proposee = bool(decision and decision.propose and gain_net > 0)
    return PreuveVariante(
        variante_id=variante.variante_id,
        transformation=variante.transformation,
        jetons_origine=variante.jetons_origine,
        jetons_variante=variante.jetons_variante,
        jetons_economises=variante.jetons_economises,
        cout_par_requete_avant=round(cout_avant, 8),
        cout_par_requete_apres=round(cout_apres, 8),
        economie_mensuelle_usd=economie,
        cout_preuve_usd=round(cout_preuve, 6),
        gain_net_12_mois_usd=gain_net,
        proposee=proposee,
        motifs=_motifs(decision, gain_net, cout_preuve),
        decision=decision,
    )


def _rejouer(
    variante: VariantePrompt,
    consigne_originale: str,
    client: ModeleClient,
    textes: TextesEnMemoire,
    parametres: ParametresRejeu,
    echantillon: tuple[EchantillonRequete, ...],
    application: Application,
    table: TableTarifs,
    region: str,
    a_la_date: date,
    ajuster_jetons: bool,
) -> RejeuComplet:
    """Rejoue l'échantillon sur la référence et sur la variante de prompt."""
    executeur = ExecuteurRejeu(
        ClientVariantePrompt(
            client, variante, application.modele_actuel, ajuster_jetons=ajuster_jetons
        ),
        table,
        region=region,
        a_la_date=a_la_date,
    )
    return executeur.rejouer(
        campagne_id=f"campagne-{variante.variante_id}",
        application_id=application.id,
        modele_actuel=application.modele_actuel,
        candidats=[variante.variante_id],
        echantillon=list(echantillon),
        parametres=parametres,
        textes=textes,
        consigne_systeme=consigne_originale,
    )


@dataclass(frozen=True)
class EntreePreuve:
    """Paramètres d'une preuve par rejeu, regroupés pour rester lisibles."""

    application: Application
    variante: VariantePrompt
    consigne_originale: str
    client: ModeleClient
    tarifs: TableTarifs
    region: str
    a_la_date: date
    echantillon: tuple[EchantillonRequete, ...]
    textes: TextesEnMemoire
    parametres: ParametresRejeu
    juge: JugeClient
    volume_mensuel: int
    jetons_sortie_moyens: int = JETONS_SORTIE_MOYENS_DEFAUT
    ajuster_jetons: bool = False


def _prouver(entree: EntreePreuve) -> PreuveVariante:
    """Rejoue la variante puis applique la décision et le critère de gain net."""
    if not entree.variante.acceptee:
        return _preuve_refusee(entree.variante)
    table = TableTarifsVariante(
        entree.tarifs, entree.variante.variante_id, entree.application.modele_actuel
    )
    rejeu = _rejouer(
        entree.variante,
        entree.consigne_originale,
        entree.client,
        entree.textes,
        entree.parametres,
        entree.echantillon,
        entree.application,
        table,
        entree.region,
        entree.a_la_date,
        entree.ajuster_jetons,
    )
    contexte = ContextePreuve(
        application=entree.application,
        rejeu=rejeu,
        types={requete.requete_id: requete.type_requete for requete in entree.echantillon},
        juge=entree.juge,
        table=table,
        region=entree.region,
        a_la_date=entree.a_la_date,
        echantillon=entree.echantillon,
        volume_mensuel=entree.volume_mensuel,
        jetons_sortie_moyens=entree.jetons_sortie_moyens,
    )
    return _trancher(entree.variante, contexte)


def prouver_variante(
    *,
    application: Application,
    variante: VariantePrompt,
    consigne_originale: str,
    client: ModeleClient,
    tarifs: TableTarifs,
    region: str,
    a_la_date: date,
    echantillon: Sequence[EchantillonRequete],
    textes: TextesEnMemoire,
    parametres: ParametresRejeu,
    juge: JugeClient,
    volume_mensuel: int,
    jetons_sortie_moyens: int = JETONS_SORTIE_MOYENS_DEFAUT,
    ajuster_jetons: bool = False,
) -> PreuveVariante:
    """Rejoue la variante et tranche : la qualité d'abord, le gain net ensuite.

    Formule du gain net : économie mensuelle mesurée × 12 − coût de la campagne
    de preuve (§17.1). La variante n'est proposée que si la décision du §13.4
    est favorable **et** que le gain net est strictement positif.
    """
    return _prouver(
        EntreePreuve(
            application=application,
            variante=variante,
            consigne_originale=consigne_originale,
            client=client,
            tarifs=tarifs,
            region=region,
            a_la_date=a_la_date,
            echantillon=tuple(echantillon),
            textes=textes,
            parametres=parametres,
            juge=juge,
            volume_mensuel=volume_mensuel,
            jetons_sortie_moyens=jetons_sortie_moyens,
            ajuster_jetons=ajuster_jetons,
        )
    )
