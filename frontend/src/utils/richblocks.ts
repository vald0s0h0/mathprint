// Blocs de PRÉSENTATION d'un énoncé — miroir exact de backend services/blocks.py.
//
// Trois lignes d'un énoncé ne se lisent pas comme des phrases et ne doivent pas
// se mettre en page comme telles :
// - TABLEAU : l'extraction recopie les tableaux de données du manuel en
//   Markdown (« | Effectif | 10 | 14 | ») ; rendus au fil du texte, ils
//   s'affichent en bouillie de barres verticales ;
// - SÉRIE : une liste de valeurs (« 10 W 8 W 6 W 10 W … ») se recolle au fil du
//   texte et l'élève ne voit plus où une valeur finit ; c'est une grille sans
//   filets ;
// - TEXTE : tout le reste, une ligne logique par bloc.
//
// L'aperçu de l'écran doit montrer la feuille qui sortira de l'imprimante : ce
// fichier et son homologue Python appliquent donc les MÊMES règles. Toute
// évolution de l'un se reporte sur l'autre (cf. blocks.py, qui porte le
// commentaire de référence).

export type RichBlock =
  | { kind: 'text'; text: string }
  | { kind: 'table'; rows: string[][]; header: boolean }
  | { kind: 'series'; items: string[]; label: string | null }

// ------------------------------------------------------------------- gras
// Seul balisage de CARACTÈRE admis. Non gourmand, et le contenu commence et
// finit par un caractère visible : « **a** et **b** » fait deux gras, pas un
// seul, et « 3 ** 4 ** 5 » n'en fait aucun.
const BOLD_RE = /\*\*(\S|\S[\s\S]*?\S)\*\*/g

/** Découpe un texte en segments [contenu, gras]. Les `**` disparaissent. */
export function splitBold(text: string): Array<[string, boolean]> {
  const out: Array<[string, boolean]> = []
  let pos = 0
  const re = new RegExp(BOLD_RE.source, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(text || '')) !== null) {
    if (m.index > pos) out.push([text.slice(pos, m.index), false])
    out.push([m[1], true])
    pos = m.index + m[0].length
  }
  if (pos < (text || '').length) out.push([text.slice(pos), false])
  return out.length ? out : [['', false]]
}

export const stripBold = (text: string) =>
  splitBold(text).map(([part]) => part).join('')

// ---------------------------------------------------------------- tableaux
const SEPARATOR_CELL = /^:?-{2,}:?$/

/** Cellules d'une ligne Markdown. Les `|` d'une formule ($|x|$) ne coupent
 *  pas : on ne découpe qu'en dehors des spans `$...$`. */
function splitCells(line: string): string[] {
  const cells: string[] = []
  let cur = ''
  let inMath = false
  for (const ch of line.trim()) {
    if (ch === '$') { inMath = !inMath; cur += ch }
    else if (ch === '|' && !inMath) { cells.push(cur.trim()); cur = '' }
    else cur += ch
  }
  cells.push(cur.trim())
  if (cells.length && !cells[0]) cells.shift()
  if (cells.length && !cells[cells.length - 1]) cells.pop()
  return cells
}

const isTableLine = (line: string) => {
  const s = (line || '').trim()
  return s.startsWith('|') && (s.match(/\|/g) || []).length >= 2
}

const isSeparator = (cells: string[]) =>
  cells.length > 0 && cells.every((c) => SEPARATOR_CELL.test(c))

function tableBlock(lines: string[]): RichBlock | null {
  let rows = lines.map(splitCells)
  const header = rows.length > 1 && isSeparator(rows[1])
  rows = rows.filter((r) => !isSeparator(r))
  const width = rows.reduce((m, r) => Math.max(m, r.length), 0)
  if (rows.length < 2 || width < 2) return null
  return {
    kind: 'table', header,
    rows: rows.map((r) => [...r, ...Array(width - r.length).fill('')]),
  }
}

// ------------------------------------------------------------------ séries
const NUMBER = String.raw`[-+]?\d+(?:[.,]\d+)?(?:\s*%)?`
const UNIT = String.raw`(?:%|€|°[CF]?|\$?[A-Za-zµΩ]{1,4}(?:\/[A-Za-zµΩ]{1,4})?)`
const VALUE_RE = new RegExp(String.raw`^(\$[^$\n]+\$|${NUMBER})(?:[ \t]*(${UNIT}))?`)
const GAP_RE = /^(?:[ \t]*[;,·•/][ \t]*|[ \t]+)/
// Mots courts qui ressemblent à une unité mais relient deux valeurs : sans ce
// garde-fou, « 12, 18, 21 et 25 » se lirait comme une série d'unité « et ».
const NOT_UNITS = new Set(['et', 'ou', 'a', 'de', 'du', 'des', 'la', 'le', 'au',
  'aux', 'en', 'puis', 'sur', 'par', 'que', 'qui', 'un', 'une'])
export const SERIES_MIN_ITEMS = 4
const SERIES_MAX_ITEM_LEN = 14

/** Items d'une ligne qui n'est QU'une suite de valeurs, sinon null. La ligne
 *  doit être consommée en ENTIER (au point final près) : c'est ce qui empêche
 *  une phrase contenant des nombres de passer pour une série. */
export function parseSeries(line: string): string[] | null {
  const s = (line || '').trim().replace(/\.+$/, '').trim()
  if (!s || s.includes('{{')) return null
  const items: string[] = []
  const units: string[] = []
  let rest = s
  while (rest.length) {
    const m = VALUE_RE.exec(rest)
    if (!m) return null
    const unit = (m[2] || '').trim()
    if (unit && NOT_UNITS.has(unit.toLowerCase())) return null
    const item = m[0].trim()
    if (item.length > SERIES_MAX_ITEM_LEN) return null
    items.push(item)
    units.push(unit)
    rest = rest.slice(m[0].length)
    if (!rest.length) break
    const gap = GAP_RE.exec(rest)
    if (!gap || !gap[0].length) return null
    rest = rest.slice(gap[0].length)
  }
  if (items.length < SERIES_MIN_ITEMS) return null
  const marked = units.filter(Boolean)
  if (marked.length && (marked.length !== units.length || new Set(marked).size !== 1)) return null
  return items
}

// Étiquette de sous-question en tête de ligne (miroir de SUBQUESTION_RE côté
// backend) : elle voyage avec le bloc, pour rester une pastille.
const SUBLABEL_RE = /^([a-h]|\d{1,2})\s*[.)]\s+(?=\S)/

function seriesBlocks(line: string): RichBlock[] | null {
  let label: string | null = null
  let body = line
  const m = SUBLABEL_RE.exec(line)
  if (m) { label = m[1]; body = line.slice(m[0].length) }
  const direct = parseSeries(body)
  if (direct) return [{ kind: 'series', items: direct, label }]
  const cut = body.lastIndexOf(':')
  if (cut <= 0) return null
  const head = body.slice(0, cut).trim()
  const items = parseSeries(body.slice(cut + 1))
  if (!head || !items) return null
  const lead = label ? `${label}. ${head} :` : `${head} :`
  return [{ kind: 'text', text: lead }, { kind: 'series', items, label: null }]
}

// ------------------------------------------------------------------- blocs
/** Découpe un énoncé en blocs de présentation. Une ligne qui ne relève d'aucun
 *  cas particulier ressort telle quelle en bloc « text ». */
export function parseBlocks(text: string): RichBlock[] {
  const out: RichBlock[] = []
  let pending: string[] = []
  const flush = () => {
    if (!pending.length) return
    const block = pending.length >= 2 ? tableBlock(pending) : null
    if (block) out.push(block)
    else pending.forEach((ln) => out.push({ kind: 'text', text: ln }))
    pending = []
  }
  for (const line of (text || '').split('\n')) {
    if (isTableLine(line)) { pending.push(line); continue }
    flush()
    const series = seriesBlocks(line)
    if (series) out.push(...series)
    else out.push({ kind: 'text', text: line })
  }
  flush()
  return out
}
