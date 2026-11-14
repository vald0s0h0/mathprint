// Référentiels par niveau — affichage hiérarchisé domaine (H1) > chapitre
// (H2) > compétences (H3). Le référentiel suit le cycle global. L'ID court
// (ex. A1.1, repris de la numérotation du sommaire) est affiché à côté du
// libellé de compétence — un libellé isolé (ex. "Automatismes") ne suffit
// pas à savoir de quoi il s'agit sans son chapitre.
// Une colonne de maîtrise moyenne par classe du niveau (même source que
// l'assistant sujet : /api/assessments/competency-matrix) + une colonne
// Moyenne (moyenne des classes renseignées).
import {
  Badge, Button, Select, Group, Progress, ScrollArea, Stack, Text, TextInput, Title, Tooltip,
} from '@mantine/core'
import { Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, download } from '../api'
import { notifications } from '@mantine/notifications'
import CompetencyHierarchy, { type CompetencyHierarchyColumn } from '../components/CompetencyHierarchy'
import GradeSelectionRequired from '../components/GradeSelectionRequired'
import { useAppState } from '../state/AppState'
import { masteryColor } from '../utils/mastery'
import { formatHours, formatMinutes } from '../utils/duration'

type Framework = { id: string; name: string; grade_level: string; version: string; status: string; program_id: string; subject_id: string; path_id: string | null }
type Comp = {
  id: string; code: string; short_id: string; label: string
  // durée estimée et rang dans l'année, quand le programme les fournit (CI)
  recommended_minutes?: number | null; teaching_order?: number | null
}
type Chapter = {
  code: string; name: string; competencies: Comp[]
  // leçon dans la progression annuelle officielle : heures, rang, semaine de début
  hours?: number | null; rank?: number | null; week?: number | null
}
type Domain = { code: string; name: string; chapters: Chapter[] }
type ClassRef = { id: string; name: string }
type Matrix = {
  classes: ClassRef[]
  domains: { chapters: { competencies: { id: string; mastery_by_class: Record<string, number | null> }[] }[] }[]
}

const CLASS_COL_WIDTH = 96
const DURATION_COL_WIDTH = 84
const LABEL_MIN_WIDTH = 320

function MasteryCell({ value, strong, hint }: { value: number | null | undefined; strong?: boolean; hint: string }) {
  if (value == null) return <Text size="xs" c="dimmed">—</Text>
  const pct = Math.round(value * 100)
  return (
    <Tooltip label={`${hint} : ${pct} % de maîtrise moyenne`}>
      <Group gap={5} justify="center" wrap="nowrap">
        <Progress value={pct} size={6} w={32} color={masteryColor(value)} />
        <Text size="xs" fw={strong ? 700 : undefined} c={strong ? undefined : 'dimmed'}
          style={{ minWidth: 30, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
          {pct} %
        </Text>
      </Group>
    </Tooltip>
  )
}

export default function Competencies() {
  const [frameworks, setFrameworks] = useState<Framework[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [tree, setTree] = useState<Domain[]>([])
  const [filter, setFilter] = useState('')
  const [matrix, setMatrix] = useState<Matrix | null>(null)
  const { cycle, subject, teaching } = useAppState()
  // Référentiels du cycle, dans les seuls programmes enseignés par l'utilisateur.
  const available = useMemo(() => frameworks.filter((f) => f.grade_level === cycle
    && f.subject_id === subject && teaching.programs.includes(f.program_id)),
  [frameworks, cycle, subject, teaching.programs])

  useEffect(() => {
    api.get<Framework[]>('/api/competencies/frameworks').then(setFrameworks)
  }, [])

  // le référentiel affiché suit le cycle filtré dans la barre du haut
  useEffect(() => {
    setSel(available[0]?.id ?? null)
    setTree([])
  }, [available])

  useEffect(() => {
    setMatrix(null)
    if (cycle === 'all' || !sel) return
    let current = true
    api.get<Matrix>(`/api/assessments/competency-matrix?grade_level=${cycle}&subject_id=${subject}&framework_id=${sel}`)
      .then((m) => { if (current) setMatrix(m) })
      .catch(() => {})
    return () => { current = false }
  }, [cycle, subject, sel])

  // maîtrise par compétence : id -> (classe -> maîtrise moyenne)
  const masteryById = useMemo(() => {
    const out = new Map<string, Record<string, number | null>>()
    for (const d of matrix?.domains ?? [])
      for (const ch of d.chapters)
        for (const c of ch.competencies) out.set(c.id, c.mastery_by_class)
    return out
  }, [matrix])

  // Durée estimée : colonne affichée seulement si le référentiel la fournit.
  const hasTiming = useMemo(() => tree.some((d) => d.chapters.some((ch) =>
    ch.competencies.some((c) => c.recommended_minutes != null))), [tree])

  const columns = useMemo<CompetencyHierarchyColumn<Comp>[]>(() => {
    const classes = matrix?.classes ?? []
    const duration: CompetencyHierarchyColumn<Comp>[] = hasTiming ? [{
      key: '__duree', label: 'Durée', width: DURATION_COL_WIDTH, align: 'right' as const,
      render: (c: Comp) => c.recommended_minutes == null ? <Text size="xs" c="dimmed">—</Text> : (
        <Tooltip label={`Durée estimée${c.teaching_order ? ` · ${c.teaching_order === 1 ? '1re' : `${c.teaching_order}e`} compétence de l’année` : ''}`}>
          <Text size="xs" c="dimmed" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatMinutes(c.recommended_minutes)}
          </Text>
        </Tooltip>
      ),
    }] : []
    if (!classes.length) return duration
    const average = (c: Comp) => {
      const values = Object.values(masteryById.get(c.id) ?? {})
        .filter((v): v is number => v != null)
      return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
    }
    return [
      ...duration,
      ...classes.map((cls) => ({
        key: cls.id, label: cls.name, width: CLASS_COL_WIDTH, align: 'center' as const,
        render: (c: Comp) => <MasteryCell value={masteryById.get(c.id)?.[cls.id]} hint={cls.name} />,
      })),
      {
        key: '__moyenne', label: 'Moyenne',
        width: CLASS_COL_WIDTH, align: 'center' as const,
        render: (c: Comp) => <MasteryCell value={average(c)} strong hint="Toutes classes" />,
      },
    ]
  }, [matrix, masteryById, hasTiming])

  useEffect(() => {
    if (!sel) return
    let current = true
    api.get<Domain[]>(`/api/competencies/tree?framework_id=${sel}`)
      .then((domains) => { if (current) setTree(domains) })
    return () => { current = false }
  }, [sel])

  const fw = frameworks.find((f) => f.id === sel)
  const filtered = useMemo(() => {
    if (!filter.trim()) return tree
    const q = filter.toLowerCase()
    return tree.map((d) => ({
      ...d,
      chapters: d.chapters.map((ch) => ({
        ...ch,
        competencies: ch.competencies.filter((c) => c.label.toLowerCase().includes(q)),
      })).filter((ch) => ch.competencies.length),
    })).filter((d) => d.chapters.length)
  }, [tree, filter])

  const chapterInfo = useMemo(() => {
    const out = new Map<string, Chapter>()
    for (const d of tree) for (const ch of d.chapters) out.set(`${d.code}/${ch.code || ch.name}`, ch)
    return out
  }, [tree])
  const chapterCount = (chapter: { key: string; rows: Comp[] }) => {
    const n = chapter.rows.length
    const parts = [`${n} compétence${n > 1 ? 's' : ''}`]
    const info = chapterInfo.get(chapter.key)
    if (info?.hours != null) parts.push(formatHours(info.hours))
    if (info?.rank != null) parts.push(`leçon n° ${info.rank} de l’année`)
    if (info?.week != null) parts.push(`semaine ${info.week}`)
    return parts.join(' · ')
  }

  const total = tree.reduce((n, d) => n + d.chapters.reduce((m, ch) => m + ch.competencies.length, 0), 0)
  const hierarchy = useMemo(() => filtered.map((domain) => ({
    key: domain.code || domain.name,
    code: domain.code,
    name: domain.name,
    chapters: domain.chapters.map((chapter) => ({
      key: `${domain.code}/${chapter.code || chapter.name}`,
      code: chapter.code,
      name: chapter.name,
      rows: chapter.competencies,
    })),
  })), [filtered])

  if (cycle === 'all') return <GradeSelectionRequired title="Compétences" noClass={!teaching.cycles.length} />

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <div>
          <Title order={2}>Compétences</Title>
          {fw && (
            <Group gap="xs" mt={4}>
              <Badge variant="light" color={fw.status === 'published' ? 'green' : 'gray'} size="sm">
                v{fw.version} — {fw.status === 'published' ? 'publiée (immuable)' : fw.status}
              </Badge>
              <Text size="xs" c="dimmed">
                {total} compétences · {fw.program_id.toUpperCase()} · {fw.grade_level}
              </Text>
            </Group>
          )}
        </div>
        <Group gap="xs">
          <Button size="xs" variant="light" disabled={!sel || !total}
            onClick={() => download(`/api/competencies/frameworks/${sel}/pdf`, 'competences.pdf').catch(e => notifications.show({ color: 'red', message: e.message }))}>Liste à imprimer</Button>
          <TextInput size="xs" w={240} placeholder="Filtrer les objectifs…" value={filter}
            leftSection={<Search size={14} />}
            onChange={(e) => setFilter(e.target.value)} />
        </Group>
      </Group>

      {/* Seul accès aux autres référentiels quand il y en a plusieurs (deux programmes, spécialités du lycée). */}
      {available.length > 1 && <Select aria-label="Programme" allowDeselect={false} value={sel} onChange={v => { setSel(v); setTree([]) }}
        data={available.map(f => ({ value: f.id, label: `${f.program_id.toUpperCase()} · ${f.name}` }))} />}
      {total === 0 && <Text size="sm" c="dimmed">Structure prête. Les compétences seront ajoutées ultérieurement.</Text>}
      <ScrollArea>
        <CompetencyHierarchy domains={hierarchy} columns={columns} columnsInChapterBand
          chapterCount={chapterCount}
          tableMinWidth={columns.length ? LABEL_MIN_WIDTH + columns.length * CLASS_COL_WIDTH : undefined}
          getRowKey={(competency) => competency.id}
          getShortId={(competency) => competency.short_id || competency.code}
          getLabel={(competency) => competency.label} />
      </ScrollArea>
    </Stack>
  )
}
