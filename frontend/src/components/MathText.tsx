// Rendu fiable des formules mathématiques via KaTeX.
// Découpe sur $...$ (spans délimitant du LaTeX), renderise via KaTeX côté web.
//
// Les sauts de ligne de l'énoncé sont RENDUS (white-space: pre-wrap) : ils font
// partie du texte (cf. backend services/statement.py), c'est eux qui séparent
// une donnée de la suivante et une sous-question de la précédente. Sans ça, le
// HTML les replie en espaces et l'aperçu de la banque montrerait un énoncé d'un
// seul tenant là où la copie imprimée, elle, est bien mise en lignes.
import { Box } from '@mantine/core'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import React, { useMemo } from 'react'
import { splitBold } from '../utils/richblocks'

/** Découpe les syntaxes usuelles de notre banque et de Mathpix : `$...$`,
 * `$$...$$`, `\(...\)` et `\[...\]`. */
function splitMathSpans(text: string): Array<[string, boolean]> {
  const spans: Array<[string, boolean]> = []
  const delimiters = [
    { open: '$$', close: '$$' }, { open: '\\(', close: '\\)' },
    { open: '\\[', close: '\\]' }, { open: '$', close: '$' },
  ]
  const findToken = (token: string, from: number): number => {
    let at = text.indexOf(token, from)
    while (at >= 0) {
      let slashes = 0
      for (let i = at - 1; i >= 0 && text[i] === '\\'; i--) slashes++
      if (slashes % 2 === 0) return at
      at = text.indexOf(token, at + token.length)
    }
    return -1
  }
  let pos = 0
  while (pos < text.length) {
    const found = delimiters
      .map((d) => ({ ...d, at: findToken(d.open, pos) }))
      .filter((d) => d.at >= 0)
      .sort((a, b) => a.at - b.at || b.open.length - a.open.length)[0]
    if (!found) {
      if (pos < text.length) spans.push([text.slice(pos), false])
      break
    }
    if (found.at > pos) spans.push([text.slice(pos, found.at), false])
    const contentStart = found.at + found.open.length
    const end = findToken(found.close, contentStart)
    if (end < 0) {
      spans.push([text.slice(found.at), false])
      break
    }
    const mathContent = text.slice(contentStart, end)
    if (mathContent) spans.push([mathContent, true])
    pos = end + found.close.length
  }
  return spans
}

// Cases de réponse insérées dans le fil du texte (cf. backend services/statement.py
// et services/indigo_fields.py). Le PDF les imprime en cases à remplir ; l'aperçu
// web doit faire pareil, sinon le marqueur littéral s'affiche tel quel.
// - {{blank}}       : case standard (~20 mm) ;
// - {{mini}}        : mini-case 2 chiffres (~9 mm) — trou d'équation à trous ;
// - {{blank_right}} : case qui s'étire jusqu'au bord droit (réponse plus longue).
const BLANK_TOKEN = '{{blank}}'
const MINI_TOKEN = '{{mini}}'
const WIDE_TOKEN = '{{blank_right}}'
// découpe en gardant chaque marque (la plus longue d'abord : blank_right ⊃ blank)
const ANSWER_SPLIT = /(\{\{blank_right\}\}|\{\{blank\}\}|\{\{mini\}\})/

// Espace fine insécable (U+202F) : typographie française. Sans elle, l'aperçu
// montre une pleine espace avant « ; : ! ? » et à l'intérieur des guillemets
// « … » (« trop de séparation »). Idempotent (absorbe l'espace déjà présente).
const NNBSP = ' '
const SP = '[ \\u00A0\\u202F]'          // espace normale / insécable / fine
const RE_PUNCT = new RegExp(`${SP}*([;!?%])`, 'g')
const RE_COLON = new RegExp(`([^0-9\\s])${SP}*:(?=\\s|$)`, 'g')  // pas dans 12:30 ni une URL
const RE_GUILL_OPEN = new RegExp(`«${SP}*`, 'g')
const RE_GUILL_CLOSE = new RegExp(`${SP}*»`, 'g')

function frenchSpacing(s: string): string {
  if (!s) return s
  return s
    .replace(RE_PUNCT, `${NNBSP}$1`)
    .replace(RE_COLON, `$1${NNBSP}:`)
    .replace(RE_GUILL_OPEN, `«${NNBSP}`)
    .replace(RE_GUILL_CLOSE, `${NNBSP}»`)
}

/** Case de réponse vide, dessinée en ligne à la place d'un marqueur. La variante
 *  fixe la largeur (mini = 2 chiffres, standard, ou pleine largeur qui pousse
 *  jusqu'au bord droit — approché par flex:1 dans le fil de texte). */
function BlankBox({ kind = 'normal' }: { kind?: 'normal' | 'mini' | 'right' }) {
  const base = {
    display: 'inline-block', height: kind === 'mini' ? '7mm' : '8mm',
    border: '1px solid var(--mantine-color-gray-5)', borderRadius: 2,
    margin: '0 0.12em', verticalAlign: '-0.18em',
  } as const
  if (kind === 'right')
    return <Box component="span" aria-label="case à remplir" style={{ ...base, minWidth: '22mm', width: '55%' }} />
  if (kind === 'mini')
    return <Box component="span" aria-label="mini-case" style={{ ...base, width: '9mm' }} />
  return <Box component="span" aria-label="case à remplir" style={{ ...base, width: '20mm' }} />
}

const TOKEN_KIND: Record<string, 'normal' | 'mini' | 'right'> = {
  [BLANK_TOKEN]: 'normal', [MINI_TOKEN]: 'mini', [WIDE_TOKEN]: 'right',
}

/** Texte brut (hors formule) pouvant contenir des marqueurs de case : chaque
 *  marqueur devient une case à remplir de la bonne taille, le reste est rendu
 *  tel quel (espaces insécables françaises appliquées, cf. frenchSpacing). */
function TextSpan({ content }: { content: string }) {
  const text = frenchSpacing(content)
  if (!ANSWER_SPLIT.test(text)) return <span>{text}</span>
  const parts = text.split(ANSWER_SPLIT)
  return (
    <>
      {parts.map((part, i) => {
        const kind = TOKEN_KIND[part]
        if (kind) return <BlankBox key={i} kind={kind} />
        return part ? <span key={i}>{part}</span> : null
      })}
    </>
  )
}

/** Rendu d'un span LaTeX, fallback texte brut si erreur (ne devrait jamais arriver).
 *
 * `white-space: nowrap` rend la formule INSÉCABLE : le navigateur ne choisit
 * plus un point de coupure À L'INTÉRIEUR (ce qui coupait « (3x+1)(-2x+5)=0 »
 * en deux morceaux illisibles), mais avant ou après le span entier. Ce n'est
 * PAS une règle dure — une formule seule, trop longue pour tenir même sur une
 * ligne pleine largeur, déborde plutôt que d'être charcutée : c'est le compromis
 * demandé (jamais coupée au milieu, sauf si vraiment impossible de faire
 * autrement). */
function MathSpan({ latex }: { latex: string }) {
  try {
    const html = katex.renderToString(latex, { throwOnError: false })
    return <span style={{ whiteSpace: 'nowrap' }} dangerouslySetInnerHTML={{ __html: html }} />
  } catch (_) {
    // Fallback : afficher le LaTeX brut ou texte sûr
    return <span style={{ whiteSpace: 'nowrap' }}>{latex}</span>
  }
}

/** Réponse mathématique isolée (attendu ou OCR Mathpix).
 *
 * Contrairement à un énoncé, une réponse peut arriver sous plusieurs formes :
 * `$...$` depuis notre contrat, `\(...\)` / `\[...\]` depuis Mathpix, ou un
 * fragment LaTeX nu (`\dfrac{1}{2}`). On retire uniquement les délimiteurs qui
 * entourent toute la valeur, puis KaTeX rend la formule. Un texte réellement
 * mixte reste confié à MathText afin de préserver les mots ordinaires. */
function unwrapMath(value: string): string | null {
  const s = value.trim()
  const wrappers: Array<[string, string]> = [
    ['$$', '$$'], ['\\[', '\\]'], ['\\(', '\\)'], ['$', '$'],
  ]
  for (const [open, close] of wrappers) {
    if (s.startsWith(open) && s.endsWith(close)
        && s.length > open.length + close.length) {
      const inner = s.slice(open.length, -close.length).trim()
      // `$a$ · $b$` contient plusieurs spans : ce n'est pas une unique
      // formule entourée de délimiteurs, MathText doit le découper.
      if (!inner.includes(close)) return inner
    }
  }
  return null
}

function looksLikeBareLatex(value: string): boolean {
  const s = value.trim()
  return /\\[a-zA-Z]+|[_^{}]/.test(s)
    || /^[\d\s.,()+\-*/=<>×÷]+$/.test(s)
    || /^[a-zA-Z]\s*=/.test(s)
}

export function MathAnswer({ text, fallback = '—', size }: {
  text?: string | null; fallback?: string; size?: string | number
}) {
  const value = (text || '').trim()
  if (!value) return <Box component="span" fz={size}>{fallback}</Box>
  const unwrapped = unwrapMath(value)
  if (unwrapped != null) {
    return (
      <Box component="span" fz={size} style={{ whiteSpace: 'pre-wrap' }}>
        <MathSpan latex={unwrapped} />
      </Box>
    )
  }
  if (/\$|\\\(|\\\[/.test(value)) return <MathText text={value} size={size} />
  if (looksLikeBareLatex(value)) {
    return <Box component="span" fz={size}><MathSpan latex={value} /></Box>
  }
  return <MathText text={value} size={size} />
}

/** Énoncé : texte + formules KaTeX intercalés. La taille de police est
 *  UNIFORME (pas de mise en valeur automatique après un « : ») — l'ancienne
 *  heuristique agrandissait arbitrairement l'après-deux-points, ce qui donnait
 *  des tailles incohérentes d'une ligne à l'autre. Le seul agrandissement
 *  légitime (lignes portant une case à remplir) est décidé par l'appelant. */
// Marqueur de PLACEMENT d'image ({{figure}}, cf. backend statement.py) : il ne
// s'affiche JAMAIS en texte. L'aperçu de l'onglet Exercices l'intercepte pour y
// poser la figure (StatementPreview) ; partout ailleurs (banque, sujets), on
// retire simplement sa ligne pour qu'il ne fuite pas dans le rendu.
const FIGURE_LINE_RE = /^[ \t]*\{\{figure\}\}[ \t]*\n?/gm

export default function MathText({ text, centered = false, size }: {
  text: string; centered?: boolean; size?: string | number
}) {
  // Le GRAS se résout AVANT les formules (comme à l'impression, cf. backend
  // services/blocks et pdfgen._paragraph_segs) : « **Prix : $3$ €** » a ses deux
  // marques de part et d'autre d'un span, et les chercher après le découpage
  // mathématique les laisserait orphelines — donc affichées telles quelles.
  const chunks = useMemo(
    () => splitBold((text || '').replace(FIGURE_LINE_RE, '')), [text])
  const elements = chunks.flatMap(([chunk, bold], c) => {
    const inner = splitMathSpans(chunk).map(([content, isMath], i) =>
      isMath ? <MathSpan key={`${c}-${i}`} latex={content} />
        : <TextSpan key={`${c}-${i}`} content={content} />)
    return bold
      ? [<strong key={`b${c}`} style={{ fontWeight: 700 }}>{inner}</strong>]
      : inner
  })
  if (centered)
    return <Box fz={size} ta="center" style={{ whiteSpace: 'pre-wrap' }}>{elements}</Box>
  return <Box component="span" fz={size} style={{ whiteSpace: 'pre-wrap' }}>{elements}</Box>
}

export { splitMathSpans, MathSpan }
