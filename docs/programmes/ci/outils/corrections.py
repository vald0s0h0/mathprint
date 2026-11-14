# Corrections manuelles des items dont la formule a été cassée par l'extraction.
# Clé : "doc|leçon|habileté|item" (indices de l'extraction). Valeur : texte
# (LaTeX entre $), liste (l'item est scindé) ou None (ligne parasite supprimée).
# Relu contre les pages des PDF officiels.

LIM_INTERP = ("graphiquement chacune des limites suivantes : $\\lim_{x\\to a} f(x) = +\\infty$, "
              "$\\lim_{x\\to a} f(x) = -\\infty$, $\\lim_{x\\to +\\infty} f(x) = b$, "
              "$\\lim_{x\\to -\\infty} f(x) = b$, $\\lim_{x\\to +\\infty} [f(x) - (ax+b)] = 0$, "
              "$\\lim_{x\\to -\\infty} [f(x) - (ax+b)] = 0$")
INEQ_GRAPH = ("graphiquement les inéquations du type $f(x) \\geq 0$, $f(x) > 0$, $f(x) \\leq 0$, $f(x) < 0$, "
              "$f(x) \\geq ax+b$, $f(x) > ax+b$, $f(x) \\leq ax+b$, $f(x) < ax+b$ avec $(a ; b) \\neq (0 ; 0)$, "
              "étant donnée la courbe d’une fonction polynôme ou d’une fonction rationnelle $f$")
LN_LIMITS = ("les résultats suivants : $\\lim_{x\\to 0} \\ln x = -\\infty$ ; $\\lim_{x\\to +\\infty} \\ln x = +\\infty$ ; "
             "$\\lim_{x\\to +\\infty} \\frac{\\ln x}{x} = 0$ ; $\\lim_{x\\to a} \\ln x = \\ln a$ $(a > 0)$")
EXP_LIMITS = ("les résultats suivants : $\\lim_{x\\to -\\infty} e^x = 0$ ; $\\lim_{x\\to +\\infty} e^x = +\\infty$ ; "
              "$\\lim_{x\\to -\\infty} x e^x = 0$ ; $\\lim_{x\\to +\\infty} \\frac{e^x}{x} = +\\infty$")
SUITE_EXPR = ["le terme général $u_n$ d’une suite arithmétique en fonction de $n$",
              "le terme général $u_n$ d’une suite géométrique en fonction de $n$"]
LIM_TC = ("graphiquement, $f$ étant une fonction telle que $\\lim_{x\\to +\\infty} f(x) = \\pm\\infty$ "
          "(resp. $\\lim_{x\\to -\\infty} f(x) = \\pm\\infty$) : $\\lim_{x\\to +\\infty} \\frac{f(x)}{x} = 0$ ; "
          "$\\lim_{x\\to +\\infty} \\frac{f(x)}{x} = +\\infty$ ; $\\lim_{x\\to -\\infty} \\frac{f(x)}{x} = 0$ ; "
          "$\\lim_{x\\to -\\infty} \\frac{f(x)}{x} = +\\infty$")
DERIV_FORMS = ("les dérivées des fonctions de la forme : $x \\mapsto \\sqrt[n]{x}$ $(n \\in \\mathbb{N}^*,\\ x \\in \\mathbb{R}_+^*)$ ; "
               "$x \\mapsto x^r$ $(r \\in \\mathbb{Q},\\ x \\in \\mathbb{R}_+^*)$ ; $x \\mapsto (u(x))^n$ $(n \\in \\mathbb{N}^*)$ ; "
               "$x \\mapsto \\sqrt{u(x)}$")
REPR_FORMS = ("graphiquement des fonctions du type : $x \\mapsto \\sqrt[n]{x}$ $(n \\in \\mathbb{N}^*,\\ x \\in \\mathbb{R}_+^*)$ ; "
              "$x \\mapsto x^r$ $(r \\in \\mathbb{Q},\\ x \\in \\mathbb{R}_+^*)$")
PRIM_TC = ["les primitives des fonctions de référence",
           "les primitives de : $u' + v'$ ; $\\lambda u'$ $(\\lambda \\in \\mathbb{R})$ ; $v' \\times u' \\circ v$ ; "
           "$\\frac{u'}{\\sqrt{u}}$ ; $u' \\cos u$ ; $u' \\sin u$ ; $\\frac{u'}{u^r}$, $r \\in \\mathbb{Q} \\setminus \\{1\\}$ ; "
           "$u' \\times u^m$, $m \\in \\mathbb{Q} \\setminus \\{-1\\}$ ; où $u$ et $v$ sont des fonctions dérivables"]
PRIM_TC_DET = ("les primitives d’une fonction du type : $u' + v'$ ; $\\lambda u'$ $(\\lambda \\in \\mathbb{R})$ ; "
               "$v' \\times u' \\circ v$ ; $\\frac{u'}{\\sqrt{u}}$ ; $u' \\cos u$ ; $u' \\sin u$ ; "
               "$\\frac{u'}{u^r}$, $r \\in \\mathbb{Q} \\setminus \\{1\\}$ ; $u' \\times u^m$, $m \\in \\mathbb{Q} \\setminus \\{-1\\}$ ; "
               "où $u$ et $v$ sont des fonctions dérivables")
SUITES_CONV = ("les propriétés sur la convergence des suites numériques : si $(u_n)$ est une suite convergente vers $a$ "
               "et $f$ une fonction continue en $a$, alors la suite $v_n = f(u_n)$ converge vers $f(a)$ ; soit $f$ "
               "une fonction, $D_f$ son ensemble de définition et $(u_n)$ une suite d’éléments de $D_f$ : si "
               "$\\lim_{n\\to +\\infty} u_n = a$ et $\\lim_{x\\to a} f(x) = l$, alors $\\lim_{n\\to +\\infty} f(u_n) = l$ ; "
               "les propriétés des suites récurrentes définies par une relation du type $u_{n+1} = f(u_n)$")
BARY_LN = ["la propriété de réduction de la somme $a\\overrightarrow{MA} + b\\overrightarrow{MB} = (a+b)\\overrightarrow{MG}$ ; $a+b \\neq 0$",
           "la propriété de réduction de la somme $a\\overrightarrow{MA} + b\\overrightarrow{MB} + c\\overrightarrow{MC} = (a+b+c)\\overrightarrow{MG}$ ; $a+b+c \\neq 0$"]
TRIG_REM = ("les lignes trigonométriques des angles remarquables à partir de celles des angles de mesures : "
            "$0$ ; $\\frac{\\pi}{6}$ ; $\\frac{\\pi}{4}$ ; $\\frac{\\pi}{3}$ et $\\frac{\\pi}{2}$")
TRIG_ASSOC = ("les lignes trigonométriques de : $-x$, $x+\\pi$, $\\pi-x$, $\\frac{\\pi}{2}-x$, $\\frac{\\pi}{2}+x$ "
              "à partir de $x$")
TAN_EQ = "les équations du type : $\\tan(x) = \\tan(a)$, $a \\in \\mathbb{R} \\setminus \\{\\frac{\\pi}{2} + k\\pi,\\ k \\in \\mathbb{Z}\\}$"
LIM_REF = ("la limite à l’infini des fonctions de référence : $x \\mapsto c$ ; $x \\mapsto x$ ; $x \\mapsto x^2$ ; "
           "$x \\mapsto x^3$ ; $x \\mapsto \\sqrt{x}$ ; $x \\mapsto \\frac{1}{x}$")
LIM_XN = "la limite à l’infini des fonctions $x \\mapsto x^n$ ; $x \\mapsto \\frac{1}{x^n}$ $(n \\in \\mathbb{N}^*)$"
LIM_A1 = "la limite à gauche ou à droite en un point $a$ de : $x \\mapsto \\frac{1}{x-a}$"
LIM_AN = "la limite à gauche ou à droite en un point $a$ de : $x \\mapsto \\frac{1}{(x-a)^n}$ $(n \\in \\mathbb{N}^* \\setminus \\{1\\})$"
DERIV_1C = ("des fonctions dérivées en utilisant les opérations sur les fonctions dérivables et les dérivées des fonctions : "
            "$x \\mapsto ax+b$ ; $x \\mapsto \\sqrt{x}$ ; $x \\mapsto x^n$ $(n \\in \\mathbb{N}^*)$ ; $x \\mapsto \\frac{1}{x}$ ; "
            "$x \\mapsto \\frac{1}{x^n}$ $(n \\in \\mathbb{N}^*)$ ; $x \\mapsto \\cos(x)$ ; $x \\mapsto \\sin(x)$ ; $x \\mapsto \\tan(x)$")
DENOMB = ["le cardinal du produit cartésien d’ensembles finis", "$n!$ ; $C_n^p$ ; $A_n^p$ $(p \\leq n)$"]
HOMOG = "la représentation graphique d’une fonction homographique $x \\mapsto \\frac{ax+b}{cx+d}$"
LN_MA_MB_C = "les lignes de niveau de l’application : $M \\mapsto \\frac{MA}{MB}$"
LN_MA_MB_D = "la nature et les éléments caractéristiques des lignes de niveau de l’application : $M \\mapsto \\frac{MA}{MB}$"
LN_SQ_C = "les lignes de niveau de l’application : $M \\mapsto a MA^2 + b MB^2$ ; $a+b \\neq 0$"
LN_SQ_D = "la nature et les éléments caractéristiques des lignes de niveau : $M \\mapsto a MA^2 + b MB^2$ ; $a+b \\neq 0$"
SQRT_EQ = ("une équation du type : $\\sqrt{p(x)} = q(x)$ ou une inéquation de l’un des types suivants : "
           "$\\sqrt{p(x)} \\leq q(x)$ ; $\\sqrt{p(x)} < q(x)$ ; $\\sqrt{p(x)} \\geq q(x)$ ; $\\sqrt{p(x)} > q(x)$ "
           "où $p$ est un polynôme de degré inférieur ou égal à 2 et $q$ un polynôme de degré inférieur ou égal à 1")
SUM_A = "$\\sum \\alpha_i \\overrightarrow{MA_i}$, $\\alpha_i \\in \\mathbb{R}$"
SUM_B = "$\\sum \\alpha_i MA_i^2$, $\\alpha_i \\in \\mathbb{R}$"
LN_TYPES = ("les lignes de niveau des applications de l’un des types : $M \\mapsto \\sum \\alpha_i MA_i^2$, "
            "$\\alpha_i \\in \\mathbb{R}$ ; $M \\mapsto \\frac{MA}{MB}$ ; "
            "$M \\mapsto \\mathrm{Mes}(\\widehat{\\overrightarrow{MA}, \\overrightarrow{MB}})$")
DFDX = ["la définition des dérivées successives d’une fonction",
        "les nouvelles notations des dérivées successives $\\frac{df}{dx}$ ; $\\frac{d^2f}{dx^2}$ ; … ; $\\frac{d^nf}{dx^n}$ $(n \\in \\mathbb{N}^*)$"]

C = {
    # 5e
    "02|0|0|5": "l’égalité $(a \\times b)^n = a^n \\times b^n$ connaissant les entiers naturels $a$, $b$ et $n$",
    "02|2|1|0": ["la règle de calcul de la différence de deux fractions",
                 "l’égalité $\\left(\\frac{a}{b}\\right)^n = \\frac{a^n}{b^n}$ connaissant les entiers naturels $a$, $b$ et $n$"],
    # 2nde A : fonctions élémentaires (le numérateur « 1 » de 1/x glissait dans l'item précédent)
    "05|5|0|4": "l’ensemble de définition des fonctions affines",
    "05|5|0|5": "l’ensemble de définition des fonctions : $x \\mapsto x^2$, $x \\mapsto \\frac{1}{x}$",
    "05|5|0|6": "le sens de variation d’une fonction affine",
    "05|5|0|7": "le sens de variation des fonctions : $x \\mapsto x^2$, $x \\mapsto \\frac{1}{x}$",
    "05|5|1|0": "l’ensemble de définition des fonctions affines",
    "05|5|1|1": "l’ensemble de définition des fonctions : $x \\mapsto x^2$, $x \\mapsto \\frac{1}{x}$",
    "05|5|1|2": "le sens de variation d’une fonction affine",
    "05|5|1|3": "le sens de variation des fonctions : $x \\mapsto x^2$, $x \\mapsto \\frac{1}{x}$",
    # 2nde C
    "06|5|2|0": ("les fonctions élémentaires : $x \\mapsto x^2$ ; $x \\mapsto x^3$ ; $x \\mapsto \\frac{1}{x}$ ; "
                 "$x \\mapsto \\sqrt{x}$ ; $x \\mapsto E(x)$ ; $x \\mapsto |ax+b|$ où $a \\in \\mathbb{R}^*$, $b \\in \\mathbb{R}$"),
    "06|5|6|0": ("les fonctions élémentaires pour étudier les fonctions de l’un des types : $x \\mapsto ax^2$ ; "
                 "$x \\mapsto \\frac{a}{x}$ où $a \\in \\mathbb{R}^*$"),
    # 1re A1 / A2 : dérivées
    "07|3|0|1": ("les formules des dérivées de chacune des fonctions : $x \\mapsto k$, $k \\in \\mathbb{R}$ ; $x \\mapsto x$ ; "
                 "$x \\mapsto ax+b$, $a \\in \\mathbb{R}$ et $b \\in \\mathbb{R}$ ; $x \\mapsto x^2$ ; $x \\mapsto x^3$ ; "
                 "$x \\mapsto \\frac{1}{x}$ ; $x \\mapsto \\frac{a}{x}$, $a \\in \\mathbb{R}$"),
    "07|3|0|2": "la définition de la fonction dérivée d’une fonction",
    "08|3|0|1": ("les formules des dérivées de chacune des fonctions : $x \\mapsto k$ ; $x \\mapsto x$ ; $x \\mapsto ax+b$ ; "
                 "$x \\mapsto x^2$ ; $x \\mapsto x^3$ ; $x \\mapsto \\frac{1}{x}$ ; $x \\mapsto \\frac{a}{x}$"),
    "08|3|0|2": "la définition de la fonction dérivée d’une fonction",
    # 1re C (09) et 1re D (10)
    "09|0|5|3": SQRT_EQ, "10|0|5|3": SQRT_EQ,
    "09|4|0|4": LIM_REF, "10|4|0|4": LIM_REF,
    "09|4|0|5": LIM_XN, "10|4|0|5": LIM_XN,
    "09|4|0|6": LIM_A1, "10|4|0|6": LIM_A1,
    "09|4|0|7": LIM_AN, "10|4|0|7": LIM_AN,
    "09|5|2|4": DERIV_1C, "10|5|2|4": DERIV_1C,
    "09|6|4|1": HOMOG, "10|6|4|1": HOMOG,
    "09|9|3|1": DENOMB, "10|9|3|1": DENOMB,
    "09|9|3|2": None, "10|9|3|2": None,
    "09|12|0|11": BARY_LN[0], "09|12|0|12": BARY_LN[1],
    "10|11|0|9": BARY_LN[0], "10|11|0|10": BARY_LN[1],
    "09|12|6|4": LN_SQ_C, "09|12|6|5": LN_MA_MB_C,
    "09|12|9|4": LN_SQ_D, "09|12|9|5": LN_MA_MB_D,
    "09|13|1|3": TRIG_REM, "10|12|1|3": TRIG_REM,
    "09|13|1|4": TRIG_ASSOC, "10|12|1|4": TRIG_ASSOC,
    "09|13|5|2": TAN_EQ, "10|12|5|2": TAN_EQ,
    # Tle A1 (11) / A2 (12)
    "11|1|0|1": "la limite d’une fonction rationnelle en un point où elle est définie",
    "11|1|0|2": ["la limite à gauche en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$",
                 "la limite à droite en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$"],
    "11|1|1|2": "la limite d’une fonction rationnelle en un point où elle n’est pas définie",
    "11|1|1|3": ["la limite à gauche en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$",
                 "la limite à droite en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$"],
    "11|1|2|0": LIM_INTERP, "12|1|2|0": LIM_INTERP,
    "11|1|8|0": INEQ_GRAPH, "12|1|8|0": INEQ_GRAPH,
    "12|1|0|1": "la limite d’une fonction rationnelle en un point où elle est définie",
    "12|1|0|2": ["la limite à gauche en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$",
                 "la limite à droite en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$"],
    "12|1|1|2": "la limite d’une fonction rationnelle en un point où elle n’est pas définie",
    "12|1|1|3": ["la limite à gauche en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$",
                 "la limite à droite en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$"],
    "11|2|0|1": ("les primitives de chacune des fonctions de référence : $x \\mapsto a$ $(a \\in \\mathbb{R})$ ; "
                 "$x \\mapsto x$ ; $x \\mapsto x^m$, $m \\in \\mathbb{N}$ ; $x \\mapsto \\frac{1}{x^m}$, "
                 "$m \\in \\mathbb{N} \\setminus \\{1\\}$"),
    "11|2|0|2": ("les primitives des fonctions du type : $au$, $a \\in \\mathbb{R}$ ; $au + bv$, $(a, b) \\in \\mathbb{R}^2$ ; "
                 "$u'u^m$, $m \\in \\mathbb{N}$ ; $\\frac{u'}{u^m}$, $m \\in \\mathbb{N} \\setminus \\{1\\}$"),
    "11|2|2|1": ("l’aire d’une partie du plan limitée par : la courbe représentative d’une fonction, l’axe des abscisses "
                 "et les droites d’équations $x = a$ et $x = b$ ; les courbes représentatives de deux fonctions et les "
                 "droites d’équations $x = a$ et $x = b$"),
    "11|2|2|2": None,
    "11|3|0|9": [LN_LIMITS,
                 "les primitives des fonctions du type $x \\mapsto \\frac{1}{x}$ ; $x \\mapsto \\frac{a}{cx+d}$ ; "
                 "$x \\mapsto \\frac{u'(x)}{u(x)}$"],
    "12|2|0|9": LN_LIMITS,
    "11|4|0|7": EXP_LIMITS, "12|3|0|7": EXP_LIMITS,
    "11|4|0|8": ("les primitives des fonctions de chacun des types : $x \\mapsto e^x$ ; $x \\mapsto e^{ax+b}$ ; "
                 "$x \\mapsto u'(x)e^{u(x)}$"),
    "11|4|4|0": "des équations faisant intervenir la fonction exponentielle népérienne",
    "12|3|4|0": "des équations faisant intervenir la fonction exponentielle népérienne",
    "11|5|4|0": SUITE_EXPR, "12|4|4|0": SUITE_EXPR,
    "11|5|5|1": None, "12|4|5|1": None,
    # Tle C (13) / Tle D (14)
    "13|1|2|1": ("une racine $n$-ième d’un nombre positif ($\\sqrt[n]{x}$ ou $x^{\\frac{1}{n}}$) ; "
                 "une puissance d’exposant rationnel ($x^{\\frac{p}{q}}$)"),
    "14|1|2|1": ("une racine $n$-ième d’un nombre positif ($\\sqrt[n]{x}$ ou $x^{\\frac{1}{n}}$) ; "
                 "une puissance d’exposant rationnel ($x^{\\frac{p}{q}}$)"),
    "13|1|5|0": LIM_TC, "14|1|5|0": LIM_TC,
    "13|2|0|1": DFDX, "14|2|0|1": DFDX,
    "13|2|0|2": None, "14|2|0|2": None,
    "13|2|3|2": "le nombre dérivé de la fonction $f^{-1}$ en un point $y_0$",
    "14|2|3|2": "le nombre dérivé de la fonction $f^{-1}$ en un point $y_0$",
    "13|2|5|1": DERIV_FORMS, "14|2|5|1": DERIV_FORMS,
    "13|2|6|2": REPR_FORMS, "14|2|6|2": REPR_FORMS,
    "13|2|7|0": "graphiquement la dérivabilité à droite (resp. à gauche) d’une fonction en un point $x_0$",
    "14|2|7|0": "graphiquement la dérivabilité à droite (resp. à gauche) d’une fonction en un point $x_0$",
    "13|2|8|0": "qu’une fonction composée est dérivable en un point $x_0$",
    "14|2|8|0": "qu’une fonction composée est dérivable en un point $x_0$",
    "13|3|0|1": PRIM_TC, "14|3|0|1": PRIM_TC,
    "13|3|1|3": PRIM_TC_DET, "14|3|1|3": PRIM_TC_DET,
    "13|4|0|10": "les primitives des fonctions du type : $\\frac{u'}{u}$",
    "14|4|0|10": "les primitives des fonctions du type : $\\frac{u'}{u}$",
    "13|4|2|1": "une équation de la forme $x^n = k$ $(k \\in \\mathbb{R}_+^*,\\ n \\in \\mathbb{N}^*)$",
    "14|4|2|1": "une équation de la forme $x^n = k$ $(k \\in \\mathbb{R}_+^*,\\ n \\in \\mathbb{N}^*)$",
    "13|4|2|2": ("une inéquation d’inconnue $n$ de la forme $q^n \\geq a$ ou $q^n \\leq a$ "
                 "$(q \\in \\mathbb{R}_+^*,\\ a \\in \\mathbb{R}_+^*,\\ n \\in \\mathbb{N}^*)$"),
    "14|4|2|2": ("une inéquation d’inconnue $n$ de la forme $q^n \\geq a$ ou $q^n \\leq a$ "
                 "$(q \\in \\mathbb{R}_+^*,\\ a \\in \\mathbb{R}_+^*,\\ n \\in \\mathbb{N}^*)$"),
    "13|4|3|1": "les primitives des fonctions du type : $\\frac{u'}{u}$, où $u$ est une fonction dérivable non nulle",
    "14|4|3|1": "les primitives des fonctions du type : $\\frac{u'}{u}$, où $u$ est une fonction dérivable non nulle",
    "13|6|5|0": "les variations des fonctions du type : $x \\mapsto \\int_a^x f(t)\\,dt$",
    "14|6|5|0": "les variations des fonctions du type : $x \\mapsto \\int_a^x f(t)\\,dt$",
    "13|6|6|0": "une allure d’une fonction du type : $x \\mapsto \\int_a^x f(t)\\,dt$",
    "14|6|6|0": "une allure d’une fonction du type : $x \\mapsto \\int_a^x f(t)\\,dt$",
    "13|7|0|3": SUITES_CONV, "14|7|0|3": SUITES_CONV,
    "13|7|0|5": ("les propriétés sur la convergence : des suites géométriques ; des suites arithmétiques ; "
                 "des suites du type $(n^\\alpha)$"),
    "14|7|0|5": ("les propriétés sur la convergence : des suites géométriques ; des suites arithmétiques ; "
                 "des suites du type $(n^\\alpha)$"),
    "13|7|0|7": ("les propriétés sur les limites et comportements asymptotiques comparés des suites $(\\ln n)$ ; "
                 "$(a^n)$, $a > 0$ et $(n^\\alpha)$, $\\alpha$ réel"),
    "14|7|0|7": ("les propriétés sur les limites et comportements asymptotiques comparés des suites $(\\ln n)$ ; "
                 "$(a^n)$, $a > 0$ et $(n^\\alpha)$, $\\alpha$ réel"),
    "13|7|5|0": "la plus petite valeur de $n$ telle que : $u_n \\geq 10^p$, $p \\in \\mathbb{N}$",
    "14|7|5|0": "la plus petite valeur de $n$ telle que : $u_n \\geq 10^p$, $p \\in \\mathbb{N}$",
    "13|7|5|1": "la plus petite valeur de $n$ telle que : $|u_n - l| \\leq 10^{-p}$, $p \\in \\mathbb{N}$",
    "14|7|5|1": "la plus petite valeur de $n$ telle que : $|u_n - l| \\leq 10^{-p}$, $p \\in \\mathbb{N}$",
    "13|8|3|2": "une équation différentielle du type $f'' = 0$",
    "14|8|3|2": "une équation différentielle du type $f'' = 0$",
    "13|10|1|0": "une probabilité conditionnelle : $P(A/B)$ ou $P_B(A)$",
    "14|10|1|0": "une probabilité conditionnelle : $P(A/B)$ ou $P_B(A)$",
    "13|11|0|6": "la propriété relative à la réduction de : " + SUM_A,
    "13|11|0|7": "la propriété relative à la réduction de : " + SUM_B,
    "13|11|0|8": ("la propriété relative à la ligne de niveau de l’application : "
                  "$M \\mapsto \\mathrm{Mes}(\\widehat{\\overrightarrow{MA}, \\overrightarrow{MB}})$"),
    "13|11|0|9": "la propriété relative à la ligne de niveau de l’application : $M \\mapsto \\frac{MA}{MB}$",
    "13|11|0|10": "la propriété relative à la ligne de niveau de l’application : $M \\mapsto \\sum \\alpha_i MA_i^2$",
    "13|11|3|0": SUM_A, "13|11|3|1": SUM_B,
    "13|11|4|0": LN_TYPES,
    "13|11|5|1": "les lignes de niveau, dans le plan, des applications de l’un des types : " + LN_TYPES.split(" : ", 1)[1],
    "13|17|1|0": "$a$ divise $b$ : $a \\mid b$",
}

# ---- seconde passe ----
LN2 = "$x \\mapsto ax + b + \\ln x$ ; $x \\mapsto ax + b - \\ln x$"
LN3 = LN2 + " ; $x \\mapsto \\ln(ax^2 + bx + c)$ où $(a ; b) \\neq (0 ; 0)$"
EXP4 = ("$x \\mapsto ax + b + e^x$ ; $x \\mapsto ax + b - e^x$ ; $x \\mapsto (ax+b)e^x$ ; "
        "$x \\mapsto \\frac{e^x}{ax+b}$")
RAT_EQ = "des équations du type : $\\frac{ax+b}{cx+d} = 0$"
RAT_INEQ = ("des inéquations du type : $\\frac{ax+b}{cx+d} \\geq 0$ ou $\\frac{ax+b}{cx+d} > 0$, "
            "du type $\\frac{ax+b}{cx+d} \\leq 0$ ou $\\frac{ax+b}{cx+d} < 0$")
BICARRE_1A = "des équations du type $ax^4 + bx^2 + c = 0$ $(a \\neq 0)$"
BICARRE_1C = "des équations du type : $ax^4 + bx^2 + c = 0$, où $a$, $b$ et $c$ sont des nombres réels"
INEQ_ALG = ("algébriquement les inéquations du type $f(x) \\geq 0$, $f(x) > 0$, $f(x) \\leq 0$, $f(x) < 0$, "
            "$f(x) \\geq ax+b$, $f(x) > ax+b$, $f(x) \\leq ax+b$, $f(x) < ax+b$ avec $(a ; b) \\neq (0 ; 0)$, "
            "où $f$ est une fonction homographique ou une fonction rationnelle du type "
            "$x \\mapsto \\frac{ax^2+bx+c}{dx+e}$ où $a \\neq 0$ et $d \\neq 0$")
REPR_TC = ("graphiquement une fonction du type : $x \\mapsto \\cos(ax+b)$ ; $x \\mapsto \\sin(ax+b)$ ; "
           "$x \\mapsto \\tan(ax+b)$ ; $x \\mapsto \\frac{ax+b}{cx^2+dx+e}$ ; $x \\mapsto \\frac{ax^2+bx+c}{dx^2+ex+f}$ ; "
           "$x \\mapsto \\sqrt{ax+b}$ ; $x \\mapsto \\sqrt{ax^2+bx+c}$")
ANG = {
    7: "la propriété sur les mesures des angles $(k\\vec{u}, \\vec{v})$ ; $(\\vec{u}, k\\vec{v})$ ; $(k\\vec{u}, k\\vec{v})$ ; $k \\in \\mathbb{R}^*$",
    9: "la propriété sur les mesures des angles : $(\\vec{u}, \\vec{v})$ et $(\\vec{v}, \\vec{u})$",
    10: "les propriétés sur le double d’un angle orienté",
    11: ("la propriété des angles inscrits orientés : $(\\overrightarrow{OA}, \\overrightarrow{OB}) = "
         "2(\\overrightarrow{MA}, \\overrightarrow{MB})$"),
    15: ("les formules de duplication et de linéarisation : $\\cos(2a)$ ; $\\sin(2a)$ ; $\\cos^2(a)$ ; $\\sin^2(a)$"),
}
C.update({
    "02|0|2|0": "la formule $(a \\times b)^n = a^n \\times b^n$, connaissant les entiers naturels $a$, $b$ et $n$",
    "03|2|0|3": ("la factorisation de chacune des expressions remarquables : $a^2 + 2ab + b^2$, "
                 "$a^2 - 2ab + b^2$, $a^2 - b^2$"),
    "04|10|3|0": ("le point $M$ tel que $\\overrightarrow{AM} = k\\overrightarrow{AB}$, où $k$ est un réel non nul "
                  "et le vecteur $\\overrightarrow{AB}$ donné"),
    "06|7|0|9": ("la propriété relative à l’existence et à l’unicité du point $M$ tel que : "
                 "$\\overrightarrow{OM} = \\vec{u}$, où $O$ est un point et $\\vec{u}$ un vecteur donnés"),
    "06|7|5|0": "le point $M$ tel que : $\\overrightarrow{OM} = \\vec{u}$, où $O$ est un point et $\\vec{u}$ un vecteur donnés",
    "07|0|5|0": None, "07|0|5|1": RAT_EQ, "07|0|5|2": RAT_INEQ, "07|0|5|5": BICARRE_1A,
    "08|0|5|0": None, "08|0|5|1": RAT_EQ, "08|0|5|2": RAT_INEQ,
    "09|0|5|4": BICARRE_1C, "10|0|5|4": BICARRE_1C,
    "09|6|4|1": HOMOG, "10|6|4|1": HOMOG,
    "09|6|4|2": "la représentation graphique d’une fonction de type $x \\mapsto ax + b + \\frac{c}{dx+e}$",
    "10|6|4|2": "la représentation graphique d’une fonction de type $x \\mapsto ax + b + \\frac{c}{dx+e}$",
    # limite de 1/(x-a) : l'item « à droite » existe déjà dans le programme
    "11|1|0|2": "la limite à gauche en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$",
    "11|1|0|3": "la limite à droite en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$",
    "11|1|1|3": "la limite à gauche en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$",
    "11|1|1|4": "la limite à droite en $a$ de la fonction : $x \\mapsto \\frac{1}{x-a}$",
    "12|1|0|2": "la limite à gauche en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$",
    "12|1|0|3": "la limite à droite en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$",
    "12|1|1|3": "la limite à gauche en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$",
    "12|1|1|4": "la limite à droite en $a$ de la fonction $x \\mapsto \\frac{1}{x-a}$",
    "11|1|8|1": INEQ_ALG, "12|1|8|1": INEQ_ALG,
    # Tle A1 : logarithme
    "11|3|0|4": "la dérivée d’une fonction de chacun des types : " + LN2,
    "11|3|4|0": "la fonction dérivée d’une fonction de chacun des types : " + LN2,
    "11|3|4|1": "le signe de la dérivée de chacune des fonctions des types : " + LN2,
    "11|3|4|2": "le sens de variation de chacune des fonctions des types : " + LN2,
    "11|3|4|3": None,
    "11|3|4|4": "des limites d’une fonction de chacun des types : " + LN2,
    "11|3|5|0": "le tableau de variation d’une fonction de chacun des types : " + LN2,
    "11|3|6|0": "graphiquement une fonction de chacun des types : $x \\mapsto \\ln x$ ; " + LN2,
    # Tle A2 : logarithme
    "12|2|0|4": "la dérivée d’une fonction de chacun des types : " + LN2,
    "12|2|4|0": "la fonction dérivée d’une fonction de chacun des types : " + LN3,
    "12|2|4|1": "le signe de la dérivée de chacune des fonctions des types : " + LN3,
    "12|2|4|2": "le sens de variation de chacune des fonctions des types : " + LN2,
    "12|2|4|3": "des limites d’une fonction de chacun des types : " + LN2,
    "12|2|5|0": "le tableau de variation d’une fonction de chacun des types : " + LN2,
    "12|2|6|0": "graphiquement une fonction de chacun des types : $x \\mapsto \\ln x$ ; " + LN2,
    # Tle A1 / A2 : exponentielle
    "11|4|0|4": ("la dérivée d’une fonction de chacun des types : " + EXP4 +
                 " ; $x \\mapsto \\exp(ax^2 + bx + c)$, où $(a ; b) \\neq (0 ; 0)$"),
    "12|3|0|4": ("la dérivée d’une fonction de chacun des types : " + EXP4 +
                 " ; $x \\mapsto \\exp(ax^2 + bx + c)$, où $(a ; b) \\neq (0 ; 0)$"),
    "11|4|0|5": "le résultat $e^0 = 1$", "12|3|0|5": "le résultat $e^0 = 1$",
    "11|4|5|0": "la fonction dérivée d’une fonction de chacun des types : " + EXP4,
    "12|3|5|0": "la fonction dérivée d’une fonction de chacun des types : " + EXP4,
    "11|4|5|1": "les limites de fonctions de chacun des types : " + EXP4,
    "12|3|5|1": "les limites de fonctions de chacun des types : " + EXP4,
    "11|4|6|0": "les variations d’une fonction de chacun des types : " + EXP4 + " en utilisant le signe de la fonction dérivée",
    "12|3|6|0": "les variations d’une fonction de chacun des types : " + EXP4 + " en utilisant le signe de la fonction dérivée",
    "12|3|6|1": None,
    "11|4|7|0": "le tableau de variation d’une fonction de chacun des types : " + EXP4,
    "12|3|7|0": "le tableau de variation d’une fonction de chacun des types : " + EXP4,
    "11|4|8|0": "graphiquement une fonction de chacun des types : $x \\mapsto e^x$ ; " + EXP4,
    "12|3|8|0": "graphiquement une fonction de chacun des types : $x \\mapsto e^x$ ; " + EXP4,
    # Tle C / D
    "13|1|2|0": None, "14|1|2|0": None,
    "13|2|6|3": REPR_TC, "14|2|6|3": REPR_TC,
    "14|2|6|4": None, "14|2|6|5": None, "14|2|6|6": None, "14|2|6|7": None, "14|2|6|8": None, "14|2|6|9": None,
    "13|5|0|9": ("l’allure de la courbe représentative de la fonction $x \\mapsto x^\\alpha$, $\\alpha \\neq 0$ "
                 "suivant que $\\alpha < 1$ ou $\\alpha > 1$"),
    "14|5|0|9": ("l’allure de la courbe représentative de la fonction : $x \\mapsto x^\\alpha$, $\\alpha \\neq 0$ "
                 "suivant que $\\alpha < 0$ ou $\\alpha > 0$"),
    "14|5|0|10": ("l’allure de la courbe représentative de la fonction : $x \\mapsto a^x$, $a \\neq 1$ "
                  "suivant que $0 < a < 1$ ou $a > 1$"),
    "13|6|2|0": ("une intégrale en utilisant : les primitives des fonctions usuelles ; la relation de Chasles ; "
                 "une intégration par parties ; un changement de variable affine ; une fonction du type "
                 "$u' \\times f' \\circ u$"),
    "14|6|2|0": ("une intégrale en utilisant : les primitives des fonctions usuelles ; la relation de Chasles ; "
                 "une intégration par parties ; un changement de variable affine ; une fonction du type "
                 "$u' \\times f' \\circ u$"),
})
for doc, lesson in (("09", 13), ("10", 12)):
    for ci, txt in ANG.items():
        C[f"{doc}|{lesson}|0|{ci}"] = txt
C.update({
    "07|3|0|3": ("les formules des dérivées des fonctions de l’un des types : $u + v$ ; $au$ ; $uv$ ; $\\frac{u}{v}$ ; "
                 "où $u$ et $v$ sont des fonctions dérivables et $a$ un nombre réel"),
    "08|3|0|3": ("les formules des dérivées des fonctions du type : $u + v$ ; $au$ ; $uv$ ; $\\frac{u}{v}$ ; "
                 "où $u$ et $v$ sont des fonctions dérivables et $a$ un nombre réel"),
})

# ---- relecture à l'écran (Tle C/D « Noter », fonctions circulaires) ----
C.update({
    "13|1|2|1": "une racine $n$-ième d’un nombre positif ($\\sqrt[n]{x}$ ou $x^{\\frac{1}{n}}$)",
    "14|1|2|1": "une racine $n$-ième d’un nombre positif ($\\sqrt[n]{x}$ ou $x^{\\frac{1}{n}}$)",
    "13|1|2|2": "une puissance d’exposant rationnel ($x^{\\frac{p}{q}}$)",
    "14|1|2|2": "une puissance d’exposant rationnel ($x^{\\frac{p}{q}}$)",
    "13|0|4|0": "des puissances de $\\cos x$ et $\\sin x$",
    "14|0|4|0": "des puissances de $\\cos x$ et $\\sin x$",
})
