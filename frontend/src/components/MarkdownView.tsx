// Rendu Markdown des consignes et contextes de la forge (page admin « Forge »).
// Volontairement réduit à ce que ces fichiers emploient : titres, paragraphes, listes,
// tableaux, blocs de code, citations, gras, italique, code en ligne, liens et formules
// `$…$`. Aucun HTML brut n'est interprété : tout passe par React, donc s'échappe.
import { Anchor, Box, Code, List, Table, Text, Title } from '@mantine/core'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { Fragment, type ReactNode } from 'react'

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'para'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'table'; header: string[]; rows: string[][] }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'quote'; text: string }
  | { kind: 'rule' }

const LIST_RE = /^(\s*)([-*+]|\d+\.)\s+(.*)$/

function cells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  const out: string[] = []
  let current = ''
  let inCode = false
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i]
    if (ch === '\\' && trimmed[i + 1] === '|') { current += '|'; i++; continue }
    if (ch === '`') inCode = !inCode
    if (ch === '|' && !inCode) { out.push(current.trim()); current = ''; continue }
    current += ch
  }
  out.push(current.trim())
  return out
}

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    const fence = line.match(/^(`{3,})\s*(.*)$/)
    if (fence) {
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith(fence[1])) body.push(lines[i++])
      i++
      blocks.push({ kind: 'code', lang: fence[2].trim(), text: body.join('\n') })
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) { blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2] }); i++; continue }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { blocks.push({ kind: 'rule' }); i++; continue }
    if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const header = cells(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]))
      blocks.push({ kind: 'table', header, rows })
      continue
    }
    if (line.startsWith('>')) {
      const body: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) body.push(lines[i++].replace(/^>\s?/, ''))
      blocks.push({ kind: 'quote', text: body.join(' ') })
      continue
    }
    const item = line.match(LIST_RE)
    if (item) {
      const ordered = /\d/.test(item[2])
      const items: string[] = []
      while (i < lines.length) {
        const m = lines[i].match(LIST_RE)
        if (m && m[1].length < 2) { items.push(m[3]); i++; continue }
        // suite d'un élément : ligne indentée, ou sous-liste rendue dans le même élément
        if (lines[i].trim() && /^\s+/.test(lines[i]) && items.length) {
          items[items.length - 1] += ' ' + lines[i].trim().replace(LIST_RE, '· $3'); i++; continue
        }
        break
      }
      blocks.push({ kind: 'list', ordered, items })
      continue
    }
    const body: string[] = []
    while (i < lines.length && lines[i].trim() && !lines[i].match(/^(#{1,6}\s|`{3}|>|\s*\|)/) && !lines[i].match(LIST_RE)) {
      body.push(lines[i++])
    }
    blocks.push({ kind: 'para', text: body.join(' ') })
  }
  return blocks
}

function math(latex: string, key: number): ReactNode {
  try {
    return <span key={key} dangerouslySetInnerHTML={{ __html: katex.renderToString(latex, { throwOnError: false }) }} />
  } catch {
    return <code key={key}>{latex}</code>
  }
}

/** Gras, italique, code, liens et formules, sans HTML brut. */
export function Inline({ text }: { text: string }) {
  const out: ReactNode[] = []
  const re = /(`[^`]+`)|(\$\$[^$]+\$\$|\$[^$\n]+\$)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g
  let last = 0
  let k = 0
  for (const m of text.matchAll(re)) {
    if (m.index! > last) out.push(<Fragment key={k++}>{text.slice(last, m.index)}</Fragment>)
    const token = m[0]
    if (m[1]) out.push(<Code key={k++}>{token.slice(1, -1)}</Code>)
    else if (m[2]) out.push(math(token.replace(/^\$\$?|\$\$?$/g, ''), k++))
    else if (m[3]) out.push(<strong key={k++}><Inline text={token.slice(2, -2)} /></strong>)
    else if (m[4]) {
      const [, label, href] = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/)!
      out.push(/^https?:/.test(href)
        ? <Anchor key={k++} href={href} target="_blank" rel="noreferrer">{label}</Anchor>
        : <Code key={k++}>{label}</Code>)
    } else out.push(<em key={k++}><Inline text={token.slice(1, -1)} /></em>)
    last = m.index! + token.length
  }
  if (last < text.length) out.push(<Fragment key={k++}>{text.slice(last)}</Fragment>)
  return <>{out}</>
}

export default function MarkdownView({ source }: { source: string }) {
  const blocks = parseMarkdown(source)
  return <Box style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: '78ch', lineHeight: 1.55 }}>
    {blocks.map((b, i) => {
      switch (b.kind) {
        case 'heading':
          return <Title key={i} order={Math.min(6, b.level + 1) as 2 | 3 | 4 | 5 | 6} mt={b.level <= 2 ? 8 : 4}>
            <Inline text={b.text} /></Title>
        case 'para':
          return <Text key={i} size="sm"><Inline text={b.text} /></Text>
        case 'list':
          return <List key={i} type={b.ordered ? 'ordered' : 'unordered'} size="sm" spacing={4}>
            {b.items.map((it, j) => <List.Item key={j}><Inline text={it} /></List.Item>)}</List>
        case 'table':
          return <Box key={i} style={{ overflowX: 'auto' }}>
            <Table withTableBorder withColumnBorders striped fz="xs" style={{ minWidth: 480 }}>
              <Table.Thead><Table.Tr>{b.header.map((h, j) => <Table.Th key={j}><Inline text={h} /></Table.Th>)}</Table.Tr></Table.Thead>
              <Table.Tbody>{b.rows.map((r, j) => <Table.Tr key={j}>{r.map((c, n) => <Table.Td key={n}><Inline text={c} /></Table.Td>)}</Table.Tr>)}</Table.Tbody>
            </Table></Box>
        case 'code':
          return <Code key={i} block style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{b.text}</Code>
        case 'quote':
          return <Box key={i} pl="sm" style={{ borderLeft: '3px solid var(--mantine-color-gray-4)' }}>
            <Text size="sm" c="dimmed"><Inline text={b.text} /></Text></Box>
        case 'rule':
          return <Box key={i} style={{ borderTop: '1px solid var(--mantine-color-gray-3)' }} />
      }
      return null
    })}
  </Box>
}
