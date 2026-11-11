"""Exécution d'une campagne de rejeu sur un échantillon de requêtes.

Exigences visées :
- EF-REJ-02 : chaque requête est rejouée sur le modèle actuel **et** sur chaque
  candidat avec exactement les mêmes paramètres (`ParametresRejeu`) ;
- EF-REJ-03 : le rejeu tourne dans le compte du client au **niveau Flex** ;
- EF-REJ-04 : on mesure pour chaque réponse les jetons, le coût (via
  `jauge.engine.calculator`, jamais un LLM) et la latence (p50/p95 via
  `jauge.engine.stats.percentile`) ;
- EF-EVA-04 : le modèle actuel est rejoué **deux fois** — la première réponse
  sert de référence fraîche, la comparaison des deux donne la variabilité
  naturelle du modèle (auto-accord mesuré ici, avant tout juge) ;
- ENF-CONF-01 : la trace complète (`PaireRejoueeDataPlane`) ne sort jamais du
  plan de données ; le plan de contrôle ne reçoit que `ResumeRejeu`, qui ne
  contient **aucun** texte de requête ni de réponse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from jauge.common.errors import ValidationError
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.engine.stats import percentile
from jauge.models.campaign import EchantillonRequete, PaireRejoueeDataPlane, ParametresRejeu
from jauge.models.pricing import MetriquesRequete, ServiceTier
from jauge.replay.client import DemandeRejeu, ModeleClient, ResultatInvocation

# Niveau de service imposé au rejeu (§13.2, EF-REJ-03).
NIVEAU_REJEU = ServiceTier.FLEX
# Nombre d'exécutions du modèle actuel par requête (§13.2 : « deux fois »).
EXECUTIONS_REFERENCE = 2


@dataclass(frozen=True)
class TextesEnMemoire:
    """Source de textes du plan de données : identifiant de requête → texte.

    L'exécuteur ne lit jamais les textes ailleurs : c'est la seule porte
    d'entrée du contenu, et elle vit côté plan de données (ENF-CONF-01).
    """

    textes: Mapping[str, str] = field(default_factory=dict)

    def texte(self, requete_id: str) -> str:
        """Texte d'une requête ; erreur métier s'il est absent."""
        try:
            return self.textes[requete_id]
        except KeyError as erreur:
            raise ValidationError(
                "Texte introuvable pour cette requête dans le plan de données.",
                requete_id=requete_id,
            ) from erreur


@dataclass(frozen=True)
class MesuresCandidat:
    """Résumé chiffré d'un modèle sur l'échantillon — **sans aucun texte**."""

    modele_id: str
    est_reference: bool
    nombre_requetes_rejouees: int
    executions: int
    jetons_entree_total: int
    jetons_sortie_total: int
    cout_total_usd: float
    cout_par_requete_usd: float
    latence_p50_ms: int
    latence_p95_ms: int
    erreurs: int = 0


@dataclass(frozen=True)
class ResumeRejeu:
    """Vue de contrôle d'un rejeu : ce que le plan de contrôle a le droit de voir.

    Aucun champ ne contient de texte de requête ou de réponse (ENF-CONF-01).
    """

    campagne_id: str
    application_id: str
    modele_actuel: str
    niveau: ServiceTier
    taille_echantillon: int
    auto_accord_modele_actuel: float
    mesures: tuple[MesuresCandidat, ...] = ()

    def pour_modele(self, modele_id: str) -> MesuresCandidat | None:
        """Mesures d'un modèle donné, si le rejeu a produit un résultat."""
        for mesure in self.mesures:
            if mesure.modele_id == modele_id:
                return mesure
        return None


@dataclass(frozen=True)
class RejeuComplet:
    """Résultat d'un rejeu : le résumé de contrôle et la trace du plan de données.

    `paires` ne doit **jamais** être renvoyé par un outil d'agent ni par l'API du
    plan de contrôle (ENF-CONF-01) : il contient les textes.
    """

    resume: ResumeRejeu
    paires: tuple[PaireRejoueeDataPlane, ...] = ()

    def vers_controle(self) -> ResumeRejeu:
        """Résumé destiné au plan de contrôle (sans texte)."""
        return self.resume


@dataclass
class _Accumulateur:
    """Cumul interne des mesures d'un modèle pendant le rejeu."""

    modele_id: str
    est_reference: bool
    requetes: set[str] = field(default_factory=set)
    jetons_entree: int = 0
    jetons_sortie: int = 0
    cout_usd: float = 0.0
    latences: list[int] = field(default_factory=list)
    erreurs: int = 0

    def ajouter(self, requete_id: str, invocation: ResultatInvocation, cout_usd: float) -> None:
        self.requetes.add(requete_id)
        self.jetons_entree += invocation.jetons_entree
        self.jetons_sortie += invocation.jetons_sortie
        self.cout_usd += cout_usd
        self.latences.append(invocation.latence_ms)

    def vers_mesures(self) -> MesuresCandidat:
        """Fige le cumul en résumé immuable."""
        reussies = len(self.latences)
        return MesuresCandidat(
            modele_id=self.modele_id,
            est_reference=self.est_reference,
            nombre_requetes_rejouees=len(self.requetes),
            executions=reussies,
            jetons_entree_total=self.jetons_entree,
            jetons_sortie_total=self.jetons_sortie,
            cout_total_usd=round(self.cout_usd, 8),
            cout_par_requete_usd=round(self.cout_usd / reussies, 8) if reussies else 0.0,
            latence_p50_ms=int(round(percentile(self.latences, 50))),
            latence_p95_ms=int(round(percentile(self.latences, 95))),
            erreurs=self.erreurs,
        )


class ExecuteurRejeu:
    """Rejoue un échantillon sur le modèle actuel puis sur chaque candidat.

    Le client de modèle est injecté (`ModeleClient`) : `BedrockModeleClient` en
    production, `FakeModeleClient` en test. La table des tarifs et la date
    d'effet sont explicites : aucun tarif n'est deviné (ENF-IA-01).
    """

    def __init__(
        self,
        client: ModeleClient,
        tarifs: TableTarifs,
        *,
        region: str,
        a_la_date: date,
        niveau: ServiceTier = NIVEAU_REJEU,
    ) -> None:
        self.client = client
        self.tarifs = tarifs
        self.region = region
        self.a_la_date = a_la_date
        self.niveau = niveau

    # --- API publique ------------------------------------------------------
    def rejouer(
        self,
        *,
        campagne_id: str,
        application_id: str,
        modele_actuel: str,
        candidats: Sequence[str],
        echantillon: Sequence[EchantillonRequete],
        parametres: ParametresRejeu,
        textes: TextesEnMemoire,
        consigne_systeme: str = "",
    ) -> RejeuComplet:
        """Rejoue l'échantillon complet et rend résumé de contrôle + paires."""
        if not echantillon:
            raise ValidationError("Échantillon vide : rien à rejouer.", campagne_id=campagne_id)
        cumuls = {modele_actuel: _Accumulateur(modele_actuel, est_reference=True)}
        for candidat in candidats:
            if candidat != modele_actuel:
                cumuls.setdefault(candidat, _Accumulateur(candidat, est_reference=False))

        paires: list[PaireRejoueeDataPlane] = []
        accords = 0
        for requete in echantillon:
            texte = textes.texte(requete.requete_id)
            accord = self._rejouer_requete(
                campagne_id=campagne_id,
                requete=requete,
                texte=texte,
                modele_actuel=modele_actuel,
                candidats=[c for c in cumuls if c != modele_actuel],
                parametres=parametres,
                consigne_systeme=consigne_systeme,
                cumuls=cumuls,
                paires=paires,
            )
            accords += 1 if accord else 0

        auto_accord = round(accords / len(echantillon), 6)
        resume = ResumeRejeu(
            campagne_id=campagne_id,
            application_id=application_id,
            modele_actuel=modele_actuel,
            niveau=self.niveau,
            taille_echantillon=len(echantillon),
            auto_accord_modele_actuel=auto_accord,
            mesures=tuple(cumul.vers_mesures() for cumul in cumuls.values()),
        )
        return RejeuComplet(resume=resume, paires=tuple(paires))

    # --- Détail d'une requête ---------------------------------------------
    def _rejouer_requete(
        self,
        *,
        campagne_id: str,
        requete: EchantillonRequete,
        texte: str,
        modele_actuel: str,
        candidats: Sequence[str],
        parametres: ParametresRejeu,
        consigne_systeme: str,
        cumuls: dict[str, _Accumulateur],
        paires: list[PaireRejoueeDataPlane],
    ) -> bool:
        """Rejoue une requête ; rend True si les deux exécutions de référence concordent."""
        references = self._rejouer_reference(
            requete, texte, modele_actuel, parametres, consigne_systeme, cumuls
        )
        # Les deux exécutions de référence sont nécessaires (§13.2) : si l'une
        # échoue, la requête est écartée de la mesure de variabilité.
        if len(references) < EXECUTIONS_REFERENCE:
            return False
        accord = references[0].texte == references[1].texte
        for candidat in candidats:
            self._rejouer_candidat(
                campagne_id=campagne_id,
                requete=requete,
                texte=texte,
                modele_id=candidat,
                reference=references[0],
                parametres=parametres,
                consigne_systeme=consigne_systeme,
                cumuls=cumuls,
                paires=paires,
            )
        return accord

    def _rejouer_candidat(
        self,
        *,
        campagne_id: str,
        requete: EchantillonRequete,
        texte: str,
        modele_id: str,
        reference: ResultatInvocation,
        parametres: ParametresRejeu,
        consigne_systeme: str,
        cumuls: dict[str, _Accumulateur],
        paires: list[PaireRejoueeDataPlane],
    ) -> None:
        """Rejoue un candidat et enregistre la paire (plan de données)."""
        mesure = self._invoquer(requete, modele_id, texte, parametres, consigne_systeme)
        if mesure is None:
            cumuls[modele_id].erreurs += 1
            return
        invocation, cout = mesure
        cumuls[modele_id].ajouter(requete.requete_id, invocation, cout)
        paires.append(
            PaireRejoueeDataPlane(
                campagne_id=campagne_id,
                requete_id=requete.requete_id,
                modele_candidat_id=modele_id,
                texte_requete=texte,
                reponse_reference=reference.texte,
                reponse_candidate=invocation.texte,
                # Le score d'équivalence appartient au module d'évaluation (M5) :
                # M4 construit la paire, il ne la note pas.
                score_equivalence=0.0,
                explication_juge="",
            )
        )

    def _invoquer(
        self,
        requete: EchantillonRequete,
        modele_id: str,
        texte: str,
        parametres: ParametresRejeu,
        consigne_systeme: str,
    ) -> tuple[ResultatInvocation, float] | None:
        """Invoque un modèle et rend (réponse, coût USD) ; None en cas d'erreur."""
        demande = DemandeRejeu(
            requete_id=requete.requete_id,
            modele_id=modele_id,
            texte_requete=texte,
            parametres=parametres,
            niveau=self.niveau,
            consigne_systeme=consigne_systeme,
        )
        try:
            invocation = self.client.invoquer(demande)
        except Exception:  # panne du modèle : on compte et on continue
            return None
        tarif = self.tarifs.trouver(modele_id, self.region, self.niveau, self.a_la_date)
        metriques = MetriquesRequete(
            jetons_entree=invocation.jetons_entree,
            jetons_sortie=invocation.jetons_sortie,
            jetons_cache_ecriture=invocation.jetons_cache_ecriture,
            jetons_cache_lecture=invocation.jetons_cache_lecture,
            latence_ms=invocation.latence_ms,
        )
        return invocation, calculer_cout_requete(tarif, metriques).cout_total

    def _rejouer_reference(
        self,
        requete: EchantillonRequete,
        texte: str,
        modele_actuel: str,
        parametres: ParametresRejeu,
        consigne_systeme: str,
        cumuls: dict[str, _Accumulateur],
    ) -> list[ResultatInvocation]:
        """Rejoue le modèle actuel deux fois (§13.2) et rend les deux réponses."""
        obtenues: list[ResultatInvocation] = []
        for _ in range(EXECUTIONS_REFERENCE):
            mesure = self._invoquer(requete, modele_actuel, texte, parametres, consigne_systeme)
            if mesure is None:
                cumuls[modele_actuel].erreurs += 1
                continue
            invocation, cout = mesure
            cumuls[modele_actuel].ajouter(requete.requete_id, invocation, cout)
            obtenues.append(invocation)
        return obtenues
