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

- Repérage de la copie après coup (§13.7) : en attente de confirmation (question 43).
- Corrigé par jeu de valeurs pour le professeur.
- File de nuit automatique (décision : à la main d'abord).
- Rédaction longue (reportée), enseignement technologique.
- Découpage de `forge.py` et contrats Pydantic : écarté pour l'instant, les nouveautés vivent dans des modules à part (`familles.py`, `plan.py`, `confiance.py`, `contexte.py`).
