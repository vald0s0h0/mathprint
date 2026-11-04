// Moteur de composition de l'assistant : ces cartes tiennent-elles dans ces pages ?
//
// Miroir exact de backend services/sheet_engine.pack_best et pdfgen.pack_columns,
// pour griser les cartes à chaque clic sans aller-retour réseau. Le serveur refait
// le calcul à l'enregistrement ; test_sheet_engine vérifie que les deux donnent le
// même résultat. Aucune dépendance ici : le test exécute ce fichier avec node.

export type Capacities = { first: number; rest: number }
export type PackCard = { height: number; tier: number }

const SEARCH_BUDGET = 4000
const FULL_ENOUGH_PT = 0.5
const PROBLEM_TIER = 10

const columnCapacity = (caps: Capacities, b: number) => (b < 2 ? caps.first : caps.rest)
const crossesProblems = (lower: number, upper: number) => lower < PROBLEM_TIER && upper >= PROBLEM_TIER
const sortedTiers = (cards: PackCard[]) => [...new Set(cards.map((c) => c.tier))].sort((a, b) => a - b)
const byTier = (cards: PackCard[], col: number[]) =>
  col.map((i, at) => ({ i, at })).sort((x, y) => cards[x.i].tier - cards[y.i].tier || x.at - y.at).map((x) => x.i)

function packColumns(cards: PackCard[], caps: Capacities): number[][] {
  const cols: number[][] = []
  const used: number[] = []
  let frontier = 0
  for (const tier of sortedTiers(cards)) {
    const members = cards.map((_, i) => i).filter((i) => cards[i].tier === tier)
      .sort((a, b) => cards[b].height - cards[a].height || a - b)
    let last = frontier
    for (const i of members) {
      const h = cards[i].height
      let placed = false
      for (let b = frontier; b < cols.length; b++) {
        if (used[b] + h <= columnCapacity(caps, b)) {
          cols[b].push(i)
          used[b] += h
          last = Math.max(last, b)
          placed = true
          break
        }
      }
      if (!placed) {
        while (h > columnCapacity(caps, cols.length) && h <= caps.rest) {
          cols.push([])
          used.push(0)
        }
        cols.push([i])
        used.push(h)
        last = Math.max(last, cols.length - 1)
      }
    }
    frontier = last
  }
  return cols.map((col) => byTier(cards, col))
}

function bestSubset(items: number[], cards: PackCard[], room: number): number[] {
  const suffix = new Array<number>(items.length + 1).fill(0)
  for (let i = items.length - 1; i >= 0; i--) suffix[i] = suffix[i + 1] + cards[items[i]].height
  let best: number[] = []
  let bestSum = 0
  let nodes = 0
  const picked: number[] = []
  const go = (i: number, total: number) => {
    if (total > bestSum) { bestSum = total; best = [...picked] }
    if (i >= items.length || nodes >= SEARCH_BUDGET
      || bestSum >= room - FULL_ENOUGH_PT || total + suffix[i] <= bestSum) return
    nodes += 1
    const h = cards[items[i]].height
    if (total + h <= room) {
      picked.push(items[i])
      go(i + 1, total + h)
      picked.pop()
    }
    go(i + 1, total)
  }
  go(0, 0)
  return best
}

function packBySlack(cards: PackCard[], caps: Capacities, nColumns: number): number[][] | null {
  const levels = sortedTiers(cards)
  const groups = levels.map((t) => cards.map((_, i) => i).filter((i) => cards[i].tier === t)
    .sort((a, b) => cards[b].height - cards[a].height || a - b))
  let g = 0
  const columns: number[][] = []
  for (let b = 0; b < nColumns; b++) {
    let room = columnCapacity(caps, b)
    const column: number[] = []
    while (g < groups.length) {
      const chosen = bestSubset(groups[g], cards, room)
      column.push(...chosen)
      room -= chosen.reduce((sum, i) => sum + cards[i].height, 0)
      groups[g] = groups[g].filter((i) => !chosen.includes(i))
      if (groups[g].length) break
      g += 1
    }
    if (g + 1 < groups.length && groups[g].length === 1 && !crossesProblems(levels[g], levels[g + 1])) {
      const filler = groups[g + 1].find((i) => cards[i].height <= room)
      if (filler !== undefined) {
        column.push(filler)
        groups[g + 1] = groups[g + 1].filter((i) => i !== filler)
      }
    }
    columns.push(column)
    if (groups.every((group) => group.length === 0)) return columns
  }
  return null
}

/** Colonnes (index des cartes, ordre de lecture) qui font tenir ces cartes dans
 *  `pages` pages, ou null si c'est impossible. */
export function packBest(cards: PackCard[], caps: Capacities, pages: number): number[][] | null {
  if (!cards.length) return []
  const nColumns = 2 * Math.max(1, pages)
  if (Math.max(...cards.map((c) => c.height)) > caps.rest) return null
  const columns = packColumns(cards, caps)
  if (columns.length <= nColumns) return columns
  const packed = packBySlack(cards, caps, nColumns)
  return packed && packed.map((col) => byTier(cards, col))
}

/** Place totale, en points, des colonnes de `pages` pages. */
export function totalCapacity(caps: Capacities, pages: number): number {
  let total = 0
  for (let b = 0; b < 2 * Math.max(1, pages); b++) total += columnCapacity(caps, b)
  return total
}

/** Part remplie de chaque page, dans l'ordre d'impression. */
export function pageFill(columns: number[][], cards: PackCard[], caps: Capacities, pages: number): number[] {
  return Array.from({ length: Math.max(1, pages) }, (_, p) => {
    const used = [columns[2 * p] ?? [], columns[2 * p + 1] ?? []].flat()
      .reduce((sum, i) => sum + cards[i].height, 0)
    return used / (2 * columnCapacity(caps, 2 * p))
  })
}
