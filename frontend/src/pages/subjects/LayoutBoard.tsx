// Étape « Mise en page » de l'assistant « Créer mon sujet » : à gauche les
// exercices disponibles, à droite les pages vierges du sujet où le professeur
// les dépose.
//
// Deux principes :
//  1. La page dessinée ici est à l'ÉCHELLE de la page imprimée. Les hauteurs de
//     carte (height_pt) et la hauteur utile des colonnes (metrics.column_h)
//     viennent du moteur PDF lui-même (backend services/pdfgen), jamais d'une
//     estimation refaite ici : ce que le professeur voit se remplir est
//     exactement ce qui sortira de l'imprimante.
//  2. Exercices et PROBLÈMES sont deux onglets distincts. Un exercice appartient
//     à une compétence ; un problème appartient à un CHAPITRE entier — il est
//     donc proposé dès qu'une seule compétence de son chapitre est cochée.
import {
  ActionIcon, Alert, Badge, Box, Button, Group, ScrollArea,
  SegmentedControl, Stack, Text, TextInput, Tooltip,
} from '@mantine/core'
import {
  AlertTriangle, ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Calculator,
  GripVertical, Image as ImageIcon, Search, Sparkles, Trash2, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import ExercisePrintPreview from '../../components/ExercisePrintPreview'
import MathText from '../../components/MathText'

export type PoolItem = {
  id: string; competency_id: string; competency_label: string
  chapter_code: string; chapter_name: string
  statement: string; response_type: string; difficulty: number
  expected: Record<string, any>; grading: Record<string, any>; choices: string[]
  row_labels: string[] | null; col_labels: string[] | null; lines: number | null
  figure: Record<string, any> | null; figure_url: string | null
  correction_solution: string; correction_guide: string
  kind: string; source: string; badge_type: string; title: string
  calculator: string; source_number: string; has_figure: boolean
  is_problem: boolean
  bareme_points: number; height_pt: number; height_pt_no_guide: number
}
export type Metrics = {
  page_w: number; page_h: number; col_w: number; col_gap: number
  margin: number; gap: number; cols_per_page: number; column_h: number[]
}
/** layout[page][colonne] = ids d'exercices, de haut en bas. */
export type Layout = string[][][]

export function emptyLayout(pages: number): Layout {
  return Array.from({ length: pages }, () => [[], []] as string[][])
}

/** Redimensionne un plan quand le nombre de pages change (contenu conservé). */
export function resizeLayout(layout: Layout, pages: number): Layout {
  const out = emptyLayout(pages)
  for (let p = 0; p < Math.min(pages, layout.length); p++) {
    out[p] = [[...(layout[p]?.[0] ?? [])], [...(layout[p]?.[1] ?? [])]]
  }
  return out
}

export function layoutCount(layout: Layout): number {
  return layout.reduce((n, page) => n + page.reduce((m, col) => m + col.length, 0), 0)
}

/** Hauteur d'une carte selon la politique de guide retenue pour le sujet. */
export function cardHeight(it: PoolItem | undefined, guides: string): number {
  if (!it) return 0
  return guides === 'none' ? it.height_pt_no_guide : it.height_pt
}

/** Colonnes trop chargées (« page 2, colonne droite »), pour prévenir avant de
 *  générer : le moteur PDF fait glisser les cartes en trop dans la colonne
 *  suivante — la feuille imprimée ne serait plus celle qui a été composée. */
export function overfullColumns(layout: Layout, byId: Map<string, PoolItem>,
                                metrics: Metrics, guides: string): string[] {
  const out: string[] = []
  layout.forEach((page, p) => page.forEach((col, c) => {
    const used = col.reduce((s, id) => s + cardHeight(byId.get(id), guides), 0)
    if (used > (metrics.column_h[p] ?? metrics.column_h[0])) {
      out.push(`page ${p + 1}, colonne ${c === 0 ? 'gauche' : 'droite'}`)
    }
  }))
  return out
}

const DIFFICULTY_COLOR = ['gray', 'teal', 'green', 'yellow', 'orange', 'red']
const FILL_TRIGGER_RATIO = 0.4
const RESPONSE_LABEL: Record<string, string> = {
  qcm_single: 'QCM', qcm_multiple: 'QCM multiple', short_text: 'réponse courte',
  multiline_text: 'rédaction', table_fill: 'tableau', matching: 'relier',
  manual_drawing: 'tracé', multi_blank: 'phrases à trous',
  checkbox_grid: 'grille', composite: 'mixte',
}

function ItemChips({ it }: { it: PoolItem }) {
  return (
    <Group gap={4} wrap="wrap">
      <Badge size="xs" variant="light" color={DIFFICULTY_COLOR[it.difficulty] ?? 'gray'}>
        niv. {it.difficulty}
      </Badge>
      <Badge size="xs" variant="outline" color="gray">
        {RESPONSE_LABEL[it.response_type] ?? it.response_type}
      </Badge>
      <Badge size="xs" variant="outline" color="gray">{it.bareme_points} pt</Badge>
      {it.has_figure && (
        <Tooltip label="Contient une figure"><ImageIcon size={12} opacity={0.6} /></Tooltip>
      )}
      {it.calculator !== 'autorisee' && (
        <Tooltip label={it.calculator === 'interdite' ? 'Calculatrice interdite'
          : 'Calculatrice nécessaire'}>
          <Calculator size={12} opacity={0.6} />
        </Tooltip>
      )}
    </Group>
  )
}

// ------------------------------------------------------------------ le pool

function PoolCard({ it, used, fillCandidate, onDragStart }: {
  it: PoolItem; used: boolean; fillCandidate: boolean; onDragStart: () => void
}) {
  // Un exercice déjà posé dans CETTE variante n'est plus attrapable : un élève
  // ne doit jamais voir deux fois le même exercice sur sa feuille (même règle
  // que la distribution automatique). Il reste évidemment disponible pour les
  // AUTRES variantes, qui ont chacune leur plan.
  return (
    <Box p={6} draggable={!used} onDragStart={onDragStart}
      className={fillCandidate ? 'manual-fill-pulse' : undefined}
      style={{ cursor: used ? 'default' : 'grab', opacity: used ? 0.5 : 1, borderRadius: 6 }}>
      <ExercisePrintPreview exercise={it}
        color={it.kind === 'probleme' ? 'orange' : 'indigo'}
        beforeFrame={<Text size="xs" c="dimmed" lineClamp={1}>
          {it.source_number && <b>n°{it.source_number} · </b>}{it.competency_label}
        </Text>}
        badges={<ItemChips it={it} />}
        actions={<Group gap={4} wrap="nowrap">
          {used && <Badge size="xs" color="indigo" variant="light">placé</Badge>}
          <GripVertical size={15} opacity={0.45} />
        </Group>} />
    </Box>
  )
}

// --------------------------------------------------------------- les pages

function PlacedCard({ it, h, first, last, canLeft, canRight, onDragStart,
  onDropBefore, onDropAfter, onMove, onRemove }: {
  it: PoolItem | undefined; h: number; first: boolean; last: boolean
  canLeft: boolean; canRight: boolean
  onDragStart: () => void
  onDropBefore: () => void; onDropAfter: () => void
  onMove: (d: 'up' | 'down' | 'left' | 'right') => void; onRemove: () => void
}) {
  const [edge, setEdge] = useState<'top' | 'bottom' | null>(null)
  // les flèches ne s'affichent qu'au survol : sur une carte réduite à
  // l'échelle de la page, une barre d'outils permanente mange l'énoncé.
  const [hover, setHover] = useState(false)
  return (
    <Box
      draggable onDragStart={onDragStart} onDragEnd={() => setEdge(null)}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onDragOver={(e) => {
        e.preventDefault(); e.stopPropagation()
        const r = e.currentTarget.getBoundingClientRect()
        setEdge(e.clientY - r.top < r.height / 2 ? 'top' : 'bottom')
      }}
      onDragLeave={() => setEdge(null)}
      onDrop={(e) => {
        e.preventDefault(); e.stopPropagation()
        if (edge === 'top') onDropBefore(); else onDropAfter()
        setEdge(null)
      }}
      style={{
        height: h, minHeight: 18, cursor: 'grab', position: 'relative',
        border: '1px solid var(--mantine-color-gray-4)',
        borderTop: edge === 'top' ? '3px solid var(--mantine-color-indigo-6)'
          : '1px solid var(--mantine-color-gray-4)',
        borderBottom: edge === 'bottom' ? '3px solid var(--mantine-color-indigo-6)'
          : '1px solid var(--mantine-color-gray-4)',
        borderRadius: 4, background: 'var(--mantine-color-body)',
        padding: '3px 4px', overflow: 'hidden',
      }}>
      <Text fz={8} style={{ lineHeight: 1.08, overflowWrap: 'anywhere' }}>
        {it?.source_number && <b>n°{it.source_number} · </b>}
        {it ? <MathText text={it.statement || it.title} /> : '—'}
      </Text>
      <Group gap={0} style={{
        position: 'absolute', top: 1, right: 1, borderRadius: 4,
        background: 'var(--mantine-color-body)',
        boxShadow: '0 0 3px 3px var(--mantine-color-body)',
        opacity: hover ? 1 : 0, pointerEvents: hover ? 'auto' : 'none',
        transition: 'opacity 120ms',
      }}>
        <ActionIcon size={14} variant="subtle" color="gray" disabled={first}
          onClick={() => onMove('up')} title="Monter"><ArrowUp size={9} /></ActionIcon>
        <ActionIcon size={14} variant="subtle" color="gray" disabled={last}
          onClick={() => onMove('down')} title="Descendre"><ArrowDown size={9} /></ActionIcon>
        <ActionIcon size={14} variant="subtle" color="gray" disabled={!canLeft}
          onClick={() => onMove('left')} title="Colonne/page précédente">
          <ArrowLeft size={9} /></ActionIcon>
        <ActionIcon size={14} variant="subtle" color="gray" disabled={!canRight}
          onClick={() => onMove('right')} title="Colonne/page suivante">
          <ArrowRight size={9} /></ActionIcon>
        <ActionIcon size={14} variant="subtle" color="red" onClick={onRemove}
          title="Retirer"><X size={9} /></ActionIcon>
      </Group>
    </Box>
  )
}

type DragPayload =
  | { from: 'pool'; id: string }
  | { from: 'layout'; page: number; col: number; index: number }

export default function LayoutBoard({
  pool, problems, metrics, layout, onChange, guides, pages,
}: {
  pool: PoolItem[]; problems: PoolItem[]; metrics: Metrics
  layout: Layout; onChange: (l: Layout) => void
  guides: string; pages: number
}) {
  const [tab, setTab] = useState<'exercises' | 'problems'>('exercises')
  const [search, setSearch] = useState('')
  // Une seule colonne pilote le mode « Remplir ». La clé évite qu'un objet
  // recréé à chaque rendu ne relance inutilement les calculs/animations.
  const [fillTarget, setFillTarget] = useState<string | null>(null)
  const drag = useRef<DragPayload | null>(null)

  const byId = useMemo(() => {
    const m = new Map<string, PoolItem>()
    for (const it of [...pool, ...problems]) m.set(it.id, it)
    return m
  }, [pool, problems])

  // hauteur de carte effective : sans guide, la bande de corrigé retombe à son
  // plancher — l'espace récupéré doit se voir tout de suite dans l'aperçu.
  const heightOf = (id: string) => cardHeight(byId.get(id), guides)
  const overfull = overfullColumns(layout, byId, metrics, guides)

  const usage = useMemo(
    () => new Set(layout.flatMap((page) => page.flatMap((col) => col))), [layout])

  /** Exercices encore disponibles qui tiennent entièrement dans la hauteur
   * restante. Même mesure que le PDF (`cardHeight`), guide compris. */
  const fittingIds = (page: number, col: number): Set<string> => {
    const capacity = metrics.column_h[page] ?? metrics.column_h[0]
    const remaining = capacity - usedHeight(page, col)
    return new Set(pool.filter((it) => !usage.has(it.id)
      && cardHeight(it, guides) > 0
      && cardHeight(it, guides) <= remaining + 0.01).map((it) => it.id))
  }

  const activeFillCandidates = useMemo(() => {
    if (!fillTarget) return new Set<string>()
    const [page, col] = fillTarget.split(':').map(Number)
    if (!layout[page]?.[col]) return new Set<string>()
    const capacity = metrics.column_h[page] ?? metrics.column_h[0]
    const used = (layout[page][col] ?? []).reduce((sum, id) => sum + heightOf(id), 0)
    if (capacity <= 0 || used / capacity <= FILL_TRIGGER_RATIO || used >= capacity) {
      return new Set<string>()
    }
    const remaining = capacity - used
    return new Set(pool.filter((it) => !usage.has(it.id)
      && cardHeight(it, guides) > 0
      && cardHeight(it, guides) <= remaining + 0.01).map((it) => it.id))
  }, [fillTarget, layout, metrics, pool, usage, guides, byId])

  // Après chaque dépôt/retrait, la place est recalculée. Dès qu'aucun exercice
  // ne tient plus (ou que la colonne repasse sous 40 %), le mode se ferme seul.
  useEffect(() => {
    if (fillTarget && activeFillCandidates.size === 0) setFillTarget(null)
  }, [fillTarget, activeFillCandidates])

  const filtered = useMemo(() => {
    const src = tab === 'exercises' ? pool : problems
    const q = search.trim().toLowerCase()
    const visible = q ? src.filter((it) => `${it.statement} ${it.title} ${it.competency_label}`
      .toLowerCase().includes(q)) : src
    if (!fillTarget || tab !== 'exercises') return visible
    // Tri stable hors candidats ; les cartes capables de combler le trou sont
    // remontées, de la plus petite à la plus grande.
    return visible.map((it, index) => ({ it, index })).sort((a, b) => {
      const ac = activeFillCandidates.has(a.it.id)
      const bc = activeFillCandidates.has(b.it.id)
      if (ac !== bc) return ac ? -1 : 1
      if (ac && bc) {
        const delta = cardHeight(a.it, guides) - cardHeight(b.it, guides)
        if (delta !== 0) return delta
      }
      return a.index - b.index
    }).map(({ it }) => it)
  }, [tab, pool, problems, search, fillTarget, activeFillCandidates, guides])

  // échelle d'aperçu : une page tient dans PAGE_PX de haut. Tout le reste
  // (colonnes, cartes) en découle — c'est la seule constante d'échelle.
  const PAGE_PX = 520
  const scale = PAGE_PX / metrics.page_h
  const colHeight = (p: number) => (metrics.column_h[p] ?? metrics.column_h[0]) * scale
  const usedHeight = (p: number, c: number) =>
    (layout[p]?.[c] ?? []).reduce((s, id) => s + heightOf(id), 0)

  function edit(fn: (l: Layout) => void) {
    const next = layout.map((page) => page.map((col) => [...col]))
    fn(next)
    onChange(next)
  }

  function insert(page: number, col: number, index: number) {
    const d = drag.current
    if (!d) return
    edit((l) => {
      let id: string
      let at = index
      if (d.from === 'pool') {
        id = d.id
      } else {
        id = l[d.page][d.col][d.index]
        l[d.page][d.col].splice(d.index, 1)
        // retrait avant insertion dans la MÊME colonne : l'index cible glisse
        if (d.page === page && d.col === col && d.index < index) at -= 1
      }
      l[page][col].splice(Math.max(0, Math.min(at, l[page][col].length)), 0, id)
    })
    drag.current = null
  }

  function move(page: number, col: number, index: number, dir: 'up' | 'down' | 'left' | 'right') {
    edit((l) => {
      const src = l[page][col]
      const [id] = src.splice(index, 1)
      if (dir === 'up') src.splice(Math.max(0, index - 1), 0, id)
      else if (dir === 'down') src.splice(Math.min(src.length, index + 1), 0, id)
      else {
        // ← / → : colonne précédente/suivante, en changeant de page au bout
        const flat = page * 2 + col + (dir === 'right' ? 1 : -1)
        const [np, nc] = [Math.floor(flat / 2), flat % 2]
        if (np < 0 || np >= l.length) { src.splice(index, 0, id); return }
        l[np][nc].push(id)
      }
    })
  }

  function remove(page: number, col: number, index: number) {
    edit((l) => { l[page][col].splice(index, 1) })
  }

  function clearAll() {
    setFillTarget(null)
    onChange(emptyLayout(pages))
  }

  function toggleFill(page: number, col: number) {
    const key = `${page}:${col}`
    if (fillTarget === key) {
      setFillTarget(null)
      return
    }
    // Le remplissage porte sur les exercices courts, pas sur les problèmes de
    // chapitre. Tous les candidats doivent être visibles, même si une recherche
    // était active juste avant le clic.
    setTab('exercises')
    setSearch('')
    setFillTarget(key)
  }

  return (
    <Group align="stretch" gap="md" wrap="nowrap" style={{ height: '100%' }}>
      <style>{`
        @keyframes manual-fill-fade {
          0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(76, 110, 245, .08); }
          50% { opacity: .58; box-shadow: 0 0 0 3px rgba(76, 110, 245, .22); }
        }
        .manual-fill-pulse {
          animation: manual-fill-fade 1.35s ease-in-out infinite;
          border-color: var(--mantine-color-indigo-5) !important;
        }
        @media (prefers-reduced-motion: reduce) {
          .manual-fill-pulse { animation: none; }
        }
      `}</style>
      {/* ---------------------------------------------------------- pool */}
      <Stack gap="xs" style={{ width: 400, flexShrink: 0 }}>
        <SegmentedControl fullWidth size="xs" value={tab}
          onChange={(v) => setTab(v as 'exercises' | 'problems')}
          data={[
            { value: 'exercises', label: `Exercices (${pool.length})` },
            { value: 'problems', label: `Problèmes (${problems.length})` },
          ]} />
        <Text size="xs" c="dimmed">
          {tab === 'exercises'
            ? 'Exercices des compétences cochées.'
            : 'Problèmes et énigmes des chapitres concernés : ils portent sur '
              + 'tout un chapitre, pas sur une compétence isolée.'}
        </Text>
        <TextInput size="xs" placeholder="Rechercher…" value={search}
          leftSection={<Search size={13} />}
          onChange={(e) => setSearch(e.currentTarget.value)} />
        <ScrollArea style={{ flex: 1 }} type="auto">
          <Stack gap={6} pr={6}>
            {filtered.length === 0 && (
              <Text size="xs" c="dimmed" ta="center" py="lg">
                Aucun exercice en banque pour cette sélection.
              </Text>
            )}
            {filtered.map((it) => (
              <PoolCard key={it.id} it={it} used={usage.has(it.id)}
                fillCandidate={activeFillCandidates.has(it.id)}
                onDragStart={() => { drag.current = { from: 'pool', id: it.id } }} />
            ))}
          </Stack>
        </ScrollArea>
      </Stack>

      {/* --------------------------------------------------------- pages */}
      <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}>
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            Glissez un exercice sur la colonne de votre choix. Réorganisez-les à
            la souris ou avec les flèches de chaque carte.
          </Text>
          <Button size="compact-xs" variant="subtle" color="red"
            leftSection={<Trash2 size={12} />} onClick={clearAll}
            disabled={layoutCount(layout) === 0}>
            Vider
          </Button>
        </Group>
        {overfull.length > 0 && (
          <Alert color="orange" p={6} icon={<AlertTriangle size={14} />}>
            <Text size="xs">
              Trop de cartes en {overfull.join(', ')} : à l'impression, celles qui
              ne tiennent pas glisseront dans la colonne suivante.
            </Text>
          </Alert>
        )}
        <ScrollArea style={{ flex: 1 }} type="auto">
          <Group align="flex-start" justify="center" gap="md" pr={6}>
            {layout.map((page, p) => (
              <Stack key={p} gap={4}>
                <Group gap={6} justify="space-between">
                  <Text size="xs" fw={600}>Page {p + 1}</Text>
                  {p === 0 && <Badge size="xs" variant="light" color="gray">en-tête élève</Badge>}
                </Group>
                <Group gap={metrics.col_gap * scale} align="flex-start" wrap="nowrap"
                  style={{
                    padding: metrics.margin * scale,
                    border: '1px solid var(--mantine-color-gray-4)',
                    borderRadius: 6, background: 'var(--mantine-color-gray-0)',
                  }}>
                  {[0, 1].map((cIdx) => {
                    const used = usedHeight(p, cIdx)
                    const cap = colHeight(p) / scale
                    const over = used > cap
                    const fillKey = `${p}:${cIdx}`
                    const fillActive = fillTarget === fillKey
                    const candidates = fillActive ? activeFillCandidates : fittingIds(p, cIdx)
                    const canFill = used / cap > FILL_TRIGGER_RATIO
                      && used < cap && candidates.size > 0
                    const freeTop = Math.min(colHeight(p) - 22, Math.max(0, used * scale))
                    const freeHeight = Math.max(22, colHeight(p) - freeTop)
                    return (
                      <Stack key={cIdx} gap={2} data-col={`${p}-${cIdx}`}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={(e) => {
                          e.preventDefault()
                          insert(p, cIdx, (layout[p]?.[cIdx] ?? []).length)
                        }}
                        style={{
                          width: metrics.col_w * scale, height: colHeight(p),
                          border: `1px dashed ${over ? 'var(--mantine-color-red-6)'
                            : 'var(--mantine-color-gray-4)'}`,
                          borderRadius: 4, padding: 2, overflow: 'hidden',
                          background: 'var(--mantine-color-body)', position: 'relative',
                        }}>
                        {(layout[p]?.[cIdx] ?? []).map((id, i) => (
                          <PlacedCard
                            key={`${id}-${i}`} it={byId.get(id)}
                            h={heightOf(id) * scale}
                            first={i === 0} last={i === layout[p][cIdx].length - 1}
                            canLeft={p * 2 + cIdx > 0}
                            canRight={p * 2 + cIdx < layout.length * 2 - 1}
                            onDragStart={() => {
                              drag.current = { from: 'layout', page: p, col: cIdx, index: i }
                            }}
                            onDropBefore={() => insert(p, cIdx, i)}
                            onDropAfter={() => insert(p, cIdx, i + 1)}
                            onMove={(d) => move(p, cIdx, i, d)}
                            onRemove={() => remove(p, cIdx, i)} />
                        ))}
                        {(layout[p]?.[cIdx] ?? []).length === 0 && (
                          <Text fz={9} c="dimmed" ta="center" mt="lg">
                            déposez ici
                          </Text>
                        )}
                        {canFill && (!fillTarget || fillActive) && (
                          <Box style={{
                            position: 'absolute', left: 2, right: 2, top: freeTop,
                            height: freeHeight, display: 'flex', alignItems: 'center',
                            justifyContent: 'center', zIndex: 3, pointerEvents: 'none',
                          }}>
                            <Tooltip label={fillActive
                              ? `${candidates.size} exercice(s) tiennent encore — cliquer pour arrêter`
                              : `Proposer ${candidates.size} exercice(s) qui tiennent dans cet espace`}>
                              <Button size="compact-xs" variant={fillActive ? 'filled' : 'light'}
                                color="indigo" leftSection={<Sparkles size={11} />}
                                className={fillActive ? 'manual-fill-pulse' : undefined}
                                style={{ pointerEvents: 'auto', height: 20, minHeight: 20 }}
                                onClick={(e) => { e.stopPropagation(); toggleFill(p, cIdx) }}>
                                Remplir
                              </Button>
                            </Tooltip>
                          </Box>
                        )}
                      </Stack>
                    )
                  })}
                </Group>
                <Group gap={6} justify="center">
                  {[0, 1].map((cIdx) => {
                    const used = usedHeight(p, cIdx)
                    const cap = (metrics.column_h[p] ?? metrics.column_h[0])
                    const pct = Math.round((used / cap) * 100)
                    return (
                      <Text key={cIdx} fz={10}
                        c={pct > 100 ? 'red' : pct > 85 ? 'orange' : 'dimmed'}
                        style={{ width: metrics.col_w * scale, textAlign: 'center' }}>
                        {pct}% rempli
                      </Text>
                    )
                  })}
                </Group>
              </Stack>
            ))}
          </Group>
        </ScrollArea>
      </Stack>
    </Group>
  )
}
