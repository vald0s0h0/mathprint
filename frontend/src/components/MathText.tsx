// Rendu fiable des formules mathématiques via KaTeX.
// Découpe sur $...$ (spans délimitant du LaTeX), renderise via KaTeX côté web.
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

/** Énoncé complet : consigne + expression mise en valeur, centrée. */
export default function MathText({ text, centered = false, size }: {
  text: string; centered?: boolean; size?: string | number
}) {
  const spans = useMemo(() => splitMathSpans(text), [text])

  // Rendre tous les spans (maths + texte intercalés)
  const elements = spans.map(([content, isMath], i) =>
    isMath ? (
      <MathSpan key={i} latex={content} />
    ) : (
      <span key={i}>{content}</span>
    )
  )

  // Heuristique optionnelle : si le texte contient ":", proposer une mise en valeur
  // (facultatif — conserver pour compatibilité avec l'ancienne UI, mais elle n'est plus
  // nécessaire puisque le balisage LaTeX est explicite)
  const colonIdx = text.indexOf(':')
  const afterColon = colonIdx >= 0 ? text.slice(colonIdx + 1).trim() : ''
  const splittable = colonIdx >= 0 && afterColon.length > 0 && afterColon.length < 80

  if (splittable && afterColon.split('$').some(s => /\d/.test(s))) {
    const beforeColon = text.slice(0, colonIdx)
    const afterSpans = splitMathSpans(afterColon)
    const afterElements = afterSpans.map(([content, isMath], i) =>
      isMath ? (
        <MathSpan key={i} latex={content} />
      ) : (
        <span key={i}>{content}</span>
      )
    )

    return (
      <Box fz={size}>
        <Box component="span">{beforeColon} :</Box>
        <Box mt={4} ta={centered ? 'center' : 'left'}
          fz="1.25em" fw={500} style={{ letterSpacing: '0.02em' }}>
          {afterElements}
        </Box>
      </Box>
    )
  }

  return <Box component="span" fz={size}>{elements}</Box>
}

export { splitMathSpans, MathSpan }
