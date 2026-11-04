import { Alert, Box, Button, Group, Loader, Paper, ScrollArea, SegmentedControl, Stack, Text, TextInput, Title, Tooltip } from '@mantine/core'
import { ArrowLeft, ChevronLeft, ChevronRight, Library, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAppState } from '../state/AppState'
import CompetencyHierarchy, { type CompetencyHierarchyColumn } from './CompetencyHierarchy'
import GradeSelectionRequired from './GradeSelectionRequired'
import ExerciseCatalogCard, { exerciseDifficulty, type CatalogExercise } from './ExerciseCatalogCard'
import ExerciseMasonry from './ExerciseMasonry'
import BankHealthPanel from './BankHealthPanel'

type Summary = {
  competency_id: string; code?: string; short_id: string; label: string; grade_level?: string
  domain_code: string; domain_name: string; chapter_code: string; chapter_name: string
  framework_id?: string; total?: number; problems?: number; draft?: number; validated?: number; published?: number
}
type Framework = { id: string; grade_level: string; subject_id: string; program_id: string }
type Matrix = {
  classes: { id: string; name: string }[]
  domains: { chapters: { competencies: { id: string; mastery_by_class: Record<string, number | null> }[] }[] }[]
}
const sameChapter = (a: Summary, b: Summary) => a.chapter_code === b.chapter_code && a.domain_code === b.domain_code && a.grade_level === b.grade_level && a.framework_id === b.framework_id

export default function ExerciseCatalog({ admin = false }: { admin?: boolean }) {
  const { cycle, subject, teaching, catalog } = useAppState()
  const grade = admin ? catalog.cycle : cycle
  const subjectId = admin ? catalog.subject : subject
  const [params, setParams] = useSearchParams()
  const [frameworks, setFrameworks] = useState<Framework[]>([])
  const [summary, setSummary] = useState<Summary[] | null>(null)
  const [matrix, setMatrix] = useState<Matrix | null>(null)
  const [exercises, setExercises] = useState<CatalogExercise[] | null>(null)
  const [problems, setProblems] = useState<CatalogExercise[] | null>(null)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [level, setLevel] = useState('all')
  const [preference, setPreference] = useState('all')
  const [search, setSearch] = useState('')
  const detailRef = useRef<HTMLDivElement>(null)
  const title = admin ? 'Exercices' : 'Banque de contenus'
  const frameworkIds = frameworks.filter(f => f.grade_level === grade && f.subject_id === subjectId && teaching.programs.includes(f.program_id)).map(f => f.id).join(',')
  const scope = admin
    ? `grade_level=${grade}&program_id=${catalog.program}&subject_id=${subjectId}`
    : `grade_level=${grade}&subject_id=${subjectId}`
  const endpoint = admin ? '/api/indigo' : '/api/content'
  const selected = summary?.find(s => s.competency_id === params.get('competency')) ?? null

  useEffect(() => {
    if (admin) return
    let current = true
    api.get<Framework[]>('/api/competencies/frameworks').then(rows => { if (current) setFrameworks(rows) })
      .catch(e => { if (current) setError(e.message) })
    return () => { current = false }
  }, [admin])

  useEffect(() => {
    setSummary(null); setMatrix(null); setError(''); setExercises(null); setProblems(null)
    if (grade === 'all' || (!admin && !frameworkIds)) return
    let current = true
    const ids = frameworkIds.split(',').filter(Boolean)
    const scopes = admin ? [scope] : ids.map(id => `${scope}&framework_id=${encodeURIComponent(id)}`)
    Promise.all(scopes.map(query => api.get<Summary[]>(`${endpoint}/summary?${query}`)))
      .then(groups => { if (current) setSummary(groups.flatMap((rows, i) => rows.map(row => ({ ...row, framework_id: ids[i] })))) })
      .catch(e => { if (current) setError(e.message) })
    if (!admin) Promise.all(scopes.map(query => api.get<Matrix>(`/api/assessments/competency-matrix?${query}`)))
      .then(groups => { if (current) setMatrix({
        classes: [...new Map(groups.flatMap(m => m.classes).map(cls => [cls.id, cls])).values()],
        domains: groups.flatMap(m => m.domains),
      }) })
      .catch(e => { if (current) setError(`Maîtrise des classes indisponible : ${e.message}`) })
    return () => { current = false }
  }, [scope, endpoint, grade, admin, frameworkIds])

  useEffect(() => {
    setExercises(null); setProblems(null); setDetailError('')
    if (!selected) return
    let current = true
    const id = encodeURIComponent(selected.competency_id)
    Promise.all([
      api.get<CatalogExercise[]>(`${endpoint}/exercises?competency_id=${id}&category=exercise`),
      api.get<CatalogExercise[]>(`${endpoint}/exercises?chapter_id=${id}`),
    ]).then(([items, chapter]) => {
      if (current) { setExercises(items); setProblems(chapter) }
    }).catch(e => { if (current) setDetailError(e.message) })
    return () => { current = false }
  }, [selected, endpoint])

  const mastery = useMemo(() => {
    const out = new Map<string, Record<string, number | null>>()
    for (const d of matrix?.domains ?? []) for (const ch of d.chapters)
      for (const c of ch.competencies) out.set(c.id, c.mastery_by_class)
    return out
  }, [matrix])
  const columns = useMemo<CompetencyHierarchyColumn<Summary>[]>(() => [
    { key: 'total', label: 'Total', width: 72, align: 'center', render: s => <Text size="sm">{s.total ?? (s.draft ?? 0) + (s.validated ?? 0)}</Text> },
    ...(admin ? [{ key: 'published', label: 'Publiés', width: 80, align: 'center' as const, render: (s: Summary) => <Text size="sm">{s.published ?? 0}</Text> }] : []),
    ...(matrix?.classes ?? []).map(cls => ({
      key: cls.id, label: cls.name, width: 100, align: 'center' as const,
      render: (s: Summary) => {
        const value = mastery.get(s.competency_id)?.[cls.id]
        return <Tooltip label={value == null ? 'Compétence non évaluée' : `Maîtrise moyenne de ${cls.name}`}>
          <Text size="sm" c={value == null ? 'dimmed' : undefined}>{value == null ? '—' : `${Math.round(value * 100)} %`}</Text>
        </Tooltip>
      },
    })),
  ], [admin, matrix, mastery])

  const domains = useMemo(() => {
    const groups = new Map<string, { key: string; code: string; name: string; chapters: { key: string; code: string; name: string; rows: Summary[] }[] }>()
    for (const s of summary ?? []) {
      if (search && !`${s.short_id} ${s.label} ${s.chapter_name}`.toLocaleLowerCase('fr').includes(search.toLocaleLowerCase('fr'))) continue
      const key = `${s.framework_id ?? s.grade_level ?? grade}/${s.domain_code}`
      if (!groups.has(key)) groups.set(key, { key, code: s.domain_code, name: s.domain_name, chapters: [] })
      const domain = groups.get(key)!
      let chapter = domain.chapters.find(c => c.code === s.chapter_code)
      if (!chapter) { chapter = { key: `${key}/${s.chapter_code}`, code: s.chapter_code, name: s.chapter_name, rows: [] }; domain.chapters.push(chapter) }
      chapter.rows.push(s)
    }
    return [...groups.values()]
  }, [summary, search, grade])

  const open = (row: Summary) => {
    setParams(current => { const next = new URLSearchParams(current); next.set('competency', row.competency_id); return next })
    setLevel('all'); setPreference('all')
    requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: 'start' }))
  }
  const back = () => setParams(current => { const next = new URLSearchParams(current); next.delete('competency'); return next })
  const filtered = (items: CatalogExercise[]) => items.filter(e =>
    (level === 'all' || exerciseDifficulty(e) === Number(level)) && (preference === 'all' || e.preference === preference))
    .sort((a, b) => exerciseDifficulty(a) - exerciseDifficulty(b))
  const changePreference = async (id: string, value: 'like' | 'dislike' | null) => {
    if (value) await api.put(`/api/content/exercises/${id}/preference`, { value })
    else await api.del(`/api/content/exercises/${id}/preference`)
    const update = (items: CatalogExercise[] | null) => items?.map(e => e.id === id ? { ...e, preference: value } : e) ?? null
    setExercises(update); setProblems(update)
  }
  const renderCards = (items: CatalogExercise[] | null) => {
    if (items === null) return detailError ? null : <Loader size="sm" />
    const shown = filtered(items)
    return shown.length ? <Stack gap="xl">{[1, 2, 3].map(difficulty => {
      const group = shown.filter(ex => exerciseDifficulty(ex) === difficulty)
      if (!group.length) return null
      return <Stack key={difficulty} gap="sm" component="section" aria-label={['Faciles', 'Moyens', 'Difficiles'][difficulty - 1]}>
        <Group gap="xs" style={{ borderBottom: '1px solid var(--mantine-color-default-border)', paddingBottom: 8 }}>
          <Title order={4}>{['Faciles', 'Moyens', 'Difficiles'][difficulty - 1]}</Title>
          <Text size="xs" c="dimmed">{group.length} exercice{group.length > 1 ? 's' : ''}</Text>
        </Group>
        <ExerciseMasonry>{group.map(ex => <ExerciseCatalogCard key={ex.id} exercise={ex} admin={admin}
          onPreference={admin ? undefined : value => changePreference(ex.id, value)} />)}</ExerciseMasonry>
      </Stack>
    })}</Stack>
      : <Text c="dimmed" size="sm">Aucun exercice disponible pour cette sélection.</Text>
  }
  const selectedIndex = selected ? (summary ?? []).indexOf(selected) : -1
  const previous = summary?.[selectedIndex - 1]
  const next = selectedIndex >= 0 ? summary?.[selectedIndex + 1] : undefined
  const navigation = <Group justify="space-between" gap="sm" wrap="wrap">
    <Button variant="subtle" leftSection={<ArrowLeft size={15} />} onClick={back}>Revenir à la liste</Button>
    <Group gap="xs" style={{ flex: 1, justifyContent: 'flex-end' }}>
      <Button variant="light" disabled={!previous} leftSection={<ChevronLeft size={16} />} onClick={() => previous && open(previous)}
        styles={{ root: { height: 'auto', minHeight: 36, maxWidth: '100%' }, label: { whiteSpace: 'normal', textAlign: 'left', padding: '6px 0' } }}>
        {previous ? `${previous.short_id} — ${previous.label}` : 'Première compétence'}
      </Button>
      <Button variant="light" disabled={!next} rightSection={<ChevronRight size={16} />} onClick={() => next && open(next)}
        styles={{ root: { height: 'auto', minHeight: 36, maxWidth: '100%' }, label: { whiteSpace: 'normal', textAlign: 'left', padding: '6px 0' } }}>
        {next ? `${next.short_id} — ${next.label}` : 'Dernière compétence'}
      </Button>
    </Group>
  </Group>

  if (grade === 'all') return <GradeSelectionRequired title={title} noClass={!admin && !teaching.cycles.length} />
  return <Stack gap="md" ref={detailRef}>
    <Group justify="space-between" align="center">
      <Title order={2}><Group gap={8}><Library size={22} />{title}</Group></Title>
      <Group gap="xs" wrap="wrap">
        {!selected && <TextInput aria-label="Rechercher une compétence" placeholder="Rechercher une compétence" leftSection={<Search size={15} />}
          value={search} onChange={e => setSearch(e.currentTarget.value)} />}
        {selected && <>
          {!admin && <SegmentedControl size="xs" aria-label="Préférence" value={preference} onChange={setPreference}
            data={[{ value: 'all', label: 'Tous' }, { value: 'like', label: 'Aimés' }, { value: 'dislike', label: 'Écartés' }]} />}
          <SegmentedControl size="xs" aria-label="Difficulté" value={level} onChange={setLevel}
            data={[{ value: 'all', label: 'Toutes' }, { value: '1', label: 'Facile' }, { value: '2', label: 'Moyen' }, { value: '3', label: 'Difficile' }]} />
        </>}
      </Group>
    </Group>
    {error && <Alert color="red">{error}</Alert>}
    {!selected ? <>
      {admin && <BankHealthPanel />}
      {summary === null ? (!error && (admin || frameworkIds) ? <Loader /> : !error && <Text c="dimmed">Aucune compétence disponible pour ce cycle.</Text>) : summary.length === 0 ? <Text c="dimmed">Aucune compétence disponible pour ce cycle.</Text> :
        <ScrollArea><CompetencyHierarchy domains={domains} columns={columns} tableMinWidth={360 + columns.length * 100}
          getRowKey={s => s.competency_id} getShortId={s => s.short_id || s.code || ''} getLabel={s => s.label}
          onRowClick={open} chapterAside={chapter => <Text size="xs" c="dimmed">
            {chapter.rows.reduce((sum, row) => sum + (row.total ?? (row.draft ?? 0) + (row.validated ?? 0)), 0)} exercices
          </Text>} /></ScrollArea>}
    </> : <>
      {navigation}
      <Box>
        <Title order={3}>{selected.short_id || selected.code} — {selected.label}</Title>
        <Text size="sm" c="dimmed">{selected.chapter_code} — {selected.chapter_name}</Text>
      </Box>
      {detailError && <Alert color="red">{detailError}</Alert>}
      {renderCards(exercises)}
      {navigation}
      <Paper p="md" radius="md" withBorder>
        <Stack gap="md">
          <Box>
            <Title order={3}>Problèmes du chapitre entier</Title>
            <Text size="sm" c="dimmed">{selected.chapter_name} · Toutes compétences confondues ({summary?.filter(s => sameChapter(s, selected)).length ?? 0} compétences)</Text>
          </Box>
          {renderCards(problems)}
        </Stack>
      </Paper>
    </>}
  </Stack>
}
