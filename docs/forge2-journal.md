# Forge 2 : journal d'implémentation

Plan : [idees/forge-refonte-plan.md](idees/forge-refonte-plan.md). Branche `forge2`, un commit par lot.

| Lot | Contenu | État |
|---|---|---|
| préparatoire | plan, décisions, commande `forge contexte`, lignes non mélangées sans déclaration | fait |
| 0 | musée des défauts (`agents/forge/musee/`, rejoué par `backend/tests/test_musee.py`) | fait |
| 1 | contrôle géométrique : figure (`services/figlint.py`, branché sur `check`) et carte entière (branché sur `render`, bloque `persist`) ; calibré sans faux positif sur les 64 figures et les 653 cartes de la banque | fait |
| 3 | familles (`agents/forge/familles.py`) : jeux de valeurs, calculs remplis par SymPy, deux dispositions écrites par l'auteur, jumelles contrôlées, rendues et écrites en banque en un groupe ; `author_order` : aucun mélange par script à l'impression ; le lecteur relit les deux dispositions | fait |
| 4 | doctrine de maths et archétypes (`matieres/math/doctrine.md`), archétype par emplacement, vérifié par `check` ; étalons par archétype : à écrire | fait, sauf étalons |
| 4 bis | contexte sans aucun texte de manuel (Indigo gardé pour le seul contrôle anti-copie, et pour le français seulement) ; bloc « idées » donné par `suivant` ; `prepare` refuse sans fiche | fait |
| 5 | programme ivoirien : séries du lycée (`--serie`), profil `programmes/ci.md`, progression de l'année dans le référentiel du run, volumes par formule avec amorce et `--suite`, bases du calendrier (`run.py calendrier`) et de l'épuisement (`run.py epuisement`) | fait |
| 6 | indice de confiance par format (`agents/forge/confiance.py`, `run.py confiance`), pastille « À relire » | fait |
| 6 bis | anti-triche côté application : une carte active par famille (statut `twin` pour les autres), jumelle servie selon la place de l'élève, liste de classe mise à jour par le dernier lot scanné, jumelles regroupées dans l'onglet Exercices | fait ; reste le corrigé par jeu de valeurs |
| 7 | kits de figures par matière (`kn-supports`, `kn-pc`, `kn-svt`, `kn-hg`), planche `docs/figures-kits.png` ; `\Mascotte` en attente de la planche des personnages | fait, à valider sur planche |
| 8 | commande `vague` (`backend/scripts/vague.py`, notice `docs/vague.md`) : correction et appréciation sur abonnement | fait ; jamais lancée sur un vrai moteur |
| matières | doctrine, charte et erreurs types de pc, svt, français, anglais, hg ; contrôle des fiches (`run.py fiche`) | fait, doctrines à relire ; étalons à écrire après relecture |
| suivi | groupes de besoin, prêt pour l'examen, bilan pour la famille, revanche, objectif par devoir | fait ; rendu non vu dans le navigateur |

## Ce qui n'a pas encore été éprouvé

- Aucun run réel de bout en bout avec la nouvelle procédure (fiche par `forge contexte`, puis familles par `forge`).
- La commande `vague` n'a jamais appelé un vrai moteur ; `codex` n'est pas installé ; l'identifiant du modèle Luna est à renseigner (`MATHPRINT_VAGUE_MODEL_LUNA`).
- Les vues du suivi des élèves et le regroupement des jumelles n'ont pas été regardés dans le navigateur.
- Rien n'est déployé : tout est sur la branche `forge2`, non poussée.

## Non construit

- Repérage de la copie après coup (§13.7) : abandonné (décision du 2026-11-15).
- Corrigé par jeu de valeurs pour le professeur.
- File de nuit automatique (décision : à la main d'abord).
- Rédaction longue (reportée), enseignement technologique.
- Découpage de `forge.py` et contrats Pydantic : écarté pour l'instant, les nouveautés vivent dans des modules à part (`familles.py`, `plan.py`, `confiance.py`, `contexte.py`).

## Décisions du 2026-11-15

- Repérage de la copie après coup : non construit, abandonné.
- Étalons des autres matières : écrits au moment d'ouvrir chaque matière, pas avant.
- Ligne « Ton objectif » : code gardé tel quel (imprimée sur les sujets individuels quand la place le permet) ; une appréciation complète par Haiku, sans doute au verso, la remplacera plus tard.
- Découpage de `forge.py` et contrats Pydantic : abandonnés.
- `vague --nas` : le Mac relève la file de correction du NAS par SSH.
- Physique-chimie : l'unité n'est imprimée que si elle n'est pas évaluée ; sinon, case à part.
- Livraison de `forge2` : demandée le 2026-11-15.

## Forge à niveaux (2026-11-16, branche `forge-niveaux`)

Objectif : des exercices justes et de grande qualité, publiés avec peu de relecture humaine. Chaque règle vit au niveau le plus général où elle est vraie ; on relit en amont (matière, leçon), on fait confiance en aval (exercices) grâce à des contrôles et à deux lecteurs indépendants. Schéma : artefact « Payload de la forge v2 ».

| Lot | Contenu | État |
|---|---|---|
| 0 | `run.py bilan` (tokens des lecteurs et du rédacteur, lus dans les transcriptions des sous-agents), `run.py qualite` (refus par cause et par format) | fait |
| 1 | `FORGE.md` → `CONSIGNE-EXERCICES.md` (procédure seule) + `contexte/exercice.md` (le bon exercice, universel) ; `schema.md` découpé en `contexte/formats/{commun,courants,geo_construction}.md` et `contexte/figures/{commun,pc,svt,hg}.md` ; doctrine + charte → `contexte/matieres/<m>.md` ; `programmes/` → `contexte/pays/` ; étalons → `contexte/exemples/math.json` ; `goldfish_prompts/` → `prompts/` | fait |
| 1 | `CONTEXTE.md` → `CONSIGNE-LECON.md`, `contexte.py` → `lecon.py`, commande `forge lecon` (`run.py lecon`) ; leçons dans `data/forge/lecons/<matière>/` ; idées en blocs ```` ```yaml idees ```` ; l'agent ne lit que l'en-tête, la section de sa compétence et la fin (extrait stable hors du run) ; `geo_construction.md` lu seulement si la grille a des tracés | fait |
| 1 | règles de maths recopiées dans les fiches remontées dans `contexte/matieres/math.md` ; `run.py lecon` refuse une leçon qui recopie un niveau au-dessus | fait |
| 2 | faits `F…` obligatoires dans la leçon, cités par chaque famille (`faits`), contrôlés par `check` ; vérificateur de faits (`run.py lecon --verifier`, Opus, seuls les faits modifiés rejugés ; `--accepter` pour l'humain) ; `prepare` refuse une leçon absente, à l'ancien format ou non vérifiée | fait |
| 2 | relecteur pédagogique (`run.py relecture`, Opus, grille juste / utile / distracteurs / niveau / programme) : bloquée → réécrite une fois → écartée, `persist` la laisse de côté | fait |
| 3 | cause obligatoire sur un pouce baissé (onglet Exercices) ; confiance par format et par format × archétype, remise à zéro selon la cause ; `ecartes` affiche la cause et le niveau à corriger | fait |
| 4 | autres matières | non ouvert (décision du 2026-11-16 : maths seulement) |

Décisions du 2026-11-16 : publication par clic groupé (inchangé) ; Opus pour tout (pas d'abonnement Fable) ; carte bloquée réécrite une fois puis écartée ; les exercices déjà publiés ne citent pas de faits ; 3e C entièrement publiée, à remplacer plus tard par la nouvelle forge ; rien n'est déployé.

Reste : les 7 anciennes fiches (`data/forge/lecons/math/`) sont à réécrire avec `forge lecon --refaire` avant tout nouveau run sur ces leçons ; aucun run réel de bout en bout avec relecture pédagogique n'a encore tourné.
