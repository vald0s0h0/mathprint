// Étape « Exercices » de l'assistant « Créer un sujet » (mode manuel) : même mur de
// cartes que l'onglet Banque. Le professeur clique sur ce qui l'intéresse, le moteur
// (packEngine) cherche comment tout faire tenir dans les pages du sujet et grise ce
// qui ne rentre plus. Les hauteurs viennent du moteur PDF : le remplissage affiché
// est celui de la feuille imprimée.
import {
  Alert, Badge, Box, Group, Paper, Progress, ScrollArea, SegmentedControl, Select, Stack, Text,
  TextInput, ThemeIcon, Title, Tooltip,
} from '@mantine/core'
import { AlertTriangle, Check, Clock, Layers, Search } from 'lucide-react'
import { memo, useCallback, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import ExerciseCatalogCard, { formatPoints, type CatalogExercise } from '../../components/ExerciseCatalogCard'
import ExerciseMasonry from '../../components/ExerciseMasonry'
import MathText from '../../components/MathText'
import { mathPlain } from '../../utils/mathPlain'
import { packBest, pageFill, totalCapacity, type Capacities, type PackCard } from './packEngine'

export type PoolItem = CatalogExercise & {
  id: string; competency_id: string; competency_label: string
  chapter_code: string; chapter_name: string
  difficulty: number; source_number: string; is_problem: boolean
  bareme_points: number; estimated_seconds: number; tier: number
  height_pt: number; height_pt_no_guide: number
}
export type Metrics = {
  page_w: number; page_h: number; col_w: number; col_gap: number
  margin: number; gap: number; cols_per_page: number
  column_h: number[]; column_h_rest: number
}

const DIFFICULTY_SECTIONS = [
  { level: 1, title: 'Faciles' }, { level: 2, title: 'Moyens' }, { level: 3, title: 'Difficiles' },
]

export const capacitiesOf = (metrics: Metrics): Capacities => ({
  first: metrics.column_h[0], rest: metrics.column_h_rest,
})

export const packCard = (it: PoolItem, guidesOn: boolean): PackCard => ({
  height: guidesOn ? it.height_pt : it.height_pt_no_guide, tier: it.tier,
})

/** Face d'impression d'une page : le recto-verso se lit feuille par feuille. */
export function sideLabel(page: number, pages: number): string {
  const side = page % 2 === 0 ? 'Recto' : 'Verso'
  return pages <= 2 ? side : `Feuille ${Math.floor(page / 2) + 1} · ${side.toLowerCase()}`
}

export function formatDuration(seconds: number): string {
  if (seconds <= 0) return '0 min'
  const minutes = Math.max(1, Math.round(seconds / 60))
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')}`
}

/** Résumé d'une sélection : ce qu'en pense le moteur, et les deux compteurs affichés. */
export function selectionSummary(ids: string[], byId: Map<string, PoolItem>, metrics: Metrics,
                                 guidesOn: boolean, pages: number) {
  const items = ids.map((id) => byId.get(id)).filter((it): it is PoolItem => !!it)
  const cards = items.map((it) => packCard(it, guidesOn))
  const caps = capacitiesOf(metrics)
  const columns = packBest(cards, caps, pages)
  const used = cards.reduce((sum, c) => sum + c.height, 0)
  return {
    fits: columns !== null,
    fill: used / totalCapacity(caps, pages),
    pages: columns ? pageFill(columns, cards, caps, pages) : [],
    seconds: items.reduce((sum, it) => sum + (it.estimated_seconds || 0), 0),
    points: items.reduce((sum, it) => sum + (it.bareme_points || 0), 0),
    count: items.length,
  }
}

function FillGauge({ fill, fits, pageParts, pages }: {
  fill: number; fits: boolean; pageParts: number[]; pages: number
}) {
  const pct = Math.round(fill * 100)
  const color = !fits ? 'red' : pct >= 90 ? 'teal' : 'indigo'
  return (
    <Group gap="sm" wrap="nowrap" style={{ flex: 2, minWidth: 260 }}>
      <ThemeIcon variant="light" color={color} size={38} radius="md"><Layers size={20} /></ThemeIcon>
      <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
        <Group gap={8} align="baseline" wrap="nowrap">
          <Text fw={750} fz={22} lh={1} c={fits ? undefined : 'red'}>{pct} %</Text>
          <Text size="xs" c="dimmed">de la place remplie</Text>
        </Group>
        <Group gap={6} wrap="nowrap" align="flex-start">
          {Array.from({ length: pages }, (_, p) => (
            <Box key={p} style={{ flex: 1, minWidth: 0 }}>
              <Progress size={7} color={color} value={(pageParts[p] ?? 0) * 100}
                aria-label={`${sideLabel(p, pages)} : ${Math.round((pageParts[p] ?? 0) * 100)} % rempli`} />
              <Text fz={10} c="dimmed" mt={2} truncate>{sideLabel(p, pages)}</Text>
            </Box>
          ))}
        </Group>
      </Stack>
    </Group>
  )
}

function DurationStat({ seconds, count, points }: { seconds: number; count: number; points: number }) {
  return (
    <Tooltip label="Somme des temps prévus pour un élève, à titre indicatif">
      <Group gap="sm" wrap="nowrap" style={{ flexShrink: 0 }}>
        <ThemeIcon variant="light" color="grape" size={38} radius="md"><Clock size={20} /></ThemeIcon>
        <div>
          <Text fw={750} fz={22} lh={1}>≈ {formatDuration(seconds)}</Text>
          <Text size="xs" c="dimmed" mt={4}>
            {count} carte{count > 1 ? 's' : ''} · {formatPoints(points)} pt
          </Text>
        </div>
      </Group>
    </Tooltip>
  )
}

const SelectableCard = memo(function SelectableCard({ it, selected, blocked, guidesOn, onToggle }: {
  it: PoolItem; selected: boolean; blocked: boolean; guidesOn: boolean
  onToggle: (id: string) => void
}) {
  const toggle = () => { if (!blocked) onToggle(it.id) }
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle() }
  }
  return (
    <Box role="button" tabIndex={blocked ? -1 : 0} aria-pressed={selected} aria-disabled={blocked}
      onClick={toggle} onKeyDown={onKeyDown}
      style={{
        position: 'relative', borderRadius: 'var(--mantine-radius-md)',
        cursor: blocked ? 'not-allowed' : 'pointer',
        outline: selected ? '3px solid var(--mantine-color-indigo-filled)' : 'none',
        outlineOffset: -1,
      }}>
      <Box style={{ opacity: blocked ? 0.35 : 1, filter: blocked ? 'grayscale(1)' : undefined, transition: 'opacity 150ms' }}>
        <ExerciseCatalogCard exercise={it} guides={guidesOn}
          caption={<Text size="xs" c="dimmed" lineClamp={2}>
            {it.source_number && <b>n°{it.source_number} · </b>}<MathText text={it.competency_label} />
          </Text>} />
      </Box>
      {selected && (
        <ThemeIcon size={26} radius="xl" color="indigo" style={{ position: 'absolute', top: -8, right: -8, zIndex: 2 }}>
          <Check size={16} />
        </ThemeIcon>
      )}
      {blocked && (
        <Box style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
          pointerEvents: 'none',
        }}>
          <Badge size="lg" color="gray" variant="filled">Manque de place</Badge>
        </Box>
      )}
    </Box>
  )
})

function CardSections({ items, selected, blocked, guidesOn, onToggle }: {
  items: PoolItem[]; selected: Set<string>; blocked: Set<string>; guidesOn: boolean
  onToggle: (id: string) => void
}) {
  return (
    <Stack gap="xl">
      {DIFFICULTY_SECTIONS.map(({ level, title }) => {
        const group = items.filter((it) => Math.max(1, Math.min(3, it.difficulty || 2)) === level)
        if (!group.length) return null
        return (
          <Stack key={level} gap="sm" component="section" aria-label={title}>
            <Group gap="xs" style={{ borderBottom: '1px solid var(--mantine-color-default-border)', paddingBottom: 8 }}>
              <Title order={4}>{title}</Title>
              <Text size="xs" c="dimmed">{group.length} carte{group.length > 1 ? 's' : ''}</Text>
            </Group>
            <ExerciseMasonry>
              {group.map((it) => (
                <SelectableCard key={it.id} it={it} selected={selected.has(it.id)} guidesOn={guidesOn}
                  blocked={blocked.has(it.id)} onToggle={onToggle} />
              ))}
            </ExerciseMasonry>
          </Stack>
        )
      })}
    </Stack>
  )
}

export default function ExercisePicker({ pool, problems, metrics, guidesOn, pages, selection, onChange }: {
  pool: PoolItem[]; problems: PoolItem[]; metrics: Metrics; guidesOn: boolean; pages: number
  selection: string[]; onChange: (ids: string[]) => void
}) {
  const [competency, setCompetency] = useState<string | null>(null)
  const [level, setLevel] = useState('all')
  const [search, setSearch] = useState('')

  const all = useMemo(() => [...pool, ...problems], [pool, problems])
  const byId = useMemo(() => new Map(all.map((it) => [it.id, it])), [all])
  const selected = useMemo(() => new Set(selection), [selection])
  const summary = useMemo(() => selectionSummary(selection, byId, metrics, guidesOn, pages),
    [selection, byId, metrics, guidesOn, pages])

  // Une carte est grisée dès que le moteur ne sait plus la faire tenir avec celles
  // déjà choisies. Deux cartes de même palier et de même hauteur ont le même sort.
  const blocked = useMemo(() => {
    const caps = capacitiesOf(metrics)
    const base = selection.map((id) => byId.get(id)).filter((it): it is PoolItem => !!it)
      .map((it) => packCard(it, guidesOn))
    const verdict = new Map<string, boolean>()
    const out = new Set<string>()
    for (const it of all) {
      if (selected.has(it.id)) continue
      const card = packCard(it, guidesOn)
      const key = `${card.tier}:${card.height}`
      if (!verdict.has(key)) verdict.set(key, packBest([...base, card], caps, pages) !== null)
      if (!verdict.get(key)) out.add(it.id)
    }
    return out
  }, [all, byId, selected, selection, metrics, guidesOn, pages])

  // rappel stable : un clic ne réaffiche que les cartes dont l'état change
  const latest = useRef({ selection, onChange })
  latest.current = { selection, onChange }
  const toggle = useCallback((id: string) => {
    const { selection: current, onChange: change } = latest.current
    change(current.includes(id) ? current.filter((x) => x !== id) : [...current, id])
  }, [])

  const competencies = useMemo(() => {
    const groups = new Map<string, { label: string; total: number; chosen: number }>()
    for (const it of pool) {
      const group = groups.get(it.competency_id) ?? { label: it.competency_label, total: 0, chosen: 0 }
      group.total += 1
      group.chosen += selected.has(it.id) ? 1 : 0
      groups.set(it.competency_id, group)
    }
    return [...groups.entries()].map(([value, g]) => ({
      value, label: `${mathPlain(g.label)} (${g.chosen ? `${g.chosen} choisi${g.chosen > 1 ? 's' : ''} sur ` : ''}${g.total})`,
    }))
  }, [pool, selected])

  const q = search.trim().toLocaleLowerCase('fr')
  const matches = (it: PoolItem) => (level === 'all' || Math.max(1, Math.min(3, it.difficulty || 2)) === Number(level))
    && (!q || `${it.statement} ${it.title ?? ''} ${it.competency_label}`.toLocaleLowerCase('fr').includes(q))
  const shownExercises = pool.filter((it) => matches(it) && (!competency || it.competency_id === competency))
  const shownProblems = problems.filter(matches)
  const emptyPages = summary.fits
    ? summary.pages.map((part, p) => (part === 0 && summary.count > 0 ? sideLabel(p, pages) : null)).filter(Boolean)
    : []

  return (
    <Stack gap="sm" style={{ height: '100%', minHeight: 0 }}>
      <Paper withBorder radius="md" p="sm">
        <Stack gap="sm">
          <Group gap="xl" wrap="wrap" align="center">
            <FillGauge fill={summary.fill} fits={summary.fits} pageParts={summary.pages} pages={pages} />
            <DurationStat seconds={summary.seconds} count={summary.count} points={summary.points} />
          </Group>
          {!summary.fits && (
            <Alert color="red" p={6} icon={<AlertTriangle size={14} />}>
              <Text size="xs">Ces cartes ne tiennent plus dans {pages === 1 ? 'la page' : `les ${pages} pages`} : retirez-en une, ou ajoutez une page à l'étape « Sujet ».</Text>
            </Alert>
          )}
          {emptyPages.length > 0 && (
            <Text size="xs" c="dimmed">Encore vide : {emptyPages.join(', ')}. Une page vide reste imprimée.</Text>
          )}
          <Group gap="xs" wrap="wrap">
            <Select size="xs" w={{ base: '100%', sm: 340 }} placeholder="Toutes les compétences" clearable
              aria-label="Compétence" data={competencies} value={competency} onChange={setCompetency} />
            <SegmentedControl size="xs" aria-label="Difficulté" value={level} onChange={setLevel}
              data={[{ value: 'all', label: 'Toutes' }, { value: '1', label: 'Facile' }, { value: '2', label: 'Moyen' }, { value: '3', label: 'Difficile' }]} />
            <TextInput size="xs" placeholder="Rechercher…" aria-label="Rechercher" value={search}
              leftSection={<Search size={13} />} onChange={(e) => setSearch(e.currentTarget.value)} />
          </Group>
        </Stack>
      </Paper>
      <ScrollArea style={{ flex: 1, minHeight: 0 }} type="auto">
        <Stack gap="xl" pr="sm" pt={10} pb="md">
          {shownExercises.length === 0 && shownProblems.length === 0 && (
            <Text size="sm" c="dimmed" ta="center" py="xl">Aucune carte pour cette sélection.</Text>
          )}
          <CardSections items={shownExercises} selected={selected} blocked={blocked}
            guidesOn={guidesOn} onToggle={toggle} />
          {shownProblems.length > 0 && (
            <Paper p="md" radius="md" withBorder>
              <Stack gap="md">
                <Box>
                  <Title order={3}>Problèmes du chapitre entier</Title>
                  <Text size="sm" c="dimmed">Toujours imprimés après les exercices.</Text>
                </Box>
                <CardSections items={shownProblems} selected={selected} blocked={blocked}
                  guidesOn={guidesOn} onToggle={toggle} />
              </Stack>
            </Paper>
          )}
        </Stack>
      </ScrollArea>
    </Stack>
  )
}
