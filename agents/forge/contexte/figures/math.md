# Figures : kit des mathématiques

Ce qui ne sert qu'aux figures de mathématiques. La charte commune (`commun.md`) reste valable : échelle, styles, couleurs, macros communes, supports imprimés.

## Macros

| Macro | Effet |
|---|---|
| `\DroiteGraduee{-5}{7}` | droite graduée d'entiers ; points avec `\Pt{(2,0)}{A}{above}` |
| `\Balance{$5x+4$}{$34$}` | balance de Roberval ; `\Balance[gauche]{…}{…}` si le plateau gauche est plus lourd, `[droite]` sinon |

## Solides et patrons

Une figure qui se replie ou se mesure est géométriquement juste. Patron de cône : secteur d'angle 360° × r / g, disque tangent à l'arc en son milieu. Patron de cylindre : longueur du rectangle égale à 2πr, disques tangents aux grands côtés. Dans un commentaire `%`, note le calcul qui fixe ces dimensions.

## Exemples

Triangle coté et codé :

```latex
\begin{tikzpicture}[x=0.85cm,y=0.85cm]
\coordinate (A) at (2.7,4.2); \coordinate (B) at (0,0); \coordinate (C) at (7,0);
\coordinate (M) at (1.62,2.52); \coordinate (N) at (4.43,2.52);
\filldraw[trait,aplat] (A)--(B)--(C)--cycle;
\draw[trait] (M)--(N);
\Nom{(A)}{A}{above} \Nom{(B)}{B}{below left} \Nom{(C)}{C}{below right}
\Nom{(M)}{M}{left} \Nom{(N)}{N}{right}
\Cote{(A)}{(M)}{2}{-3mm} \Cote{(A)}{(N)}{2,4}{3mm} \Cote{(B)}{(C)}{7}{-5mm}
\end{tikzpicture}
```

Figures à repérer sur quadrillage :

```latex
\begin{tikzpicture}[x=0.45cm,y=0.45cm]
\draw[grille] (0,0) grid[step=1] (15,9);
\filldraw[origine] (6,6)--(9,6)--(9,7)--(7,7)--(7,8)--(6,8)--cycle;
\filldraw[figA] (6,4)--(6,1)--(7,1)--(7,3)--(8,3)--(8,4)--cycle;
\filldraw[figB] (4,6)--(1,6)--(1,7)--(3,7)--(3,8)--(4,8)--cycle;
\Pt{(5,5)}{O}{above right}
\Nom{(8.2,7.6)}{F}{} \Nom{(7.6,1.8)}{1}{} \Nom{(2.2,7.6)}{2}{}
\end{tikzpicture}
```

Pavé en perspective cavalière avec section :

```latex
\begin{tikzpicture}[x={(0.85cm,0)},y={(0.36cm,0.25cm)},z={(0,0.85cm)}]
\foreach \n/\c in {A/{0,0,0},B/{6,0,0},C/{6,4,0},D/{0,4,0},E/{0,0,3},F/{6,0,3},
  G/{6,4,3},H/{0,4,3},I/{2.5,0,0},J/{2.5,4,0},K/{2.5,4,3},L/{2.5,0,3}} \coordinate (\n) at (\c);
\fill[faceH] (E)--(F)--(G)--(H)--cycle; \fill[faceV] (A)--(B)--(F)--(E)--cycle;
\fill[faceD] (B)--(C)--(G)--(F)--cycle;
\fill[section,draw=none] (I)--(J)--(K)--(L)--cycle;
\draw[cache] (A)--(D)--(C) (D)--(H) (I)--(J)--(K);
\draw[trait] (A)--(B)--(C)--(G)--(H)--(E)--cycle (E)--(F)--(G) (F)--(B);
\draw[sectionbord] (I)--(L)--(K);
\Nom{(A)}{A}{below left} \Nom{(B)}{B}{below} \Nom{(L)}{L}{above left}
\Cote{(A)}{(B)}{6 cm}{-6mm} \Cote{(E)}{(A)}{3 cm}{-6mm}
\end{tikzpicture}
```

Courbe dans un repère (pgfplots) :

```latex
\begin{tikzpicture}
\begin{axis}[kn repere,width=6cm,height=5cm,xmin=-1,xmax=5,ymin=-1,ymax=6,xtick={0,...,4},ytick={0,...,5}]
\addplot[draw=repA,domain=-1:5,samples=50] {0.5*x^2-x+1};
\end{axis}
\end{tikzpicture}
```
