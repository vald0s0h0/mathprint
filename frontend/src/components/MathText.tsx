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

// Case de réponse insérée dans le fil du texte (cf. backend services/statement.py).
// Le PDF l'imprime en case à remplir ; l'aperçu web doit faire pareil, sinon le
// marqueur littéral « {{blank}} » s'affiche tel quel dans la banque et les aperçus.
const BLANK_TOKEN = '{{blank}}'

/** Case de réponse vide, dessinée en ligne à la place du marqueur {{blank}}. */
function BlankBox() {
  return (
    <Box component="span" aria-label="case à remplir" style={{
      display: 'inline-block', width: '2.6em', height: '1.05em',
      border: '1px solid var(--mantine-color-gray-5)', borderRadius: 2,
      margin: '0 0.12em', verticalAlign: '-0.18em',
    }} />
  )
}

/** Texte brut (hors formule) pouvant contenir des marqueurs {{blank}} : chaque
 *  marqueur devient une case à remplir, le reste est rendu tel quel. */
function TextSpan({ content }: { content: string }) {
  if (!content.includes(BLANK_TOKEN)) return <span>{content}</span>
  const parts = content.split(BLANK_TOKEN)
  return (
    <>
      {parts.map((part, i) => (
        <React.Fragment key={i}>
          {part && <span>{part}</span>}
          {i < parts.length - 1 && <BlankBox />}
        </React.Fragment>
      ))}
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
      <TextSpan key={i} content={content} />
    )
  )

  // Heuristique optionnelle : si le texte contient ":", proposer une mise en valeur
  // (facultatif — conserver pour compatibilité avec l'ancienne UI, mais elle n'est plus
  // nécessaire puisque le balisage LaTeX est explicite).
  // Réservée aux textes d'UNE ligne, seuls pour lesquels elle a été écrite : sur un
  // énoncé mis en lignes, le premier ":" est celui d'une énumération, et « tout ce qui
  // suit » est alors le corps de l'énoncé — pas une expression à mettre en valeur.
  const singleLine = !text.includes('\n')
  const colonIdx = singleLine ? text.indexOf(':') : -1
  const afterColon = colonIdx >= 0 ? text.slice(colonIdx + 1).trim() : ''
  const splittable = colonIdx >= 0 && afterColon.length > 0 && afterColon.length < 80

  if (splittable && afterColon.split('$').some(s => /\d/.test(s))) {
    const beforeColon = text.slice(0, colonIdx)
    const afterSpans = splitMathSpans(afterColon)
    const afterElements = afterSpans.map(([content, isMath], i) =>
      isMath ? (
        <MathSpan key={i} latex={content} />
      ) : (
        <TextSpan key={i} content={content} />
      )
    )

    return (
      <Box fz={size} style={{ whiteSpace: 'pre-wrap' }}>
        <Box component="span"><TextSpan content={beforeColon} /> :</Box>
        <Box mt={4} ta={centered ? 'center' : 'left'}
          fz="1.25em" fw={500} style={{ letterSpacing: '0.02em' }}>
          {afterElements}
        </Box>
      </Box>
    )
  }

  return <Box component="span" fz={size} style={{ whiteSpace: 'pre-wrap' }}>{elements}</Box>
}

export { splitMathSpans, MathSpan }
