# Forge 2 : journal d'implémentation

Plan : [idees/forge-refonte-plan.md](idees/forge-refonte-plan.md). Branche `forge2`, un commit par lot.

| Lot | Contenu | État |
|---|---|---|
| préparatoire | plan, décisions, commande `forge contexte`, lignes non mélangées sans déclaration | fait |
| 0 | musée des défauts (`agents/forge/musee/`, rejoué par `backend/tests/test_musee.py`) | fait |
| 1 | contrôle géométrique : figure (`services/figlint.py`, branché sur `check`) et carte entière (branché sur `render`, bloque `persist`) ; calibré sans faux positif sur les 64 figures et les 653 cartes de la banque | fait |
| 3 | familles (`agents/forge/familles.py`) : jeux de valeurs, calculs remplis par SymPy, deux dispositions écrites par l'auteur, jumelles contrôlées, rendues et écrites en banque en un groupe ; `author_order` : aucun mélange par script à l'impression ; le lecteur relit les deux dispositions | fait |
