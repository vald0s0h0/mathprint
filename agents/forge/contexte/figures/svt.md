# Figures : kit de la matière

**SVT** (`kn-svt.tex`)

| Macro | Effet |
|---|---|
| `\BrinADN{ATGCCGTA}` | deux brins face à face, le complémentaire calculé (A-T, C-G), une case et une couleur par base, liaisons à 2 ou 3 traits |
| `\BrinADN[simple]{ATGCCGTA}` | un seul brin, cases vides en face à compléter |
| `\Echiquier{A,a}{A,a}` | échiquier de croisement ; gamètes en en-têtes (colonnes puis lignes), génotypes calculés, allèle dominant en premier ; un ou deux gènes (`AB,Ab,aB,ab`) |
| `\Legende{(point)}{(numéro)}{1}` | flèche fine vers le point d'un schéma, terminée par un numéro cerclé |

```latex
\begin{tikzpicture}
\BrinADN[simple]{ATGCCGTA}
\begin{scope}[yshift=-3.6cm]
\Echiquier{A,a}{A,a}
\end{scope}
\end{tikzpicture}
```

Légendes d'un schéma :

```latex
\begin{tikzpicture}
\draw[trait,aplat] (0,0) ellipse (1.5 and 0.9);
\draw[trait,aplat] (0.3,0.1) circle (0.4);
\Legende{(0.3,0.1)}{(2.2,1.2)}{1}
\Legende{(-1.2,-0.3)}{(-2,-1.2)}{2}
\end{tikzpicture}
```
