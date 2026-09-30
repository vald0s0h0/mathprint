// Référentiels par niveau — affichage hiérarchisé domaine (H1) > chapitre
// (H2) > compétences (H3). Le référentiel suit le cycle global. L'ID court
// (ex. A1.1, repris de la numérotation du sommaire) est affiché à côté du
// libellé de compétence — un libellé isolé (ex. "Automatismes") ne suffit
// pas à savoir de quoi il s'agit sans son chapitre.
// Une colonne de maîtrise moyenne par classe du niveau (même source que
// l'assistant sujet : /api/assessments/competency-matrix) + une colonne
// Moyenne (moyenne des classes renseignées).
import {
  Badge, Group, Progress, ScrollArea, Stack, Text, TextInput, Title, Tooltip,
} from '@mantine/core'
import { Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import CompetencyHierarchy, { type CompetencyHierarchyColumn } from '../components/CompetencyHierarchy'
import GradeSelectionRequired from '../components/GradeSelectionRequired'
import { useAppState } from '../state/AppState'
import { masteryColor } from '../utils/mastery'

type Framework = { id: string; name: string; grade_level: string; version: string; status: string }
type Comp = { id: string; code: string; short_id: string; label: string }
type Domain = {
  code: string; name: string
  chapters: { code: string; name: string; competencies: Comp[] }[]
}
type ClassRef = { id: string; name: string }
type Matrix = {
  classes: ClassRef[]
  domains: { chapters: { competencies: { id: string; mastery_by_class: Record<string, number | null> }[] }[] }[]
}

const CLASS_COL_WIDTH = 96
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
  const { cycle } = useAppState()

  useEffect(() => {
    api.get<Framework[]>('/api/competencies/frameworks').then(setFrameworks)
  }, [])

  // le référentiel affiché suit le cycle filtré dans la barre du haut
  useEffect(() => {
    if (cycle === 'all') {
      setSel(null)
      setTree([])
      return
    }
    const fw = frameworks.find((f) => f.grade_level === cycle)
    setSel(fw?.id ?? null)
    setTree([])
  }, [frameworks, cycle])

  useEffect(() => {
    setMatrix(null)
    if (cycle === 'all') return
    let current = true
    api.get<Matrix>(`/api/assessments/competency-matrix?grade_level=${cycle}`)
      .then((m) => { if (current) setMatrix(m) })
      .catch(() => {})
    return () => { current = false }
  }, [cycle])

  // maîtrise par compétence : id -> (classe -> maîtrise moyenne)
  const masteryById = useMemo(() => {
    const out = new Map<string, Record<string, number | null>>()
    for (const d of matrix?.domains ?? [])
      for (const ch of d.chapters)
        for (const c of ch.competencies) out.set(c.id, c.mastery_by_class)
    return out
  }, [matrix])

  const columns = useMemo<CompetencyHierarchyColumn<Comp>[]>(() => {
    const classes = matrix?.classes ?? []
    if (!classes.length) return []
    const average = (c: Comp) => {
      const values = Object.values(masteryById.get(c.id) ?? {})
        .filter((v): v is number => v != null)
      return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
    }
    return [
      ...classes.map((cls) => ({
        key: cls.id, label: cls.name, width: CLASS_COL_WIDTH, align: 'center' as const,
        render: (c: Comp) => <MasteryCell value={masteryById.get(c.id)?.[cls.id]} hint={cls.name} />,
      })),
      {
        key: '__moyenne', label: <Text size="sm" fw={700}>Moyenne</Text>,
        width: CLASS_COL_WIDTH, align: 'center' as const,
        render: (c: Comp) => <MasteryCell value={average(c)} strong hint="Toutes classes" />,
      },
    ]
  }, [matrix, masteryById])

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

  if (cycle === 'all') return <GradeSelectionRequired title="Compétences" />

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
                {total} objectifs d'apprentissage — {fw.grade_level === '6e'
                  ? 'cycle 3 (année 6e uniquement)' : 'cycle 4'}
              </Text>
            </Group>
          )}
        </div>
        <Group gap="xs">
          <TextInput size="xs" w={240} placeholder="Filtrer les objectifs…" value={filter}
            leftSection={<Search size={14} />}
            onChange={(e) => setFilter(e.target.value)} />
        </Group>
      </Group>

      <ScrollArea h="calc(100vh - 180px)">
        <CompetencyHierarchy domains={hierarchy} columns={columns}
          columnGroupLabel="Compétences acquises"
          tableMinWidth={columns.length ? LABEL_MIN_WIDTH + columns.length * CLASS_COL_WIDTH : undefined}
          getRowKey={(competency) => competency.id}
          getShortId={(competency) => competency.short_id || competency.code}
          getLabel={(competency) => competency.label} />
      </ScrollArea>
    </Stack>
  )
}
