// Formules en TEXTE SIMPLE, pour les endroits qui n'acceptent qu'une chaîne :
// options d'un Select, infobulles, attribut `title`, notifications, recherche.
// Là, KaTeX ne peut pas rendre la formule ; afficher « $x^2 = a$ » tel quel
// montre du LaTeX à un professeur. On écrit donc la formule comme à la main :
// « x² = a », « √11 », « 3/4 », « x ≤ 5 », « 12,5 ».
//
// Ailleurs (JSX), préférer <MathText>, qui rend la vraie formule.
// Miroir de backend services/handwriting.plain (même conventions).

const SUPERSCRIPT: Record<string, string> = {
  0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹',
  '-': '⁻', '+': '⁺', n: 'ⁿ',
}

// Les plus longues d'abord (\leq avant \le). Une commande alphabétique ne se
// remplace que si elle n'est pas le début d'une autre (\le ≠ \left).
const COMMANDS: Array<[string, string]> = [
  ['\\times', '×'], ['\\div', '÷'], ['\\cdot', '×'], ['\\leqslant', '≤'], ['\\geqslant', '≥'],
  ['\\leq', '≤'], ['\\geq', '≥'], ['\\neq', '≠'], ['\\le', '≤'], ['\\ge', '≥'], ['\\ne', '≠'],
  ['\\approx', '≈'], ['\\pm', '±'], ['\\pi', 'π'], ['\\infty', '∞'], ['\\degree', '°'],
  ['^\\circ', '°'], ['^{\\circ}', '°'], ['\\circ', '°'], ['\\euro', '€'], ['\\%', '%'],
  ['\\Rightarrow', '⇒'], ['\\Leftrightarrow', '⇔'], ['\\rightarrow', '→'], ['\\to', '→'],
  ['\\mapsto', '↦'], ['\\longmapsto', '↦'], ['\\ldots', '…'], ['\\dots', '…'], ['\\cdots', '⋯'],
  ['\\angle', '∠'], ['\\parallel', '∥'], ['\\perp', '⊥'], ['\\in', '∈'], ['\\notin', '∉'],
  ['\\left', ''], ['\\right', ''], ['\\qquad', ' '], ['\\quad', ' '],
  ['\\,', ' '], ['\\;', ' '], ['\\:', ' '], ['\\!', ''], ['\\ ', ' '],
  ['\\{', '{'], ['\\}', '}'],
]
const GREEK: Record<string, string> = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε', theta: 'θ',
  lambda: 'λ', mu: 'μ', rho: 'ρ', sigma: 'σ', tau: 'τ', phi: 'φ', varphi: 'φ', omega: 'ω',
  Delta: 'Δ', Omega: 'Ω', Sigma: 'Σ', Pi: 'Π',
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const COMMAND_RES = COMMANDS.map(([latex, text]) =>
  [new RegExp(escape(latex) + (/[a-zA-Z]$/.test(latex) ? '(?![a-zA-Z])' : ''), 'g'), text] as const)

function group(text: string): string {
  const t = text.trim()
  return /[+\-×÷/ ]/.test(t.replace(/^-/, '')) ? `(${t})` : t
}

function power(exp: string): string {
  const e = exp.trim()
  if (/^[-+]?(\d+|n)$/.test(e)) return [...e].map((c) => SUPERSCRIPT[c] ?? c).join('')
  return e.length > 1 ? `^(${e})` : `^${e}`
}

/** Un span LaTeX (sans ses $) écrit en texte simple. */
export function latexToText(latex: string): string {
  let s = latex.replace(/\{,\}/g, ',')
  for (let i = 0; i < 4; i++) {
    const before = s
    s = s.replace(/\\(?:text|textbf|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}/g, '$1')
    s = s.replace(/\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, (_m, a, b) => `${group(a)}/${group(b)}`)
    s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, (_m, a) => `√${group(a)}`)
    // angle « ABC » chapeau sur le sommet, vecteur flèche, segment surligné :
    // diacritiques combinants, la notation reste celle du cahier
    s = s.replace(/\\(?:widehat|hat)\s*\{([^{}]*)\}/g, (_m, a: string) => {
      const mid = Math.floor((a.length - 1) / 2)
      return a.slice(0, mid + 1) + '̂' + a.slice(mid + 1)
    })
    s = s.replace(/\\vec\s*\{([^{}]*)\}/g, (_m, a: string) => `${a}⃗`)
    s = s.replace(/\\(?:overline|bar)\s*\{([^{}]*)\}/g, (_m, a: string) =>
      [...a].map((ch) => `${ch}̅`).join(''))
    if (s === before) break
  }
  for (const [re, text] of COMMAND_RES) s = s.replace(re, text)
  s = s.replace(/\\([a-zA-Z]+)(?![a-zA-Z])/g, (m, name) => GREEK[name] ?? '')
  s = s.replace(/\^\s*(?:\{([^{}]*)\}|(-?\w))/g, (_m, a, b) => power(a ?? b))
  s = s.replace(/_\s*(?:\{([^{}]*)\}|(\w))/g, (_m, a, b) => a ?? b)
  s = s.replace(/[{}\\]/g, '').replace(/\s+/g, ' ')
  return s.replace(/\(\s+/g, '(').replace(/\s+\)/g, ')').trim()
}

/** Texte mêlant mots et spans `$...$` (ou `\(...\)`) : seules les formules sont
 *  réécrites, le reste est intact. Idempotent sur un texte sans formule. */
export function mathPlain(text: string | null | undefined): string {
  if (!text) return ''
  return text
    .replace(/\$\$([^$]+)\$\$/g, (_m, f) => latexToText(f))
    .replace(/\\\((.+?)\\\)|\\\[(.+?)\\\]/g, (_m, a, b) => latexToText(a ?? b))
    .replace(/(^|[^\\])\$([^$]+)\$/g, (_m, pre, f) => pre + latexToText(f))
    .replace(/(^|[^\\])\$([^$]+)\$/g, (_m, pre, f) => pre + latexToText(f))
}
