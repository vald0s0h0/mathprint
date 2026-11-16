# Figures : kit de la matière

**Histoire-géographie** (`kn-hg.tex`)

| Macro | Effet |
|---|---|
| `\Frise{1789}{1815}{5}` | axe gradué de 9 cm à l'échelle, une graduation tous les 5 ans (choisis une fin multiple du pas, depuis le début) ; à écrire en premier |
| `\Evenement{1789}{texte}` | repère sur l'axe et texte au-dessus ; `\Evenement[1]{…}{…}` éloigne le texte (niveau 1, 2…) pour deux dates voisines |
| `\Periode{1789}{1799}{texte}` | bande colorée sous l'axe, une couleur par appel ; `\Periode[1]{…}{…}{…}` l'empile sous la précédente |

```latex
\begin{tikzpicture}
\Frise{1789}{1819}{5}
\Evenement{1789}{Prise de la Bastille}
\Evenement[1]{1799}{Coup d'État de Bonaparte}
\Evenement{1804}{Sacre de Napoléon}
\Evenement{1815}{Waterloo}
\Periode{1789}{1799}{Révolution}
\Periode{1799}{1815}{Consulat et Empire}
\end{tikzpicture}
```
