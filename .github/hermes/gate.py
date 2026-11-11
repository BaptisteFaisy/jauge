#!/usr/bin/env python3
"""Gate Hermes Lite — fraîcheur exigée AU MOMENT DU MERGE ([HERMES-REFRESH], porté du gate `duello`).

Règles :
- retard sans conflit  → rebase serveur (PUT /pulls/N/update-branch) ;
  le rebase déclenche un événement `synchronize` qui re-run ce workflow → cycle auto.
- vrai conflit         → check rouge + label `hermes/conflit` (escalade humaine).
- frais + checks requis verts → merge (rebase), une PR à la fois.
"""

API = "https://api.github.com"
CHECK_NAME = "hermes-lite/verdict"
LABEL_OK = "hermes/approuvee"
LABEL_CONFLIT = "hermes/conflit"
LABEL_ESCALADE = "hermes/intervention-humaine"

import json
import time


def api(method, url, body=None, token=None):
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur

    data = _json.dumps(body).encode() if body is not None else None
    req = _ur.Request(API + url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with _ur.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (_json.loads(raw) if raw else None)
    except _ue.HTTPError as e:
        payload = e.read()
        try:
            return e.code, _json.loads(payload)
        except Exception:
            return e.code, payload.decode("utf-8", errors="replace")


def ensure_label(repo, token, name, color, desc):
    api(
        "POST",
        "/repos/%s/labels" % repo,
        token=token,
        body={"name": name, "color": color, "description": desc},
    )


def set_label(pr, repo, token, name, add):
    num = pr["number"]
    has = name in [l["name"] for l in pr.get("labels") or []]
    if add and not has:
        api(
            "POST", "/repos/%s/issues/%d/labels" % (repo, num), token=token, body={"labels": [name]}
        )
    elif not add and has:
        api("DELETE", "/repos/%s/issues/%d/labels/%s" % (repo, num, name), token=token)


def refresh_branch(repo, token, pr):
    """Rebase serveur d'une PR en retard. Renvoie 'refreshed'/'conflict'/'error'."""
    num = pr["number"]
    expected = pr["head"]["sha"]
    sc, resp = api(
        "PUT",
        "/repos/%s/pulls/%d/update-branch" % (repo, num),
        token=token,
        body={"update_method": "rebase", "expected_head_sha": expected},
    )
    if sc == 202:
        return "refreshed"
    if sc in (409, 422):
        return "conflict"
    print("[gate] #%d update-branch %s : %s" % (num, sc, resp), flush=True)
    return "error"


def create_check(repo, token, sha, ok, title, summary):
    api(
        "POST",
        "/repos/%s/check-runs" % repo,
        token=token,
        body={
            "name": CHECK_NAME,
            "head_sha": sha,
            "status": "completed",
            "conclusion": "success" if ok else "failure",
            "output": {"title": title, "summary": summary},
        },
    )


def check_runs_green(repo, token, sha, required):
    """Check runs GitHub Actions verts pour le SHA donné.

    Les jobs Actions sont des *check runs*, pas des statuts de commit : l'API
    `/status` ne les voit pas. On interroge donc `/check-runs` en premier, et
    l'on garde `/status` en repli (pilotage par services externes).
    """
    sc, data = api("GET", "/repos/%s/commits/%s/check-runs" % (repo, sha), token=token)
    if sc != 200 or not data:
        return False
    etats = {
        run.get("name"): (run.get("status"), run.get("conclusion"))
        for run in data.get("check_runs", [])
    }
    for ctx in required:
        statut, conclusion = etats.get(ctx, (None, None))
        if statut != "completed" or conclusion != "success":
            return False
    return True


def required_checks_green(repo, token, pr, required):
    """Statuts de commit requis verts. Liste vide => considéré vert."""
    if not required:
        return True
    sha = pr["head"]["sha"]
    if check_runs_green(repo, token, sha, required):
        return True
    sc, data = api("GET", "/repos/%s/commits/%s/status" % (repo, sha), token=token)
    if sc != 200 or not data:
        return False
    states = {s["context"]: s["state"] for s in data.get("statuses") or []}
    for ctx in required:
        if states.get(ctx) != "success":
            return False
    return True


def declencher_resolveur(pr, dry=False):
    """[M16] Vrai conflit → lance `hermes-resolver.yml` sur cette PR.

    Le résolveur rebase, résout (jambe InferHub puis repli déterministe),
    valide avec `bash scripts/ci-local.sh`, pousse en `--force-with-lease` et
    rapporte sur la PR. La logique (anti-doublon, backoff, escalade) vit dans
    `dispatch.py`, testée par `dispatch.py --selftest`.

    Best effort : une erreur de dispatch ne doit jamais empêcher le gate de
    conclure son verdict.
    """
    try:
        import dispatch

        dispatch.dispatch_resolver(pr, dry=dry)
    except Exception as e:  # noqa: BLE001 - best effort, jamais bloquant
        print("[gate] #%s dispatch résolveur : %r" % (pr.get("number"), e), flush=True)

def settle_after_refresh(repo, token, num, required, attempts=20, pause=10, dry=False):
    """[HERMES-REFRESH] Ré-évalue la PR après le rebase serveur, DANS CE RUN.

    Le push effectué par `update-branch` est un push de bot : il ne déclenche
    AUCUN workflow (anti-récursion GitHub), et hermes-lite n'a ni cron ni
    dispatch possible avec GITHUB_TOKEN. Sans cette boucle, une PR rebasée
    resterait ouverte pour toujours : personne ne la re-évaluerait.
    Pollue borné (~60 s), puis rend la main (la veille événementielle
    rattrapera au prochain check_run de ce head).
    """
    for i in range(attempts):
        time.sleep(pause if i else 2)
        sc, pr = api("GET", "/repos/%s/pulls/%d" % (repo, num), token=token)
        if sc != 200 or not isinstance(pr, dict):
            continue
        if pr.get("merged"):
            return "merged"
        sha = pr["head"]["sha"]
        base_ref = pr["base"]["ref"]
        sc, cmp_ = api("GET", "/repos/%s/compare/%s...%s" % (repo, base_ref, sha), token=token)
        behind = cmp_.get("behind_by") if sc == 200 else -1
        behind = behind if isinstance(behind, int) else -1
        if behind > 0:
            continue  # rebase pas encore appliqué (ou main a bougé entre-temps)
        conflicts = pr.get("mergeable") is False and (pr.get("mergeable_state") or "") == "dirty"
        if conflicts:
            create_check(
                repo,
                token,
                sha,
                False,
                "Conflits avec %s — résolveur Hermes lancé" % base_ref,
                "Le rebase automatique a révélé un VRAI conflit : "
                "`hermes-resolver.yml` est lancé pour le résoudre, puis la PR "
                "sera ré-évaluée. Rebase manuel possible en parallèle.",
            )
            set_label(pr, repo, token, LABEL_CONFLIT, True)
            set_label(pr, repo, token, LABEL_OK, False)
            declencher_resolveur(pr, dry=dry)
            return "conflict"
        set_label(pr, repo, token, LABEL_OK, True)
        create_check(
            repo,
            token,
            sha,
            True,
            "PR à jour — merge par le gate",
            "Fraîche au moment du merge (settle après rebase serveur).",
        )
        if dry:
            return "dry"
        m = try_merge(repo, token, pr, required)
        print("[gate] #%d : settle merge → %s" % (num, m), flush=True)
        if m != "waiting":
            return m
    return "timeout"


def try_merge(repo, token, pr, required):
    """Merge la PR (méthode rebase) si fraîche et checks requis verts."""
    num = pr["number"]
    sha = pr["head"]["sha"]
    # Re-vérification de fraîcheur au dernier moment (défense anti-course)
    sc, cmp_ = api("GET", "/repos/%s/compare/%s...%s" % (repo, pr["base"]["ref"], sha), token=token)
    if sc == 200 and cmp_.get("behind_by") not in (0, None):
        return "behind"
    if not required_checks_green(repo, token, pr, required):
        return "waiting"
    sc, resp = api(
        "PUT",
        "/repos/%s/pulls/%d/merge" % (repo, num),
        token=token,
        body={"sha": sha, "merge_method": "rebase"},
    )
    if sc == 200:
        return "merged"
    if sc == 409:
        return "behind"
    print("[gate] #%d merge %s : %s" % (num, sc, resp), flush=True)
    return "error"


def handle_pr(pr, repo, token, required, dry=False, auto_rebase=True):
    num = pr["number"]
    sha = pr["head"]["sha"]
    base_ref = pr["base"]["ref"]
    mergeable = pr.get("mergeable")  # None = calcul en cours
    mergeable_state = pr.get("mergeable_state") or ""

    sc, cmp_ = api("GET", "/repos/%s/compare/%s...%s" % (repo, base_ref, sha), token=token)
    behind = cmp_.get("behind_by") if sc == 200 else -1
    behind = behind if isinstance(behind, int) else -1
    fresh = behind == 0
    conflicts = mergeable is False and mergeable_state == "dirty"

    if conflicts:
        create_check(
            repo,
            token,
            sha,
            False,
            "Conflits avec %s — résolveur Hermes lancé" % base_ref,
            "Le gate a détecté un VRAI conflit : `hermes-resolver.yml` rebase la "
            "branche, résout les conflits (InferHub puis repli déterministe), "
            "valide avec la toolchain du dépôt et pousse. Rebase manuel possible "
            "en parallèle, le label `hermes/intervention-humaine` informe un humain.",
        )
        set_label(pr, repo, token, LABEL_CONFLIT, True)
        set_label(pr, repo, token, LABEL_OK, False)
        declencher_resolveur(pr, dry=dry)
        return "conflict"
    set_label(pr, repo, token, LABEL_CONFLIT, False)

    if not fresh:
        if not auto_rebase:
            # Rebase serveur désactivé (variable HERMES_AUTO_REBASE=0).
            # Raison : le push de rebase est fait avec le GITHUB_TOKEN, qui ne
            # déclenche AUCUN workflow → le nouveau head n'a plus de check runs
            # et la PR resterait ouverte. Dans ce mode, le rafraîchissement est
            # fait par un agent avec un jeton classique (la CI repart alors).
            print("[gate] #%d : en retard de %d commit(s) — rebase manuel attendu" % (num, behind))
            return "behind"
        if dry:
            print(
                "[gate] #%d : en retard de %s commit(s) — rebase serveur (dry-run)" % (num, behind)
            )
            return "dry"
        r = refresh_branch(repo, token, pr)
        print("[gate] #%d : en retard de %s → refresh %s" % (num, behind, r), flush=True)
        if r == "refreshed":
            # Le push du rebase est un push de bot : aucun workflow ne se
            # déclenche. Ré-évaluation et merge DANS CE RUN.
            return settle_after_refresh(repo, token, num, required, dry=dry)
        if r == "conflict":
            # Le rebase serveur a été refusé : conflit réel après tout.
            create_check(
                repo,
                token,
                sha,
                False,
                "Conflits avec %s — résolveur Hermes lancé" % base_ref,
                "Le rebase automatique a été refusé : `hermes-resolver.yml` est "
                "lancé pour résoudre le conflit puis pousser la branche.",
            )
            set_label(pr, repo, token, LABEL_CONFLIT, True)
            set_label(pr, repo, token, LABEL_OK, False)
            declencher_resolveur(pr, dry=dry)
        return r

    set_label(pr, repo, token, LABEL_OK, True)
    create_check(
        repo,
        token,
        sha,
        True,
        "PR à jour — merge par le gate",
        "Fraîche au moment du merge ; %s"
        % ("checks requis verts" if required else "aucun check requis configuré"),
    )
    if dry:
        print("[gate] #%d : fraîche — merge possible (dry-run)" % num)
        return "dry"
    m = try_merge(repo, token, pr, required)
    print("[gate] #%d : merge → %s" % (num, m), flush=True)
    return m


def list_open_prs(repo, token):
    prs, page = [], 1
    while True:
        sc, plist = api(
            "GET", "/repos/%s/pulls?state=open&per_page=100&page=%d" % (repo, page), token=token
        )
        if sc != 200 or not isinstance(plist, list):
            break
        prs += plist
        if len(plist) < 100:
            break
        page += 1
    return prs


def main():
    import os

    token = os.environ["GH_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    event_name = os.environ["GITHUB_EVENT_NAME"]
    required = [
        s.strip() for s in os.environ.get("HERMES_REQUIRED_CHECKS", "").split(",") if s.strip()
    ]
    dry = os.environ.get("HERMES_LITE_DRY_RUN") == "1"
    auto_rebase = os.environ.get("HERMES_AUTO_REBASE", "1") == "1"

    for lbl, couleur in (
        (LABEL_OK, "0e8a16"),
        (LABEL_CONFLIT, "d73a4a"),
        (LABEL_ESCALADE, "fbca04"),
    ):
        ensure_label(repo, token, lbl, couleur, "Posé par hermes-lite / le résolveur Hermes")

    with open(os.environ["GITHUB_EVENT_PATH"]) as fh:
        event = json.load(fh)

    if event_name == "workflow_dispatch":
        only = (event.get("inputs") or {}).get("pr_number") or None
        prs = list_open_prs(repo, token)
        if only:
            prs = [p for p in prs if p["number"] == int(only)]
    elif event_name == "pull_request":
        prs = [event["pull_request"]]
    elif event_name == "check_run":
        # Un check vient de finir : ré-évalue les PR dont le head porte ce check.
        sha = event["check_run"]["head_sha"]
        prs = [p for p in list_open_prs(repo, token) if p["head"]["sha"] == sha]
    elif event_name == "workflow_run":
        # La CI vient de terminer : ré-évalue les PR dont le head porte ce SHA.
        # (Un déclencheur par fin de CI, pas un par check : pas de boucle.)
        sha = event["workflow_run"]["head_sha"]
        prs = [p for p in list_open_prs(repo, token) if p["head"]["sha"] == sha]
    else:
        prs = []

    merged = 0
    for pr in prs:
        if pr.get("draft") or pr.get("state") != "open":
            continue
        try:
            r = handle_pr(pr, repo, token, required, dry, auto_rebase=auto_rebase)
            if r == "merged":
                merged += 1
        except Exception as e:
            print("[gate] #%s exception : %r" % (pr.get("number"), e), flush=True)
    print("[gate] terminé : %d PR traitée(s), %d merge(s)" % (len(prs), merged), flush=True)


if __name__ == "__main__":
    main()
