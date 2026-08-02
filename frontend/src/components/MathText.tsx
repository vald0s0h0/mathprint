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

/** Split text on $...$ délimiteurs. Returns [(content, isMath), ...] */
function splitMathSpans(text: string): Array<[string, boolean]> {
  const spans: Array<[string, boolean]> = []
  let pos = 0
  while (true) {
    const start = text.indexOf('$', pos)
    if (start === -1) {
      if (pos < text.length) spans.push([text.slice(pos), false])
      break
    }
    if (start > pos) spans.push([text.slice(pos, start), false])

    const end = text.indexOf('$', start + 1)
    if (end === -1) {
      spans.push([text.slice(start), false])
      break
    }
    const mathContent = text.slice(start + 1, end)
    if (mathContent) spans.push([mathContent, true])
    pos = end + 1
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
    display: 'inline-block', height: kind === 'mini' ? '0.95em' : '1.05em',
    border: '1px solid var(--mantine-color-gray-5)', borderRadius: 2,
    margin: '0 0.12em', verticalAlign: '-0.18em',
  } as const
  if (kind === 'right')
    return <Box component="span" aria-label="case à remplir" style={{ ...base, minWidth: '3em', width: '55%' }} />
  if (kind === 'mini')
    return <Box component="span" aria-label="mini-case" style={{ ...base, width: '1.3em' }} />
  return <Box component="span" aria-label="case à remplir" style={{ ...base, width: '2.6em' }} />
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

/** Rendu d'un span LaTeX, fallback texte brut si erreur (ne devrait jamais arriver). */
function MathSpan({ latex }: { latex: string }) {
  try {
    const html = katex.renderToString(latex, { throwOnError: false })
    return <span dangerouslySetInnerHTML={{ __html: html }} />
  } catch (_) {
    // Fallback : afficher le LaTeX brut ou texte sûr
    return <span>{latex}</span>
  }
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
  const spans = useMemo(() => splitMathSpans((text || '').replace(FIGURE_LINE_RE, '')), [text])
  const elements = spans.map(([content, isMath], i) =>
    isMath ? <MathSpan key={i} latex={content} /> : <TextSpan key={i} content={content} />
  )
  if (centered)
    return <Box fz={size} ta="center" style={{ whiteSpace: 'pre-wrap' }}>{elements}</Box>
  return <Box component="span" fz={size} style={{ whiteSpace: 'pre-wrap' }}>{elements}</Box>
}

export { splitMathSpans, MathSpan }
