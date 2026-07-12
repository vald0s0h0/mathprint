// Rendu lisible des énoncés mathématiques côté web : fractions empilées,
// signe ×, exposants, flèches — miroir du rendu PDF (pdfgen._statement_layout).
import { Box } from '@mantine/core'
import React from 'react'

const FRAC_RE = /(?<![\w/])(\d+)\s*\/\s*(\d+)(?![\w/])/g

function normalize(text: string): string {
  return text
    .replace(/\*\*2|\^2/g, '²')
    .replace(/\*\*3|\^3/g, '³')
    .replace(/\*/g, '×')
    .replace(/->/g, '→')
}

function Frac({ n, d }: { n: string; d: string }) {
  return (
    <span style={{
      display: 'inline-flex', flexDirection: 'column', alignItems: 'center',
      verticalAlign: 'middle', margin: '0 0.15em', lineHeight: 1.1,
    }}>
      <span style={{ fontSize: '0.85em', padding: '0 0.25em' }}>{n}</span>
      <span style={{
        borderTop: '1.5px solid currentColor', fontSize: '0.85em',
        padding: '0 0.25em', width: '100%', textAlign: 'center',
      }}>{d}</span>
    </span>
  )
}

export function renderMath(text: string): React.ReactNode[] {
  const t = normalize(text)
  const out: React.ReactNode[] = []
  let last = 0
  let key = 0
  for (const m of t.matchAll(FRAC_RE)) {
    const i = m.index ?? 0
    if (i > last) out.push(<span key={key++}>{t.slice(last, i)}</span>)
    out.push(<Frac key={key++} n={m[1]} d={m[2]} />)
    last = i + m[0].length
  }
  if (last < t.length) out.push(<span key={key++}>{t.slice(last)}</span>)
  return out
}

/** Énoncé complet : consigne + expression mise en valeur, centrée. */
export default function MathText({ text, centered = false, size }: {
  text: string; centered?: boolean; size?: string | number
}) {
  // même heuristique que le PDF : « consigne : expression »
  const idx = text.indexOf(':')
  const tail = idx >= 0 ? text.slice(idx + 1).trim() : ''
  const splittable = idx >= 0 && tail.length > 0 && tail.length < 80 && /\d/.test(tail)

  if (!splittable) {
    return <Box component="span" fz={size}>{renderMath(text)}</Box>
  }
  return (
    <Box fz={size}>
      <Box component="span">{text.slice(0, idx).trim()} :</Box>
      <Box mt={4} ta={centered ? 'center' : 'left'}
        fz="1.25em" fw={500} style={{ letterSpacing: '0.02em' }}>
        {renderMath(tail)}
      </Box>
    </Box>
  )
}
