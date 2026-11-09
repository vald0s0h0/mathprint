"""Encodeur DataMatrix ECC200 rectangulaire, sans dépendance (ISO/IEC 16022).

Le code d'identité de page est un DataMatrix de 8 rangées (8×32 pour 20
chiffres, 8×18 pour 10) : 8 modules sur la hauteur d'un fiduciel, le plus
lisible possible. Les chiffres sont écrits deux par deux (un mot de code par
paire, 130 + valeur), exactement comme le font les encodeurs du commerce — la
lecture se fait avec n'importe quel lecteur DataMatrix (zxing-cpp côté vision).

Seuls les deux symboles à 8 rangées sont gérés : c'est tout ce dont la mise en
page a besoin, et chacun a été comparé module par module à un encodeur de
référence. `encode_digits` retourne la matrice (liste de rangées de booléens,
True = module noir), sans zone de silence.
"""
from __future__ import annotations

# taille -> (rangées, colonnes, régions en colonnes, mots de données, mots de correction)
_SYMBOLS = {
    5: (8, 18, 1, 5, 7),
    10: (8, 32, 2, 10, 11),
}
ROWS = 8


def _gf_tables():
    exp, log = [0] * 512, [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x12D           # polynôme primitif x^8 + x^5 + x^3 + x^2 + 1
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _gf_tables()


def _rs_generator(n: int) -> list[int]:
    """Polynôme générateur de n mots de correction : racines α^1 … α^n."""
    poly = [1]
    for i in range(1, n + 1):
        nxt = [0] * (len(poly) + 1)
        for j, coef in enumerate(poly):
            nxt[j] ^= coef
            if coef:
                nxt[j + 1] ^= _EXP[(_LOG[coef] + i) % 255]
        poly = nxt
    return poly


def _rs_remainder(data: list[int], n_ec: int) -> list[int]:
    gen = _rs_generator(n_ec)
    reg = [0] * n_ec
    for d in data:
        feedback = d ^ reg[0]
        reg = reg[1:] + [0]
        if feedback:
            for j in range(n_ec):
                coef = gen[j + 1]
                if coef:
                    reg[j] ^= _EXP[(_LOG[feedback] + _LOG[coef]) % 255]
    return reg


def _placement(nrow: int, ncol: int) -> list[list[tuple[int, int] | None]]:
    """Placement des bits (annexe F de la norme) : pour chaque module de la
    matrice de données, (index du mot de code, numéro du bit 1..8)."""
    grid: list[list[tuple[int, int] | None]] = [[None] * ncol for _ in range(nrow)]

    def module(row, col, chr_, bit):
        if row < 0:
            row += nrow
            col += 4 - ((nrow + 4) % 8)
        if col < 0:
            col += ncol
            row += 4 - ((ncol + 4) % 8)
        grid[row][col] = (chr_, bit)

    def utah(row, col, chr_):
        module(row - 2, col - 2, chr_, 1)
        module(row - 2, col - 1, chr_, 2)
        module(row - 1, col - 2, chr_, 3)
        module(row - 1, col - 1, chr_, 4)
        module(row - 1, col, chr_, 5)
        module(row, col - 2, chr_, 6)
        module(row, col - 1, chr_, 7)
        module(row, col, chr_, 8)

    def corner1(chr_):
        module(nrow - 1, 0, chr_, 1)
        module(nrow - 1, 1, chr_, 2)
        module(nrow - 1, 2, chr_, 3)
        module(0, ncol - 2, chr_, 4)
        module(0, ncol - 1, chr_, 5)
        module(1, ncol - 1, chr_, 6)
        module(2, ncol - 1, chr_, 7)
        module(3, ncol - 1, chr_, 8)

    def corner2(chr_):
        module(nrow - 3, 0, chr_, 1)
        module(nrow - 2, 0, chr_, 2)
        module(nrow - 1, 0, chr_, 3)
        module(0, ncol - 4, chr_, 4)
        module(0, ncol - 3, chr_, 5)
        module(0, ncol - 2, chr_, 6)
        module(0, ncol - 1, chr_, 7)
        module(1, ncol - 1, chr_, 8)

    def corner3(chr_):
        module(nrow - 3, 0, chr_, 1)
        module(nrow - 2, 0, chr_, 2)
        module(nrow - 1, 0, chr_, 3)
        module(0, ncol - 2, chr_, 4)
        module(0, ncol - 1, chr_, 5)
        module(1, ncol - 1, chr_, 6)
        module(2, ncol - 1, chr_, 7)
        module(3, ncol - 1, chr_, 8)

    def corner4(chr_):
        module(nrow - 1, 0, chr_, 1)
        module(nrow - 1, ncol - 1, chr_, 2)
        module(0, ncol - 3, chr_, 3)
        module(0, ncol - 2, chr_, 4)
        module(0, ncol - 1, chr_, 5)
        module(1, ncol - 3, chr_, 6)
        module(1, ncol - 2, chr_, 7)
        module(1, ncol - 1, chr_, 8)

    chr_ = 0
    row, col = 4, 0
    while True:
        if row == nrow and col == 0:
            corner1(chr_); chr_ += 1
        if row == nrow - 2 and col == 0 and ncol % 4:
            corner2(chr_); chr_ += 1
        if row == nrow - 2 and col == 0 and ncol % 8 == 4:
            corner3(chr_); chr_ += 1
        if row == nrow + 4 and col == 2 and not ncol % 8:
            corner4(chr_); chr_ += 1
        while True:                                   # diagonale montante
            if 0 <= row < nrow and 0 <= col < ncol and grid[row][col] is None:
                utah(row, col, chr_); chr_ += 1
            row -= 2
            col += 2
            if not (row >= 0 and col < ncol):
                break
        row += 1
        col += 3
        while True:                                   # diagonale descendante
            if 0 <= row < nrow and 0 <= col < ncol and grid[row][col] is None:
                utah(row, col, chr_); chr_ += 1
            row += 2
            col -= 2
            if not (row < nrow and col >= 0):
                break
        row += 3
        col += 1
        if row >= nrow and col >= ncol:
            break
    return grid


def encode_digits(digits: str) -> list[list[bool]]:
    """DataMatrix 8×18 (≤ 10 chiffres) ou 8×32 (≤ 20 chiffres, nombre pair
    complété par un zéro) pour une chaîne de chiffres."""
    if not digits.isascii() or not digits.isdigit():
        raise ValueError("chiffres uniquement")
    if len(digits) % 2:
        digits = "0" + digits
    pairs = len(digits) // 2
    size = 5 if pairs <= 5 else 10 if pairs <= 10 else 0
    if not size:
        raise ValueError("20 chiffres au plus")
    rows, cols, regions, n_data, n_ec = _SYMBOLS[size]
    words = [130 + int(digits[i:i + 2]) for i in range(0, len(digits), 2)]
    # remplissage : 129 puis mots de bourrage pseudo-aléatoires (§5.2.3)
    if len(words) < n_data:
        words.append(129)
        while len(words) < n_data:
            pos = len(words) + 1
            r = (149 * pos) % 253 + 1
            words.append((129 + r) % 254 if 129 + r > 254 else 129 + r)
    words += _rs_remainder(words, n_ec)

    region_w = (cols // regions) - 2                  # colonnes de données par région
    nrow, ncol = rows - 2, region_w * regions         # matrice de données assemblée
    placement = _placement(nrow, ncol)
    bits = [[0] * ncol for _ in range(nrow)]
    for r in range(nrow):
        for c in range(ncol):
            chr_, bit = placement[r][c]
            bits[r][c] = (words[chr_] >> (8 - bit)) & 1

    sym = [[False] * cols for _ in range(rows)]
    for g in range(regions):
        x0 = g * (region_w + 2)
        for r in range(rows):
            for c in range(region_w + 2):
                x = x0 + c
                if c == 0 or r == rows - 1:           # repère en L : bords gauche et bas pleins
                    sym[r][x] = True
                elif r == 0:                          # horloge du haut, alternée
                    sym[r][x] = (c % 2 == 0)
                elif c == region_w + 1:               # horloge de droite, alternée
                    sym[r][x] = (r % 2 == 1)
                else:
                    sym[r][x] = bool(bits[r - 1][g * region_w + c - 1])
    return sym
