# Figures : kit de la matière

**Physique-chimie** (`kn-pc.tex`) : charge circuitikz (symboles européens), chemfig et mhchem 4. Un kit se déclenche dès qu'une figure contient `to[` (composant de circuit), `\ce`, `\chemfig` ou une macro ci-dessous. Fils et composants sont à 0,6 pt comme `trait`.

| Macro | Effet |
|---|---|
| `\Becher{(x,y)}{0.6}{étiquette}` | bécher posé par son point bas au centre ; niveau de liquide de 0 à 1 ; étiquette facultative (`{}`), placée dessous |
| `\Erlenmeyer`, `\Eprouvette`, `\TubeEssai`, `\Ballon` | mêmes arguments (point, niveau, étiquette) |
| `\Burette{(x,y)}{0.7}{étiquette}` | burette verticale graduée à robinet ; son point bas est la pointe |
| `\Support{(x,y)}{6}` | potence : socle de 2,2 cm au point donné, tige de la hauteur donnée (en cm) |
| `\Force{(point)}{angle}{longueur}{$\vec{P}$}` | vecteur-force `repD`, angle en degrés, longueur en cm, étiquette au bout |
| `\PlanIncline{30}{5}` | plan incliné (angle en bas à gauche, longueur de la pente en cm) |
| `\Solide{(x,y)}{étiquette}` | bloc de 1,1 × 0,7 cm posé par le milieu de sa base ; étiquette vide `{}` si une force part de son centre ; sur une pente, dans `\begin{scope}[rotate=30]` |
| `\draw (0,0) to[battery1,l=$E$] (0,2) to[R,l=$R$] (3,2) to[lamp] (3,0) -- (0,0);` | circuit (composants de circuitikz) |
| `\ce{H2O}`, `\chemfig{H-C(-[2]H)(-[6]H)-O-H}` | formules et schémas de molécules. La flèche `->` de `\ce` est dessinée avec des tirets qui se recouvrent : le contrôle géométrique la signale à tort |

```latex
\begin{tikzpicture}
\Support{(2.2,0)}{6.6}
\draw[trait] (0,5.4) -- (2.2,5.4);
\Burette{(0,2.6)}{0.7}{}
\Becher{(0,0)}{0.4}{\ce{HO-}}
\end{tikzpicture}
```

Circuit :

```latex
\begin{tikzpicture}
\draw (0,0) to[battery1,l=$E$] (0,2) to[R,l=$R$] (3,2) to[lamp] (3,0) -- (0,0);
\end{tikzpicture}
```

Mécanique :

```latex
\begin{tikzpicture}
\PlanIncline{30}{5}
\begin{scope}[rotate=30]
\Solide{(3,0)}{}
\Force{(3,0.7)}{90}{1.3}{$\vec{R}$}
\end{scope}
\Force{(2.42,1.8)}{-90}{1.1}{$\vec{P}$}
\end{tikzpicture}
```
