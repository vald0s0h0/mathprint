"""Pipeline Gemini : création d'exercices ANCRÉS DANS LE MANUEL de la classe —
2e source d'exercices de l'app, à côté de l'extraction Sésamath
(services.sesamaths).

Les deux pipelines lisent donc le MÊME manuel, mais n'en font pas la même
chose : Sésamaths ADAPTE les exercices imprimés (pool fini — ce qui est dans
la Série est tout ce qu'on peut en tirer), Gemini s'en sert de RÉFÉRENCE pour
en créer d'autres (pool infini, c'est nous qui décidons quand nous arrêter).

Le contexte manuel n'est pas un raffinement : sans lui (première version, 17/07)
le modèle ne disposait que du libellé de la compétence et produisait des
exercices mal ciblés, au mauvais niveau, hors programme — un libellé de
référentiel ne dit ni jusqu'où va le programme, ni quelle taille de nombres est
attendue dans la classe. Les pages du manuel le disent. D'où : pas de contexte
manuel = pas de création (GeminiGenerationError), jamais un repli silencieux
sur l'invention libre qui a justement causé le problème.

Ce contexte est la phase 1 de Sésamaths — l'OCR Mistral de la Série, via
sesamaths.ensure_series_ocr — SANS son adaptateur Claude : on veut le texte du
manuel, pas des exercices au contrat app. L'extraction et son cache sont
partagés avec l'autre pipeline : une seule facture OCR par Série, quelle que
soit celle qui passe la première.

Reste propre à la création (le pool est infini) :
  - pas de cache LLM : rappeler le modèle avec le même prompt doit produire des
    exercices NEUFS, c'est le but même de la boucle ;
  - appels par lots de `settings.gemini_batch_size` (5), répétés jusqu'à ce que
    la banque atteigne `settings.gemini_bank_target` (30) — 6 appels si tout
    passe, davantage si la validation en recale (nombre d'appels non borné a
    priori, seulement plafonné par `settings.gemini_max_batches`) ;
  - PUIS un appel DÉDIÉ (prompt _FILLER_INTRO) crée `settings.gemini_filler_target`
    (5) petites cartes de REMPLISSAGE (kind="filler", cible totale 35) : un seul
    calcul / un QCM court, exclues de la sélection normale et réservées aux trous
    de bas de page (services.generation) ; best-effort, un échec n'empêche pas
    le sujet ;
  - anti-doublon à deux niveaux : les énoncés déjà produits sont renvoyés au
    modèle à chaque lot (« n'en produis aucun équivalent »), ET tout candidat
    dupliqué est rejeté déterministiquement (exercise_gen._dedup_key) — la
    consigne seule ne suffit jamais.

Chaque lot demande une composition imposée (3 QCM, 1 réponse écrite, 1 exercice
long) : c'est le mélange qui remplit une page proprement, sans page entière de
QCM ni page entière de rédaction. Elle est demandée au modèle, jamais imposée
après coup — un lot qui s'en écarte reste exploitable.

Validation : aucune logique propre. Chaque candidat repasse par
exercise_gen._validate_exercise (le contrat de format lui est décrit par
exercise_gen.format_contract, une seule définition partagée avec Sésamaths),
plus une règle spécifique à cette pipeline : matching/manual_drawing sont
refusés (correction non automatisable — on invente l'exercice, on peut donc
toujours en choisir un qui se corrige tout seul).

Difficulté : figée à 3/5 (comme Sésamaths depuis le 17/07). Le modèle n'évalue
pas le niveau et cette pipeline ne produit donc QUE du niveau 3 ; un appel pour
un autre niveau ne génère rien et laisse exercise_gen.bank_rows_near_level se
rabattre sur le 3.

Barème : le modèle renvoie en revanche un "effort_points" par exercice — ce
n'est PAS la difficulté déguisée (cf. point précédent), mais ce que l'exercice
VAUT d'après l'effort qu'il exige (temps de réflexion). Les deux grandeurs sont
volontairement distinctes : on ne note pas le niveau de l'élève, on récompense
le travail fourni. Contrat et repères dans exercise_gen._BAREME_RULES,
conversion en note dans services/scoring.py.

Géométrie : hors périmètre pour l'instant (refus explicite, message clair).
"""
import logging
import time

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Competency, CompetencyFramework, GeneratedExercise
from . import exercise_gen, providers, sesamaths

logger = logging.getLogger(__name__)

PROMPT_VERSION = "gemini-exgen-3-layout"
SOURCE = "gemini"
# Seul niveau produit : la difficulté n'est pas évaluée (cf. en-tête).
GENERATED_LEVEL = 3

# Exercices COURTS de remplissage : petites cartes (un seul calcul, un énoncé
# très court, un QCM) créées par un appel Gemini DÉDIÉ (prompt _FILLER_INTRO),
# stockées avec kind=FILLER_KIND et EXCLUES de la sélection normale — elles ne
# servent qu'à combler les trous de bas de page (services.generation). Formats
# volontairement bornés aux petites cartes : ni tableau, ni rédaction, ni
# sous-questions.
FILLER_KIND = "filler"
FILLER_RESPONSE_TYPES = {"short_text", "qcm_single", "qcm_multiple"}

# Formats refusés dans cette pipeline : leur correction n'est jamais
# automatique (matching = détection de trait manuscrit, manual_drawing =
# correction humaine). L'adaptateur Sésamaths, lui, en a besoin — il subit le
# format du manuel et n'a pas le droit d'omettre un exercice ; ici on invente,
# donc on n'a aucune excuse pour produire un exercice non corrigeable.
FORBIDDEN_RESPONSE_TYPES = {"matching", "manual_drawing"}

# Budgets de sortie essayés dans l'ordre : le budget Gemini couvre AUSSI les
# tokens de réflexion (2.5 Flash pense par défaut), un lot de 5 exercices dont
# un table_fill dense peut donc dépasser 16000.
_TOKEN_BUDGETS = (16000, 32000, 48000)


class GeminiGenerationError(RuntimeError):
    """La création Gemini n'a pas pu produire d'exercices pour cette
    compétence (domaine hors périmètre, modèle en échec…). Remontée telle
    quelle à l'appelant, avec un message actionnable — jamais de repli
    silencieux sur une autre source."""


# ================================================================ prompt

_INTRO = (
    "Tu es professeur agrégé de mathématiques en collège français.\n\n"
    "Crée §COUNT§ exercices pour une classe de §GRADE§ sur la compétence "
    "« §COMPETENCY§ », chapitre « §CHAPTER§ », domaine « §DOMAIN§ ».\n\n"

    "# Périmètre exact de la compétence visée\n"
    "Voici toutes les compétences du domaine « §DOMAIN§ », chapitre par "
    "chapitre. Elles ne sont PAS à traiter : elles servent à situer "
    "précisément la compétence visée (marquée « ⇦ CIBLE ») parmi ses voisines, "
    "pour que tes exercices tombent exactement dessus et pas à côté. Tout ce "
    "qui relève d'une AUTRE compétence de cette liste est hors sujet. Les "
    "§COUNT§ exercices portent TOUS sur la SEULE compétence cible.\n\n"
    "§COMPETENCY_TREE§\n\n"

    "# LE MANUEL DE LA CLASSE SUR CETTE COMPÉTENCE — TA RÉFÉRENCE\n"
    "Voici le texte des pages du manuel de §GRADE§ (collection Sésamath) "
    "consacrées EXACTEMENT à la compétence cible, tel que lu par OCR : ce sont "
    "les exercices réellement donnés à ces élèves. C'est ta référence de "
    "vérité pour les trois choses que tu ne dois surtout pas deviner :\n"
    "- le PROGRAMME : ce qui est au programme sur cette compétence est ce qui "
    "est traité dans ces pages, rien de plus — pas de notion vue plus tard "
    "dans l'année ni dans une classe supérieure, même si elle a un rapport ;\n"
    "- le NIVEAU : la taille et la nature des nombres, la longueur des "
    "énoncés, le nombre d'étapes attendues, le vocabulaire employé ;\n"
    "- l'OBJECTIF D'APPRENTISSAGE : ce que ces exercices font réellement "
    "travailler à l'élève.\n"
    "Tu as le droit de REPRENDRE tel quel un énoncé de ces pages, et le droit "
    "d'en INVENTER de nouveaux — mais uniquement en t'INSPIRANT de ceux-ci : "
    "même type de tâche, même exigence, nombres et contextes renouvelés. Un "
    "exercice qui ne serait ni repris ni inspiré de ces pages est hors sujet, "
    "même s'il colle au libellé de la compétence.\n"
    "Ce texte est BRUT et vient d'un OCR de pages imprimées : la mise en page "
    "peut être désordonnée, des fragments s'y mêlent (numéros de page, titres "
    "de rubrique, « À RETENIR », légendes), et les réponses n'y figurent PAS "
    "(une suite de points « ... » est une case que l'élève devait remplir, "
    "jamais un texte à recopier). Ignore les fragments, et RÉSOUS toi-même "
    "tout énoncé que tu reprends. Les exercices de ces pages qui s'appuient "
    "sur une FIGURE sont inutilisables ici (cf. règle géométrie) : ne les "
    "reprends pas.\n\n"
    "§MANUAL_CONTEXT§\n\n"

    "# Ce que deviennent tes exercices (enjeu de la plateforme)\n"
    "Ils sont imprimés sur des copies papier distribuées aux élèves ; les "
    "copies, remplies à la main, sont ensuite scannées et corrigées "
    "AUTOMATIQUEMENT : les réponses écrites sont lues par OCR dans les cases "
    "de réponse imprimées, les QCM par vision par ordinateur (détection des "
    "cases cochées). Il en découle des règles non négociables :\n"
    "- la réponse attendue doit être COURTE et n'avoir qu'UNE SEULE écriture "
    "naturelle : un OCR lit ce qui est écrit, il ne devine aucune intention ;\n"
    "- jamais de question dont la réponse serait une phrase libre, un « ça "
    "dépend », ou plusieurs écritures également acceptables ;\n"
    "- l'élève n'écrit RIEN en dehors des cases prévues par le format choisi : "
    "tout ce que tu attends de lui doit tenir dans \"answer\".\n\n"

    "# Composition OBLIGATOIRE du lot de §COUNT§ exercices\n"
    "C'est ce mélange qui remplit une page d'entraînement proprement :\n"
    "- 3 QCM (\"qcm_single\" ou \"qcm_multiple\" — utilise les deux, pas trois "
    "fois le même) ;\n"
    "- 1 exercice à réponse écrite (\"short_text\" ou \"multi_blank\") ;\n"
    "- 1 exercice long, AU CHOIX : un problème à raisonnement rédigé en "
    "plusieurs étapes (\"multiline_text\"), OU plusieurs sous-questions a./b./"
    "c. rattachées à un MÊME énoncé (\"multi_blank\"), OU un tableau à remplir "
    "(\"table_fill\").\n"
    "Un même exercice peut comporter plusieurs cases de réponse, 10 au MAXIMUM "
    "(cellules de tableau et {{blank}} confondus). Chaque case a son propre "
    "type attendu : des sous-questions a./b./c. d'un même énoncé peuvent donc "
    "attendre des réponses de natures différentes (un entier, une fraction, "
    "une expression…) — c'est particulièrement utile pour les problèmes.\n\n"

    "# Contraintes de rédaction\n"
    "- respecter le programme français de §GRADE§ ;\n"
    "- viser une difficulté MOYENNE, pour un élève médian de §GRADE§ ; "
    "n'évalue et ne renvoie AUCUN niveau de difficulté, ce n'est pas demandé ;\n"
    "- donner à chaque exercice son barème \"effort_points\" (cf. BARÈME dans "
    "le contrat de format) : ce que l'exercice coûte en TEMPS DE RÉFLEXION, "
    "jamais le niveau de l'élève ni la difficulté de l'exercice — c'est une "
    "grandeur différente de celle du point précédent, ne les confonds pas ;\n"
    "- employer un français simple et naturel, des phrases courtes ;\n"
    "- ne poser aucune question ambiguë : une seule lecture possible de "
    "l'énoncé, une seule réponse juste ;\n"
    "- choisir des nombres qui donnent des résultats raisonnables (calculables "
    "de tête ou posés, pas de décimale à rallonge, pas de fraction monstrueuse) ;\n"
    "- VÉRIFIER indépendamment chaque résultat AVANT de répondre : recalcule "
    "chaque réponse attendue une seconde fois en repartant de l'énoncé, et "
    "n'écris \"answer\" qu'une fois les deux calculs concordants ;\n"
    "- rédiger \"correction\" pour l'ÉLÈVE, pas pour le professeur : ce n'est "
    "PAS le corrigé de l'exercice (ne donne NI le résultat, NI la solution, NI "
    "une résolution pas-à-pas). C'est un GUIDE d'auto-correction, TRÈS COURT "
    "(2 à 3 lignes maximum), qui ne s'affiche que si l'élève s'est trompé et "
    "l'aide à SE corriger seul : rappelle la règle ou la propriété du cours à "
    "mobiliser, et surtout LE PIÈGE PRÉCIS de CET exercice — l'erreur classique "
    "qui le guette ici (erreur de signe, priorité opératoire oubliée, mauvaise "
    "unité, oubli de convertir, confusion de règle, retenue…). L'explication "
    "doit être FORTEMENT liée à cet exercice-ci (renvoie aux nombres/à la "
    "situation de l'énoncé), jamais un conseil générique. Respecte la même mise "
    "en page que l'énoncé : formules en LaTeX $...$, et un saut de ligne \\n "
    "quand tu passes du rappel de règle au piège (deux idées = deux lignes) ;\n"
    "- les distracteurs de tes QCM sont exactement ces erreurs classiques "
    "d'élèves, jamais des nombres pris au hasard : un distracteur doit être le "
    "résultat d'une faute plausible ;\n"
    "- l'énoncé ne révèle JAMAIS le corrigé : ni la réponse, ni un exemple "
    "résolu qui donnerait la méthode complète. \"statement\" et \"correction\" "
    "sont deux mondes séparés ;\n"
    "- éviter le jargon inutile ;\n"
    "- pour les problèmes, s'inspirer de la vie courante (bricolage, gestion "
    "d'argent, courses, cuisine, sport, loisirs, transports) : contexte "
    "crédible pour un collégien, prénoms variés ;\n"
    "- NE PAS traiter la géométrie (figure, tracé, lecture de figure, "
    "construction) : cette pipeline ne la gère pas encore ;\n"
    "- à l'intérieur du lot, ne réutilise jamais deux fois le même contexte ni "
    "les mêmes nombres.\n\n"

    "# Exercices déjà en banque pour cette compétence\n"
    "Le message utilisateur te donne \"already_created\" : les énoncés DÉJÀ "
    "créés lors des appels précédents. Aucun de tes exercices ne doit leur "
    "être équivalent — ni le même calcul avec d'autres nombres, ni le même "
    "contexte avec d'autres prénoms. Change de type de nombres, de situation, "
    "d'angle d'attaque.\n\n"
)


# Prompt du 2e appel, DÉDIÉ aux exercices courts de remplissage. Même ancrage
# (compétence, manuel, contraintes OCR) que _INTRO, mais une composition
# radicalement différente : QUE des petites cartes, pour boucher les trous de
# bas de page sans jamais y mettre une grande carte qui déborderait.
_FILLER_INTRO = (
    "Tu es professeur agrégé de mathématiques en collège français.\n\n"
    "Crée §COUNT§ exercices TRÈS COURTS pour une classe de §GRADE§ sur la "
    "compétence « §COMPETENCY§ », chapitre « §CHAPTER§ », domaine « §DOMAIN§ ».\n\n"

    "# À quoi ils servent — LIS-LE BIEN\n"
    "Ce sont des exercices de REMPLISSAGE : de toutes petites cartes qui "
    "viennent combler les trous de bas de page laissés par les grands "
    "exercices. Ils doivent donc être VISUELLEMENT PETITS et se traiter en "
    "quelques secondes. Un seul geste mental par exercice : un calcul isolé, "
    "une conversion, une question de cours à trancher. JAMAIS un problème, "
    "jamais plusieurs étapes, jamais de sous-questions.\n\n"

    "# Périmètre exact de la compétence visée\n"
    "Les compétences voisines ci-dessous NE sont PAS à traiter : elles situent "
    "la compétence visée (« ⇦ CIBLE ») pour que tes exercices tombent "
    "exactement dessus. Les §COUNT§ exercices portent TOUS sur la SEULE "
    "compétence cible.\n"
    "§COMPETENCY_TREE§\n\n"

    "# LE MANUEL DE LA CLASSE — TA RÉFÉRENCE\n"
    "Texte OCR des pages du manuel de §GRADE§ (Sésamath) sur cette compétence : "
    "ta référence pour le PROGRAMME (ne va pas au-delà de ce qui y est traité) "
    "et le NIVEAU (taille des nombres, vocabulaire). Le texte est brut (OCR) : "
    "ignore les fragments de mise en page, une suite de « ... » est une case à "
    "remplir (pas un texte à recopier), et RÉSOUS toi-même ce que tu reprends. "
    "N'utilise aucun exercice s'appuyant sur une figure.\n"
    "§MANUAL_CONTEXT§\n\n"

    "# Correction automatique (contraintes non négociables)\n"
    "Imprimés sur copie, remplis à la main, scannés et corrigés "
    "AUTOMATIQUEMENT : la réponse attendue doit être COURTE et n'avoir qu'UNE "
    "SEULE écriture naturelle ; jamais de phrase libre ni de réponse "
    "ambiguë.\n\n"

    "# Composition OBLIGATOIRE — QUE des petites cartes\n"
    "- utilise UNIQUEMENT ces formats : \"short_text\" (une seule case réponse, "
    "un calcul ou une valeur), \"qcm_single\" ou \"qcm_multiple\" ;\n"
    "- INTERDITS ici : \"multiline_text\", \"table_fill\", \"multi_blank\", "
    "\"matching\", \"manual_drawing\", et toute géométrie ;\n"
    "- UNE seule case de réponse par exercice (pour un short_text) ; pas de "
    "sous-questions a./b./c. ;\n"
    "- énoncé d'UNE phrase courte, idéalement une seule ligne ; pour un calcul, "
    "l'expression seule suffit (ex. « Calcule $7 \\times 8$. ») ;\n"
    "- QCM : 3 ou 4 choix courts, distracteurs = erreurs classiques d'élèves "
    "(résultat d'une faute plausible), jamais des nombres au hasard.\n\n"

    "# Contraintes de rédaction\n"
    "- respecter le programme français de §GRADE§, difficulté MOYENNE ; "
    "n'évalue ni ne renvoie aucun niveau de difficulté ;\n"
    "- donner à chaque exercice son barème \"effort_points\" (cf. BARÈME) : ces "
    "cartes sont courtes, donc leur effort est FAIBLE (le plus souvent 0,5) ;\n"
    "- des nombres qui donnent des résultats simples (calcul de tête ou posé) ;\n"
    "- VÉRIFIER chaque résultat deux fois avant de répondre ;\n"
    "- rédiger \"correction\" pour l'ÉLÈVE (pas le professeur) : PAS le résultat "
    "ni la solution, mais un GUIDE d'auto-correction TRÈS COURT (1 à 2 lignes), "
    "affiché seulement en cas d'erreur — rappelle la règle et LE PIÈGE PRÉCIS "
    "de CET exercice. Formules en LaTeX $...$ ;\n"
    "- l'énoncé ne révèle JAMAIS la réponse ;\n"
    "- à l'intérieur du lot, ne réutilise jamais deux fois le même calcul ni le "
    "même contexte.\n\n"

    "# Exercices déjà en banque\n"
    "\"already_created\" liste les énoncés DÉJÀ créés : aucun des tiens ne doit "
    "leur être équivalent (ni le même calcul avec d'autres nombres).\n\n"
)


def _competency_tree(db: Session, competency: Competency) -> str:
    """Toutes les compétences du domaine, groupées par chapitre, la cible
    marquée — donne au modèle les FRONTIÈRES de la compétence visée (ce qui
    est traité juste à côté, et qu'il ne doit donc pas traiter ici)."""
    rows = (db.query(Competency)
            .filter(Competency.framework_id == competency.framework_id,
                    Competency.domain_code == competency.domain_code)
            .order_by(Competency.order_index).all())
    lines: list[str] = []
    current_chapter = None
    for c in rows:
        if c.chapter_code != current_chapter:
            current_chapter = c.chapter_code
            lines.append(f"{c.chapter_code} {c.chapter_name}")
        mark = "  ⇦ CIBLE" if c.id == competency.id else ""
        lines.append(f"  - {c.short_id or c.code} {c.label}{mark}")
    return "\n".join(lines)


def _competency_name(c: Competency) -> str:
    return f"{c.short_id} {c.label}".strip() if c.short_id else c.label


# Blocs OCR sans valeur de contexte : ni énoncé, ni consigne, ni donnée — que
# du bruit de mise en page. "image" est écarté pour une autre raison : son
# "content" est vide (la figure vit dans son bbox, cf. sesamaths._to_candidate),
# la citer ne ferait qu'annoncer au modèle des exercices qu'il ne peut pas voir
# — et qu'on lui interdit de toute façon de reprendre (géométrie).
_CONTEXT_SKIP_BLOCKS = {"header", "footer", "signature", "references", "code",
                        "aside_text", "image"}


def _manual_context(blocks: list[dict]) -> str:
    """Rend les blocs OCR d'une Série en texte lisible pour le prompt : un bloc
    par ligne, taggé de son type (le modèle doit savoir qu'un « table » est un
    tableau imprimé et un « title » un numéro d'exercice), regroupé par page du
    manuel. Aucune interprétation ici — le regroupement en exercices, c'est le
    travail de l'ADAPTATEUR Sésamaths, et il n'a pas lieu d'être pour un simple
    contexte de programme."""
    lines: list[str] = []
    current_page = None
    for b in blocks:
        if b.get("type") in _CONTEXT_SKIP_BLOCKS:
            continue
        content = str(b.get("content") or "").strip()
        if not content:
            continue
        if b.get("page") != current_page:
            current_page = b.get("page")
            lines.append(f"\n--- page {current_page} du manuel ---")
        lines.append(f"({b.get('type')}) {content}")
    return "\n".join(lines).strip()


def _fill_placeholders(template: str, db: Session, competency: Competency,
                       grade: str, count: int, manual_context: str) -> str:
    # .replace (et non .format) : le prompt contient des accolades littérales
    # (schéma JSON, marqueur {{blank}})
    return (template
            .replace("§COUNT§", str(count))
            .replace("§GRADE§", grade)
            .replace("§COMPETENCY§", _competency_name(competency))
            .replace("§CHAPTER§", f"{competency.chapter_code} {competency.chapter_name}".strip())
            .replace("§DOMAIN§", f"{competency.domain_code} {competency.domain_name}".strip())
            .replace("§COMPETENCY_TREE§", _competency_tree(db, competency))
            # en dernier : le texte du manuel est le SEUL fragment non maîtrisé
            # du prompt (OCR d'un PDF). S'il contenait « §COMPETENCY_TREE§ » ou
            # tout autre marqueur, un .replace ultérieur l'interpréterait.
            .replace("§MANUAL_CONTEXT§", manual_context))


def _system_prompt(db: Session, competency: Competency, grade: str, count: int,
                   manual_context: str) -> str:
    intro = _fill_placeholders(_INTRO, db, competency, grade, count, manual_context)
    return intro + exercise_gen.format_contract(exercise_gen._GEMINI_FORMAT_INTRO)


def _system_prompt_filler(db: Session, competency: Competency, grade: str,
                          count: int, manual_context: str) -> str:
    intro = _fill_placeholders(_FILLER_INTRO, db, competency, grade, count, manual_context)
    return intro + exercise_gen.format_contract(exercise_gen._GEMINI_FORMAT_INTRO)


# ================================================================ candidats

def _reject_reason(item: dict, filler: bool = False) -> str | None:
    """Refus propres à cette pipeline, EN PLUS de _validate_exercise (jamais à
    la place). None = rien à redire ici. `filler` : lot d'exercices courts —
    les formats sont bornés aux petites cartes (cf. FILLER_RESPONSE_TYPES)."""
    rtype = item.get("response_type")
    if rtype in FORBIDDEN_RESPONSE_TYPES:
        return f"format non corrigeable automatiquement : {rtype!r}"
    if filler and rtype not in FILLER_RESPONSE_TYPES:
        return f"format trop long pour une carte de remplissage : {rtype!r}"
    if item.get("figure"):
        # Le prompt l'interdit ; s'il en produit une quand même, l'exercice
        # s'appuie dessus (« la figure ci-contre ») — la retirer casserait
        # l'énoncé en silence. On le rejette : le lot suivant en produira un
        # autre, c'est le principe même de la boucle.
        return "figure demandée alors que la pipeline ne traite pas la géométrie"
    return None


def _to_candidate(item: dict, competency: Competency, db: Session,
                  existing_norms: set[str], filler: bool = False) -> dict | None:
    if not isinstance(item, dict):
        return None
    item = dict(item)
    item.pop("difficulty", None)   # niveau non évalué par le LLM : toujours 3
    item.pop("source_blocks", None)  # champ Sésamaths, sans objet ici

    reason = _reject_reason(item, filler=filler)
    if reason is None:
        valid = exercise_gen._validate_exercise(item, competency, db, existing_norms)
        if valid is not None:
            valid["difficulty"] = GENERATED_LEVEL
            valid["kind"] = FILLER_KIND if filler else valid.get("kind", "application")
            return valid
        reason = exercise_gen.diagnose_rejection(item, competency)
    # pourquoi, et pas seulement combien : un « 5 renvoyés, 0 validés » est
    # indiagnosticable (cf. incident extraction Sésamaths A1)
    logger.warning("Gemini : exercice REFUSÉ — %s | énoncé : %.90s", reason,
                   str(item.get("statement", "")).replace("\n", " "))
    return None


def _generate_batch(db: Session, competency: Competency, grade: str, batch: int,
                    already_created: list[str], existing_norms: set[str],
                    manual_context: str, *, count: int | None = None,
                    filler: bool = False) -> list[dict]:
    """Un appel Gemini = un lot d'exercices, dont on ne garde que ceux qui
    passent la validation déterministe. `filler` bascule sur le prompt des
    petites cartes (_FILLER_INTRO) et borne les formats acceptés."""
    count = count if count is not None else settings.gemini_batch_size
    system = (_system_prompt_filler(db, competency, grade, count, manual_context)
              if filler else _system_prompt(db, competency, grade, count, manual_context))
    payload = {"grade_level": grade, "competency_code": competency.code,
               "competency_label": _competency_name(competency),
               "chapter": f"{competency.chapter_code} {competency.chapter_name}".strip(),
               "domain": f"{competency.domain_code} {competency.domain_name}".strip(),
               # copie : l'appelant continue d'alimenter sa liste lot après lot,
               # le payload d'un appel ne doit pas bouger sous ses pieds
               "count": count, "batch": batch, "already_created": list(already_created)}
    correlation_id = f"gemini-{competency.code}-{'f' if filler else 'b'}{batch}"

    data = None
    for budget in _TOKEN_BUDGETS:
        for attempt in range(3):
            try:
                data = providers.gemini_json(db, "exercise_generation", system, payload,
                                             max_tokens=budget, correlation_id=correlation_id)
                break
            except Exception as e:
                if providers.is_rate_limited(e) and attempt < 2:
                    delay = providers.retry_after_s(e, attempt)
                    logger.info("Gemini : 429, nouvel essai dans %.0f s (tentative %s/3)",
                                delay, attempt + 2)
                    time.sleep(delay)
                    continue
                if providers.is_truncated(e) and budget != _TOKEN_BUDGETS[-1]:
                    logger.info("Gemini : réponse tronquée à max_tokens=%s, nouvel essai "
                                "avec un budget plus élevé", budget)
                    break
                raise
        if data is not None:
            break

    items = (data or {}).get("exercises") or []
    cands = [c for c in (_to_candidate(i, competency, db, existing_norms, filler=filler)
                         for i in items) if c is not None]
    logger.info("Gemini : lot %s%s pour %s — %s exercice(s) validé(s) sur %s renvoyé(s)",
                "filler-" if filler else "", batch, competency.code, len(cands), len(items))
    return cands


# ================================================================ banque

def _bank_rows(db: Session, competency: Competency, level: int) -> list[GeneratedExercise]:
    """Exercices CLASSIQUES actifs (kind ≠ filler) : ceux qu'un sujet enchaîne
    normalement. Les cartes de remplissage en sont EXCLUES — elles ne servent
    qu'à boucher les trous, via filler_rows/services.generation."""
    return (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id == competency.id,
                    GeneratedExercise.difficulty_level == level,
                    GeneratedExercise.status == "active",
                    GeneratedExercise.source == SOURCE,
                    GeneratedExercise.kind != FILLER_KIND)
            .all())


def _filler_rows(db: Session, competency: Competency, level: int) -> list[GeneratedExercise]:
    return (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id == competency.id,
                    GeneratedExercise.difficulty_level == level,
                    GeneratedExercise.status == "active",
                    GeneratedExercise.source == SOURCE,
                    GeneratedExercise.kind == FILLER_KIND)
            .all())


def filler_rows(db: Session, competency: Competency, level: int) -> list[GeneratedExercise]:
    """Cartes de remplissage (kind=filler) déjà en banque pour ce niveau. Ne
    déclenche AUCUNE génération : elles sont créées par ensure_bank en même
    temps que les exercices classiques. Vide si la banque n'a pas encore été
    constituée, ou pour une source sans remplissage (Sésamaths)."""
    return _filler_rows(db, competency, level)


def ensure_bank(db: Session, competency: Competency, level: int) -> list[GeneratedExercise]:
    """Garantit `settings.gemini_bank_target` (30) exercices actifs pour la
    compétence, en enchaînant autant d'appels Gemini que nécessaire — banque
    vide : 30 d'un coup (6 lots de 5) ; banque partielle : le complément
    seulement ; banque pleine : rien du tout, sans même lire le manuel.

    Remplir d'un coup, et pas au besoin d'un sujet, est délibéré : les sujets
    suivants puisent dans la banque sans plus rien payer, et le modèle n'a pas
    à recréer à l'aveugle des exercices proches de ceux déjà en stock.

    Pool strictement séparé (source="gemini") : les exercices CRÉÉS d'après le
    manuel ne se mélangent jamais à ceux qui en sont EXTRAITS (source
    "sesamaths"), même si les deux pipelines lisent les mêmes pages."""
    level = max(1, min(5, level))
    rows = _bank_rows(db, competency, level)
    if level != GENERATED_LEVEL:
        # cette pipeline ne produit que du niveau 3 (difficulté non évaluée) :
        # ne RIEN générer ici, bank_rows_near_level se rabattra sur le 3 plutôt
        # que de nous faire ranger des exercices moyens sous une autre étiquette
        return rows
    if competency.domain_code in exercise_gen.GEOMETRY_DOMAINS:
        raise GeminiGenerationError(
            f"Création Gemini indisponible pour {competency.code} : les exercices "
            f"de géométrie ({competency.domain_name}) ne sont pas encore traités "
            "par cette pipeline. Choisissez la source Sésamaths pour cette compétence.")

    # Banque déjà pleine : on ne crée RIEN et on ne lit même pas le manuel. Les
    # exercices en stock serviront les prochains sujets — c'est tout l'intérêt
    # de viser 30 d'un coup plutôt que de rappeler le modèle à chaque sujet.
    target = settings.gemini_bank_target
    if len(rows) >= target:
        return rows

    fw = db.get(CompetencyFramework, competency.framework_id)
    grade = fw.grade_level if fw else ""
    if not grade:
        raise GeminiGenerationError(
            f"Création Gemini impossible pour {competency.code} : le niveau de "
            "classe (référentiel) est introuvable.")

    # Contexte manuel AVANT le premier appel : sans les pages de la Série, le
    # modèle n'a que le libellé de la compétence et produit hors programme (cf.
    # en-tête). Pas de contexte = pas de création, message clair — surtout pas
    # un repli sur l'invention libre. Phase 1 de Sésamaths seule (OCR mis en
    # cache, partagé) : aucun appel à l'adaptateur Claude.
    try:
        blocks = sesamaths.ensure_series_ocr(db, competency)
    except sesamaths.SesamathsExtractionError as e:
        raise GeminiGenerationError(
            f"Création Gemini impossible pour {competency.code} : les pages du "
            f"manuel traitant cette compétence n'ont pas pu être lues, or elles "
            f"sont le contexte qui cale le programme et le niveau des exercices "
            f"créés. Détail : {e}") from e
    manual_context = _manual_context(blocks)
    if not manual_context:
        raise GeminiGenerationError(
            f"Création Gemini impossible pour {competency.code} : l'OCR des pages "
            "du manuel traitant cette compétence n'a renvoyé aucun texte "
            "exploitable — sans ce contexte, les exercices créés seraient hors "
            "programme.")
    logger.info("Gemini : contexte manuel pour %s — %s bloc(s) OCR retenu(s), "
                "%s caractères", competency.code, len(blocks), len(manual_context))

    # Pas de filtre status="active" : un exercice RETIRÉ doit rester
    # définitivement « vu », sinon il est recréé à l'identique au prochain
    # appel (cf. même règle côté Sésamaths). Les cartes de remplissage sont
    # EXCLUES de cette comptabilité : elles ont leur propre pool et leur propre
    # appel (_ensure_filler) ; les compter ici décalerait le n° de lot classique
    # et gonflerait "already_created".
    seen = (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id == competency.id,
                    GeneratedExercise.source == SOURCE,
                    GeneratedExercise.kind != FILLER_KIND).all())
    existing_norms = {
        exercise_gen._dedup_key(ex.statement, ex.expected_json,
                                (ex.grading_json or {}).get("choices"))
        for ex in seen}
    already_created = [ex.statement for ex in seen]

    logger.info("Gemini : banque %s niveau %s — %s variante(s) en stock, cible %s "
                "(lots de %s)", competency.code, level, len(rows), target,
                settings.gemini_batch_size)

    added: list[GeneratedExercise] = []
    next_variant = len(rows)
    error: Exception | None = None

    # « batch » = le n-ième lot demandé pour cette compétence DEPUIS TOUJOURS,
    # pas depuis ce run : on le reprend là où la banque s'est arrêtée. Reparti
    # de 0, un complément de banque partielle (25 en stock, cible 30) redemande
    # au modèle un lot qu'il a déjà produit — il le resert à l'identique, tout
    # est rejeté en doublon, et la boucle s'arrête sur un « aucun exercice
    # exploitable » trompeur. Compté sur `seen` (retirés inclus) : ces lots-là
    # ont bien été demandés et payés.
    first_batch = len(seen) // max(1, settings.gemini_batch_size)

    for batch in range(first_batch, first_batch + settings.gemini_max_batches):
        if len(rows) + len(added) >= target:
            break
        try:
            cands = _generate_batch(db, competency, grade, batch, already_created,
                                    existing_norms, manual_context)
        except Exception as e:
            # Garder ce qui a déjà été produit : un lot en échec ne doit pas
            # jeter les précédents (le message remonte si la banque est vide).
            logger.warning("Gemini : lot %s pour %s en échec : %s", batch,
                           competency.code, e)
            error = e
            break
        if not cands:
            logger.warning("Gemini : lot %s pour %s n'a produit aucun exercice "
                           "exploitable — arrêt", batch, competency.code)
            break
        for cand in cands:
            row = GeneratedExercise(
                competency_id=competency.id, difficulty_level=level, variant=next_variant,
                statement=cand["statement"], correction=cand["correction"],
                response_type=cand["response_type"],
                expected_json=cand["expected"], grading_json=cand["grading"],
                model=settings.gemini_model, prompt_version=PROMPT_VERSION,
                status="active", verifier_model="", verifier_verdict_json={},
                quality_json={}, figure_json=cand.get("figure_json"), source=SOURCE,
                kind=cand.get("kind", "application"))
            db.add(row)
            added.append(row)
            already_created.append(cand["statement"])
            next_variant += 1

    db.flush()
    if not rows and not added:
        raise GeminiGenerationError(
            f"Aucun exercice Gemini n'a pu être créé pour {competency.code} "
            f"niveau {level}" + (f" : {error}" if error else
                                 " : le modèle n'a renvoyé aucun exercice valide."))
    total = len(rows) + len(added)
    if total < target:
        logger.warning("Gemini : banque %s niveau %s incomplète — %s exercice(s) sur "
                       "les %s visés (la page pourrait se répéter)", competency.code,
                       level, total, target)
    logger.info("Gemini : banque %s niveau %s prête : %s variante(s) (%s créée(s) "
                "à l'instant)", competency.code, level, total, len(added))

    # 2e appel DÉDIÉ : petites cartes de remplissage (kind=filler), en plus des
    # exercices classiques. Best-effort — un échec ici ne compromet jamais le
    # sujet (les trous resteront simplement vides, comme avant).
    _ensure_filler(db, competency, grade, level, manual_context)
    return rows + added


def _ensure_filler(db: Session, competency: Competency, grade: str, level: int,
                   manual_context: str) -> list[GeneratedExercise]:
    """Complète la banque de remplissage jusqu'à `settings.gemini_filler_target`
    en UN appel Gemini dédié (prompt court). Best-effort : toute erreur est
    journalisée et avalée — le remplissage est un bonus, pas un prérequis."""
    target = settings.gemini_filler_target
    have = _filler_rows(db, competency, level)
    # UN seul appel de remplissage par compétence×niveau : dès qu'il existe la
    # moindre carte courte, on ne rappelle plus le modèle (le remplissage est un
    # bonus, pas un contenu qu'on paie à re-compléter sujet après sujet).
    if have:
        return have
    # dédup contre TOUT ce qui existe déjà pour la compétence (classiques inclus)
    seen = (db.query(GeneratedExercise)
            .filter(GeneratedExercise.competency_id == competency.id,
                    GeneratedExercise.source == SOURCE).all())
    existing_norms = {
        exercise_gen._dedup_key(ex.statement, ex.expected_json,
                                (ex.grading_json or {}).get("choices"))
        for ex in seen}
    already_created = [ex.statement for ex in seen]
    next_variant = 10_000                       # plage de variantes réservée au filler
    batch = 1000                                # index distinct des lots classiques (0..N)

    try:
        cands = _generate_batch(db, competency, grade, batch, already_created,
                                existing_norms, manual_context,
                                count=target - len(have), filler=True)
    except Exception as e:
        logger.warning("Gemini : remplissage %s en échec (ignoré) : %s",
                       competency.code, e)
        return have
    for cand in cands:
        db.add(GeneratedExercise(
            competency_id=competency.id, difficulty_level=level, variant=next_variant,
            statement=cand["statement"], correction=cand["correction"],
            response_type=cand["response_type"],
            expected_json=cand["expected"], grading_json=cand["grading"],
            model=settings.gemini_model, prompt_version=PROMPT_VERSION,
            status="active", verifier_model="", verifier_verdict_json={},
            quality_json={}, figure_json=cand.get("figure_json"), source=SOURCE,
            kind=FILLER_KIND))
        next_variant += 1
    db.flush()
    result = _filler_rows(db, competency, level)
    logger.info("Gemini : remplissage %s niveau %s — %s carte(s) courte(s) en "
                "stock (%s créée(s) à l'instant)", competency.code, level,
                len(result), len(cands))
    return result
