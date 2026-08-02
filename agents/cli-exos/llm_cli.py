"""Provider LLM par le CLI Claude Code (abonnement Anthropic) — JAMAIS l'API.

Remplace, pour la pipeline `cli-exos`, le rôle de `app.services.providers.claude_json`
/ `gemini_json` : on ne fait PAS un POST vers `api.anthropic.com`, on invoque le
binaire `claude` en mode non-interactif (`claude -p`). L'authentification se fait
alors par l'**abonnement** de l'utilisateur connecté (Claude Pro/Max), et non par
une clé d'API facturée.

Garde-fou « jamais l'API » : `ANTHROPIC_API_KEY` (et `ANTHROPIC_AUTH_TOKEN`) sont
RETIRÉS de l'environnement passé à `claude`. Si l'une d'elles traînait dans le
shell, le CLI l'utiliserait et facturerait l'API — on l'empêche ici.

INVOCATION ALLÉGÉE (coût/latence) : `claude -p` est un AGENT, pas une simple
complétion. Par défaut il précharge, à CHAQUE appel, son gros prompt système
d'agent, les schémas de TOUS les outils, les serveurs MCP, les fichiers CLAUDE.md
/ mémoire et les settings — mesuré à ~21 000 tokens de contexte par appel, pour
une tâche qui n'en a AUCUN besoin (on veut juste transformer du JSON). On coupe
donc tout ce contexte inutile :
  • --system-prompt <le nôtre>   : remplace le prompt d'agent par NOTRE contrat ;
  • --tools ""                   : aucun outil (pas de schémas, pas de tours d'outil) ;
  • --strict-mcp-config + config vide : aucun serveur MCP chargé ;
  • --setting-sources ""         : pas de hooks/settings projet ;
  • --no-session-persistence     : rien écrit sur disque.
Effet mesuré : ~21 000 → ~4 600 tokens de contexte par appel (−78 %). NB : notre
contrat (envoyé identique à chaque appel) est mis en cache côté serveur, donc bon
marché en lecture dès le 2ᵉ appel. (Le mode `--bare` couperait aussi CLAUDE.md,
mais il FORCE l'auth par clé d'API — incompatible avec l'abonnement : on ne l'utilise pas.)

Ce module ne dépend QUE de la bibliothèque standard : il est testable seul (avec
un faux binaire `claude`), sans importer le backend.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("cli-exos.llm")


def _resolve_bin() -> str:
    """Chemin du binaire `claude`. Priorité : CLAUDE_BIN explicite → `claude` sur
    le PATH → emplacements d'INSTALL NATIVE connus (le PATH d'un shell non
    interactif n'inclut pas toujours ~/.local/bin). En dernier ressort « claude »,
    ce qui laisse `is_available()` répondre False proprement."""
    explicit = os.environ.get("CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "claude",
                 Path.home() / ".claude" / "local" / "claude"):
        if cand.is_file():
            return str(cand)
    return "claude"


# binaire du CLI ; surchargeable via CLAUDE_BIN=/chemin/vers/claude.
CLAUDE_BIN = _resolve_bin()

# délai total par appel (un lot de génération/relecture peut être long)
DEFAULT_TIMEOUT_S = int(os.environ.get("CLI_EXOS_TIMEOUT_S", "900"))

# premier objet {...} d'un texte (même tolérance que app.services.providers)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

_TAIL = ("Réponds UNIQUEMENT par l'objet JSON demandé — aucun texte avant ou "
         "après, aucun bloc de code Markdown, aucune explication.")


class ClaudeCliError(RuntimeError):
    """Échec d'un appel au CLI Claude (binaire absent, code non nul, JSON hors schéma)."""


def is_available() -> bool:
    """Vrai si le binaire `claude` est trouvable (PATH ou CLAUDE_BIN)."""
    return shutil.which(CLAUDE_BIN) is not None or os.path.isfile(CLAUDE_BIN)


def _clean_env() -> dict:
    """Environnement pour `claude` SANS les identifiants d'API Anthropic : force
    l'usage de l'abonnement (cf. en-tête du module)."""
    env = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(var, None)
    return env


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJ_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def claude_cli_json(system: str, payload: dict, *, model: str,
                    timeout: int = DEFAULT_TIMEOUT_S,
                    correlation_id: str | None = None) -> dict:
    """Un appel `claude -p` en sortie JSON. `system` = instructions (prompt),
    `payload` = données (sérialisées en JSON dans le message). `model` = alias
    (`sonnet`/`opus`/`haiku`) ou id complet. Retourne l'objet JSON produit par le
    modèle. Lève `ClaudeCliError` si le binaire manque, échoue, ou ne renvoie pas
    de JSON exploitable après une tentative corrective."""
    if not is_available():
        raise ClaudeCliError(
            f"binaire « {CLAUDE_BIN} » introuvable. Installe/connecte le CLI "
            "Claude Code (abonnement) ou définis CLAUDE_BIN=/chemin/vers/claude.")

    # NOTRE contrat passe en --system-prompt (remplace le prompt d'agent) ; le
    # message utilisateur ne porte QUE les données. Flags « allégés » : cf. en-tête.
    user_msg = f"# Données (JSON)\n{json.dumps(payload, ensure_ascii=False)}\n\n{_TAIL}"
    env = _clean_env()
    cmd = [CLAUDE_BIN, "-p", "--output-format", "json", "--model", model,
           "--system-prompt", system, "--tools", "",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--no-session-persistence"]

    full = user_msg
    last_reason = ""
    for attempt in range(2):
        try:
            proc = subprocess.run(cmd, input=full, capture_output=True, text=True,
                                  env=env, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise ClaudeCliError(
                f"claude CLI ({model}) : pas de réponse en {timeout}s "
                f"(corr={correlation_id})") from e
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-800:]
            raise ClaudeCliError(
                f"claude CLI ({model}) a échoué (code {proc.returncode}) : {tail}")

        # enveloppe --output-format json : {type:result, is_error, result, total_cost_usd, ...}
        try:
            env_obj = json.loads(proc.stdout)
        except json.JSONDecodeError:
            env_obj = None
        if isinstance(env_obj, dict) and "result" in env_obj:
            if env_obj.get("is_error"):
                raise ClaudeCliError(
                    f"claude CLI ({model}) a signalé une erreur : "
                    f"{str(env_obj.get('result'))[:800]}")
            logger.info("cli-exos/%s : cost≈%.4f$ dur=%sms (corr=%s)", model,
                        float(env_obj.get("total_cost_usd") or 0.0),
                        env_obj.get("duration_ms"), correlation_id)
            text = str(env_obj.get("result") or "")
        else:
            # pas d'enveloppe (version de CLI différente) : on prend stdout brut
            text = proc.stdout

        parsed = _extract_json(text)
        if parsed is not None:
            return parsed
        last_reason = (text or "").strip()[:200]
        # 2e chance : on rappelle la contrainte JSON à la fin du message
        full = user_msg + ("\n\nATTENTION : ta réponse précédente n'était pas un objet "
                           "JSON valide. Recommence en ne produisant QUE l'objet JSON.")

    raise ClaudeCliError(
        f"claude CLI ({model}) : réponse hors schéma JSON après 2 essais "
        f"(corr={correlation_id}) — début reçu : {last_reason!r}")
