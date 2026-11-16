# Figures : charte commune

`{"kind": "tikz", "spec": {"tikz": "\\begin{tikzpicture}...\\end{tikzpicture}"}}`. **Toute nouvelle figure s'écrit en TikZ**, jamais de découpe d'une page de manuel. Les anciens moteurs (`geo`, `chart`, `template`, `svg`) ne servent plus qu'aux exercices déjà en banque ; `check` les refuse dans une nouvelle carte.

Tu n'écris que le dessin. La charte `backend/app/assets/tikz/kn-figures.tex` fixe tout le reste : épaisseurs, couleurs, police, codages, cotes. Les figures de tous les exercices se ressemblent donc, quel que soit l'auteur. tikz, tkz-euclide, tikz-3dplot et pgfplots sont chargés ; les bibliothèques `calc`, `angles`, `quotes`, `arrows.meta`, `intersections`, `positioning`, `decorations` et `fit` aussi.

### Échelle

- **Une unité vaut 1 cm imprimé.** Une figure fait au plus 93 × 90 mm ; vise 40 à 75 mm de large.
- Si la figure est trop grande, réduis `x` et `y` ensemble, par palier : `[x=0.8cm,y=0.8cm]`, puis `0.6cm`, puis `0.5cm`. Un quadrillage de 5 mm : `[x=0.5cm,y=0.5cm]`. Garde les vraies valeurs dans les coordonnées : un côté de 6 cm vaut 6 unités.
- Perspective cavalière (collège) : `[x={(0.85cm,0)},y={(0.36cm,0.25cm)},z={(0,0.85cm)}]`, fuyante à 45° environ et réduite de moitié. Vraie perspective (lycée) : `\tdplotsetmaincoords{70}{110}` puis `[tdplot_main_coords]`.
- La taille des textes ne bouge pas : 10 pt pour les noms, 9,5 pt pour les cotes. Ne réduis pas une figure pour la caser.

### Styles

| Style | Pour |
|---|---|
| `trait` | tout trait de la figure |
| `fort` | ce qu'il faut voir : section, segment cherché |
| `cache` | arête cachée, tirets |
| `constr` | trait de construction, hauteur auxiliaire, pointillés de lecture |
| `aplat`, `aplatB` | fond très clair d'une surface, purement décoratif |
| `section` | section d'un solide (aplat et contour bleus) ; l'énoncé dit « la section colorée », jamais « grisée » |
| `sectionbord` | bords visibles d'une section, quand le fond est tracé à part (`\fill[section,draw=none]`) |
| `grille` | quadrillage : `\draw[grille] (0,0) grid[step=1] (12,8);` |
| `axe` | axe fléché |
| `origine` | figure de départ à repérer : aplat bleu soutenu |
| `figA` à `figE` | figures à distinguer entre elles : bleu, vert, violet, orange, framboise (couleurs `repA` à `repE`) |
| `faceH`, `faceV`, `faceD` | faces du dessus, de face et de côté d'un solide |
| `cylL`, `cylT` | surface et dessus d'un cylindre ou d'un cône |
| `etiq` | nom d'un point ou d'une figure (gras, bleu) |
| `petit` | graduation, légende secondaire |
| `cote` | valeur d'une longueur ou d'un angle (gras, orange) |
| `boite` | expression dans une case : programme de calcul, plateau de balance |

L'ordre de dessin est imposé par la charte, quel que soit l'ordre de ton code : les fonds (`aplat`, faces, `origine`, `figA`…) tout en bas, puis les codages d'angle, puis les traits et les textes. Un angle ne recouvre donc jamais un trait.

Le quadrillage `grille` est gris-bleu et bien visible : il sert à lire une figure, jamais à tracer. L'élève ne trace que dans une géométrie à tracer (`geo_construction`), dont le quadrillage et la case restent saumon : la CV les efface pour lire le tracé.

Interdit dans une figure : `line width`, `thin`, `thick`, `scale=`, `font=`, `\fontsize`, `\definecolor`, `\tikzset`, `\def`, `\newcommand`, `\input` et toute couleur hors palette. Pour une valeur calculée : `\pgfmathsetmacro{\r}{2}`.

### Couleurs

- Palette : `ink` (traits), `nomc` (noms), `cotec` (cotes), `accent`, `fillA`, `fillB`, `angfill`, `gridc`, `repA` à `repE`, `white`. Mélanges permis : `repA!40`, `fillA!50`.
- **Les sujets sont imprimés en couleur.** Une couleur sert à repérer, jamais à décorer :
  - la figure d'origine en `origine` ;
  - ses images ou les figures candidates en `figA` à `figE`, une couleur par figure ;
  - une courbe ou un tracé : `draw=repA` ; plusieurs courbes, une couleur chacune ;
  - une section en `section`.
- Ne donne jamais une réponse par la couleur : quatre images candidates ont quatre couleurs différentes, pas la bonne en vert.

### Macros communes

Les macros propres à une matière sont dans son kit (`contexte/figures/<matière>.md`).

| Macro | Effet |
|---|---|
| `\Pt{(2,1)}{A}{above right}` | point marqué d'une croix, et son nom |
| `\Nom{(A)}{A}{below left}` | nom seul : sommet d'un polygone, numéro d'une figure |
| `\PtA{(A)}{A}{215}`, `\NomA{(A)}{A}{215}` | même chose, nom posé à 3,2 mm dans la direction donnée en degrés : la plus dégagée, à l'opposé des traits qui partent du point |
| `\AngDroit{A}{B}{C}` | angle droit en B (points définis par `\coordinate`) |
| `\AngMark{A}{B}{C}{37°}{1}` | angle en B avec 1 ou 2 arcs et sa valeur ; `{}` sans valeur ; `0` arc pour la valeur seule |
| `\Cote{(A)}{(B)}{6 cm}{4mm}` | cote fléchée parallèle à [AB], décalée de 4 mm à gauche du sens A→B (`-4mm` : à droite), avec lignes de rappel |
| `\CoteV{(5,0)}{(5,3)}{3 m}` | cote verticale, valeur à droite |
| `\Marque{(A)}{(B)}{2}` | codage de longueurs égales (1 à 3 traits) |

Place chaque cote **hors de la figure**, du côté libre ; une valeur ne chevauche jamais un trait. Les nombres s'écrivent à la française (`2,4`, `37°`) ; une expression en `$…$` prend la police de la figure.

`check` compile chaque figure et renvoie l'erreur avec son numéro de ligne (« tikz : ligne 4 : Undefined control sequence »). Il refuse aussi une figure trop grande ou un réglage hors charte. Regarde la carte rendue : aucun nom ni aucune valeur ne doit chevaucher un trait.

## Kits par matière

Un kit est chargé **seulement** si ta figure emploie une de ses macros (la clé et le temps de compilation des autres figures ne changent pas). Les palettes, styles et polices sont ceux de la charte ; les interdits de `check` valent pour ton dessin, pas pour les kits. Les dimensions des macros de verrerie et de mécanique sont en cm réels : garde l'unité de la figure à 1 cm.

**Supports imprimés** (`kn-supports.tex`)

| Macro | Effet |
|---|---|
| `\Calculatrice{haut}{bas}` | écran de calculatrice (4,6 cm), police à chasse fixe, ligne du bas à droite : `2,9999999`, `1.5E-4` |
| `\Ticket{titre}{Riz 5 kg/3 500,Huile 1 L/1 200}{4 700 F}` | ticket de caisse de 4,2 cm : une ligne `libellé/prix` par article (séparées par des virgules, pas de virgule dans un libellé), TOTAL en bas |
| `\Copie[Awa]{3x + 2 = 11\\3x = 9}` | feuille d'élève de 6 cm, lignes de cahier, « Copie de Awa » ; texte manuscrit violet foncé, qui n'est ni le noir de l'imprimé ni le bleu de l'élève. Écris les calculs en texte simple : un `$…$` reprend la police mathématique |
| `\Bulle{texte}` | bulle de message à pointe, 4,4 cm de texte au plus ; se pose en `(0,0)` |
| `\Mascotte[x,y]{kiro}{doute}` | personnage de la planche, 14 mm de haut ; refusé tant que la planche n'est pas fournie ou si le personnage ou la pose n'y figure pas |

```latex
\begin{tikzpicture}
\Ticket{Marché de Cocody}{Riz 5 kg/3 500,Huile 1 L/1 200,Sucre 2 kg/1 600}{6 300 F}
\begin{scope}[yshift=-5.1cm]
\Calculatrice{6 300 ÷ 3 =}{2 100}
\end{scope}
\end{tikzpicture}
```
