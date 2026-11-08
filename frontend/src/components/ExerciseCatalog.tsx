import { Alert, Box, Button, Group, Loader, Modal, Paper, ScrollArea, SegmentedControl, Stack, Switch, Text, TextInput, Title, Tooltip, VisuallyHidden } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { ArrowLeft, ChevronLeft, ChevronRight, Library, Search, Send } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useAppState } from '../state/AppState'
import CompetencyHierarchy, { type CompetencyHierarchyColumn } from './CompetencyHierarchy'
import GradeSelectionRequired from './GradeSelectionRequired'
import ExerciseCatalogCard, { exerciseDifficulty, type CatalogExercise, type ReviewVerdict } from './ExerciseCatalogCard'
import ExerciseMasonry from './ExerciseMasonry'
import MathText from './MathText'
import BankHealthPanel from './BankHealthPanel'

type Summary = {
  competency_id: string; code?: string; short_id: string; label: string; grade_level?: string
  domain_code: string; domain_name: string; chapter_code: string; chapter_name: string
  needs_review?: number; needs_update?: number; framework_id?: string; total?: number; problems?: number; draft?: number; validated?: number; published?: number
}
type Framework = { id: string; grade_level: string; subject_id: string; program_id: string }
type PublicationSelection = {
  grade_level: string; program_id: string; subject_id: string; competency_ids: string[]
  pending: number; skipped: number; unsettled: number; fingerprint: string
}
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
  const [onlyProblems, setOnlyProblems] = useState(false)
  const [confirmPublish, setConfirmPublish] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [publicationSelection, setPublicationSelection] = useState<PublicationSelection | null>(null)
  const [preparingPublication, setPreparingPublication] = useState(false)
  const publicationRequest = useRef(0)
  const detailRef = useRef<HTMLDivElement>(null)
  const title = admin ? 'Exercices' : 'Banque de contenus'
  const frameworkIds = frameworks.filter(f => f.grade_level === grade && f.subject_id === subjectId && teaching.programs.includes(f.program_id)).map(f => f.id).join(',')
  const scope = admin
    ? `grade_level=${grade}&program_id=${catalog.program}&subject_id=${subjectId}`
    : `grade_level=${grade}&subject_id=${subjectId}`
  const endpoint = admin ? '/api/indigo' : '/api/content'
  const selected = summary?.find(s => s.competency_id === params.get('competency')) ?? null
  const selectedId = selected?.competency_id
  useEffect(() => {
    publicationRequest.current += 1
    setPublicationSelection(null); setPreparingPublication(false)
  }, [scope, search, onlyProblems])

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
    if (!selectedId) return
    let current = true
    const id = encodeURIComponent(selectedId)
    Promise.all([
      api.get<CatalogExercise[]>(`${endpoint}/exercises?competency_id=${id}&category=exercise`),
      api.get<CatalogExercise[]>(`${endpoint}/exercises?chapter_id=${id}`),
    ]).then(([items, chapter]) => {
      if (current) { setExercises(items); setProblems(chapter) }
    }).catch(e => { if (current) setDetailError(e.message) })
    return () => { current = false }
  }, [selectedId, endpoint])

  useEffect(() => {
    if (!admin || grade === 'all') return
    let current = true
    const refresh = async () => {
      try {
        const rows = await api.get<Summary[]>(`${endpoint}/summary?${scope}`)
        if (current) setSummary(rows)
        if (selectedId) {
          const id = encodeURIComponent(selectedId)
          const [items, chapter] = await Promise.all([
            api.get<CatalogExercise[]>(`${endpoint}/exercises?competency_id=${id}&category=exercise`),
            api.get<CatalogExercise[]>(`${endpoint}/exercises?chapter_id=${id}`),
          ])
          if (current) { setExercises(items); setProblems(chapter) }
        }
      } catch { /* Les données restent visibles ; le prochain rafraîchissement réessaie. */ }
    }
    window.addEventListener('focus', refresh)
    const timer = window.setInterval(refresh, 30000)
    return () => { current = false; window.removeEventListener('focus', refresh); window.clearInterval(timer) }
  }, [admin, grade, scope, endpoint, selectedId])

  const mastery = useMemo(() => {
    const out = new Map<string, Record<string, number | null>>()
    for (const d of matrix?.domains ?? []) for (const ch of d.chapters)
      for (const c of ch.competencies) out.set(c.id, c.mastery_by_class)
    return out
  }, [matrix])
  const columns = useMemo<CompetencyHierarchyColumn<Summary>[]>(() => [
    { key: 'total', label: <VisuallyHidden>Total</VisuallyHidden>, width: 72, align: 'center', render: s => <Text size="sm">{s.total ?? (s.draft ?? 0) + (s.validated ?? 0)}</Text> },
    ...(admin ? [{ key: 'published', label: 'Publiés', width: 80, align: 'center' as const, render: (s: Summary) => <Text size="sm">{s.published ?? 0}</Text> }] : []),
    ...(admin ? [{ key: 'needs_update', label: 'Mettre à jour', width: 115, align: 'center' as const, render: (s: Summary) => <Tooltip label="Exercices déjà publiés dont la version actuelle diffère de la version en banque"><Text size="sm" fw={s.needs_update ? 650 : undefined} c={s.needs_update ? 'orange' : 'dimmed'}>{s.needs_update ?? 0}</Text></Tooltip> }] : []),
    ...(admin ? [{ key: 'needs_review', label: 'À reprendre', width: 110, align: 'center' as const, render: (s: Summary) => <Text size="sm" c={s.needs_review ? 'red' : 'dimmed'}>{s.needs_review ?? 0}</Text> }] : []),
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
      if (onlyProblems && !(s.needs_review ?? 0)) continue
      if (search && !`${s.short_id} ${s.label} ${s.chapter_name}`.toLocaleLowerCase('fr').includes(search.toLocaleLowerCase('fr'))) continue
      const key = `${s.framework_id ?? s.grade_level ?? grade}/${s.domain_code}`
      if (!groups.has(key)) groups.set(key, { key, code: s.domain_code, name: s.domain_name, chapters: [] })
      const domain = groups.get(key)!
      let chapter = domain.chapters.find(c => c.code === s.chapter_code)
      if (!chapter) { chapter = { key: `${key}/${s.chapter_code}`, code: s.chapter_code, name: s.chapter_name, rows: [] }; domain.chapters.push(chapter) }
      chapter.rows.push(s)
    }
    return [...groups.values()]
  }, [summary, search, grade, onlyProblems])

  const open = (row: Summary) => {
    setParams(current => { const next = new URLSearchParams(current); next.set('competency', row.competency_id); return next })
    setLevel('all'); setPreference('all')
    requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: 'start' }))
  }
  const back = () => setParams(current => { const next = new URLSearchParams(current); next.delete('competency'); return next })
  const filtered = (items: CatalogExercise[]) => items.filter(e =>
    (!onlyProblems || e.needs_review) &&
    (level === 'all' || exerciseDifficulty(e) === Number(level)) && (preference === 'all' || e.preference === preference))
    .sort((a, b) => exerciseDifficulty(a) - exerciseDifficulty(b))
  const changePreference = async (id: string, value: 'like' | 'dislike' | null) => {
    if (value) await api.put(`/api/content/exercises/${id}/preference`, { value })
    else await api.del(`/api/content/exercises/${id}/preference`)
    const update = (items: CatalogExercise[] | null) => items?.map(e => e.id === id ? { ...e, preference: value } : e) ?? null
    setExercises(update); setProblems(update)
  }
  const changeReview = async (id: string, value: ReviewVerdict | null, comment?: string) => {
    const saved = await api.put<Pick<CatalogExercise, 'review_comment' | 'review_history' | 'needs_review'>>(`/api/indigo/exercises/${id}/review-verdict`, { value, comment })
    const update = (items: CatalogExercise[] | null) => items?.map(e => e.id === id ? { ...e, ...saved, review_verdict: value } : e) ?? null
    setExercises(update); setProblems(update)
    try { setSummary(await api.get<Summary[]>(`${endpoint}/summary?${scope}`)) }
    catch { notifications.show({ color: 'red', message: 'Avis enregistré. Actualisez la page pour mettre les compteurs à jour.' }) }
  }
  const drafts = (exercises ?? []).filter(e => e.status === 'draft' || e.needs_update)
  const toPublish = drafts.filter(e => e.review_verdict !== 'dislike')
  const setAside = drafts.length - toPublish.length
  const unsettled = toPublish.filter(e => e.review_notes?.length && !e.review_verdict).length
  const publishAll = async () => {
    if (!selectedId) return
    setPublishing(true)
    try {
      const result = await api.post<{ published: number; skipped: number }>(`/api/indigo/competencies/${encodeURIComponent(selectedId)}/publish`)
      const id = encodeURIComponent(selectedId)
      setExercises(await api.get<CatalogExercise[]>(`${endpoint}/exercises?competency_id=${id}&category=exercise`))
      setSummary(await api.get<Summary[]>(`${endpoint}/summary?${scope}`))
      notifications.show({ color: 'teal', message: `${result.published} exercice${result.published > 1 ? 's' : ''} publié${result.published > 1 ? 's' : ''}${result.skipped ? `, ${result.skipped} à reprendre laissé${result.skipped > 1 ? 's' : ''} de côté` : ''}.` })
      setConfirmPublish(false)
    } catch (e) {
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : 'Publication impossible.' })
    } finally { setPublishing(false) }
  }
  const preparePublicationSelection = async () => {
    const competency_ids = domains.flatMap(d => d.chapters.flatMap(ch => ch.rows.map(r => r.competency_id)))
    if (!competency_ids.length) return
    const selection = { grade_level: grade, program_id: catalog.program, subject_id: subjectId, competency_ids }
    const request = ++publicationRequest.current
    setPreparingPublication(true)
    try {
      const plan = await api.post<Omit<PublicationSelection, keyof typeof selection>>('/api/indigo/publish-selection/preview', selection)
      if (publicationRequest.current === request) setPublicationSelection({ ...selection, ...plan })
    } catch (e) {
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : 'Préparation impossible.' })
    } finally { if (publicationRequest.current === request) setPreparingPublication(false) }
  }
  const publishSelection = async () => {
    if (!publicationSelection) return
    setPublishing(true)
    try {
      const result = await api.post<{ published: number; skipped: number }>('/api/indigo/publish-selection', publicationSelection)
      setPublicationSelection(null)
      setSummary(await api.get<Summary[]>(`${endpoint}/summary?${scope}`))
      notifications.show({ color: 'teal', message: `${result.published} exercice(s) publié(s) / mis à jour ; ${result.skipped} disliké(s) laissé(s) de côté.` })
    } catch (e) {
      setPublicationSelection(null)
      notifications.show({ color: 'red', message: e instanceof Error ? e.message : 'Publication impossible.' })
    } finally { setPublishing(false) }
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
          onPreference={admin ? undefined : value => changePreference(ex.id, value)}
          onReview={admin ? (value, comment) => changeReview(ex.id, value, comment) : undefined} />)}</ExerciseMasonry>
      </Stack>
    })}</Stack>
      : <Text c="dimmed" size="sm">Aucun exercice disponible pour cette sélection.</Text>
  }
  const navigationRows = (summary ?? []).filter(s => !onlyProblems || (s.needs_review ?? 0) > 0)
  const selectedIndex = selected ? navigationRows.indexOf(selected) : -1
  const previous = navigationRows[selectedIndex - 1]
  const next = selectedIndex >= 0 ? navigationRows[selectedIndex + 1] : undefined
  const navigation = <Group justify="space-between" gap="sm" wrap="wrap">
    <Button variant="subtle" leftSection={<ArrowLeft size={15} />} onClick={back}>Revenir à la liste</Button>
    <Group gap="xs" style={{ flex: 1, justifyContent: 'flex-end' }}>
      <Button variant="light" disabled={!previous} leftSection={<ChevronLeft size={16} />} onClick={() => previous && open(previous)}
        styles={{ root: { height: 'auto', minHeight: 36, maxWidth: '100%' }, label: { whiteSpace: 'normal', textAlign: 'left', padding: '6px 0' } }}>
        {previous ? <>{previous.short_id} — <MathText text={previous.label} /></> : 'Première compétence'}
      </Button>
      <Button variant="light" disabled={!next} rightSection={<ChevronRight size={16} />} onClick={() => next && open(next)}
        styles={{ root: { height: 'auto', minHeight: 36, maxWidth: '100%' }, label: { whiteSpace: 'normal', textAlign: 'left', padding: '6px 0' } }}>
        {next ? <>{next.short_id} — <MathText text={next.label} /></> : 'Dernière compétence'}
      </Button>
    </Group>
  </Group>

  if (grade === 'all') return <GradeSelectionRequired title={title} noClass={!admin && !teaching.cycles.length} />
  return <Stack gap="md" ref={detailRef}>
    <Group justify="space-between" align="center">
      <Title order={2}><Group gap={8}><Library size={22} />{title}</Group></Title>
      <Group gap="xs" wrap="wrap">
        {admin && !selected && <Button leftSection={<Send size={15} />} loading={preparingPublication}
          disabled={!summary || !domains.length} onClick={preparePublicationSelection}>Tout publier / mettre à jour</Button>}
        {admin && <Switch label="Uniquement les exercices nécessitant mon intervention" checked={onlyProblems} onChange={e => setOnlyProblems(e.currentTarget.checked)} />}
        {!selected && <TextInput aria-label="Rechercher une compétence" placeholder="Rechercher une compétence" leftSection={<Search size={15} />}
          value={search} onChange={e => setSearch(e.currentTarget.value)} />}
        {selected && <>
          {!admin && <SegmentedControl size="xs" aria-label="Préférence" value={preference} onChange={setPreference}
            data={[{ value: 'all', label: 'Tous' }, { value: 'like', label: 'Aimés' }, { value: 'dislike', label: 'Écartés' }]} />}
          {admin && <Button size="xs" leftSection={<Send size={14} />} disabled={!toPublish.length} onClick={() => setConfirmPublish(true)}>
            {toPublish.length ? `Publier / mettre à jour (${toPublish.length})` : 'Rien à publier'}
          </Button>}
          <SegmentedControl size="xs" aria-label="Difficulté" value={level} onChange={setLevel}
            data={[{ value: 'all', label: 'Toutes' }, { value: '1', label: 'Facile' }, { value: '2', label: 'Moyen' }, { value: '3', label: 'Difficile' }]} />
        </>}
      </Group>
    </Group>
    {admin && summary !== null && <Text size="sm" fw={600} c={summary.some(s => s.needs_review) ? 'red' : 'teal'}>
      {summary.reduce((sum, s) => sum + (s.needs_review ?? 0), 0)} exercice(s) à surveiller / reprendre
      <Text component="span" size="xs" c="dimmed"> · Exercices et problèmes du cycle sélectionné. Les likes retirent le signalement ; seuls les dislikes seront repris par l’agent.</Text>
    </Text>}
    {admin && selected && !!selected.needs_update && <Text size="sm" c="orange">{selected.needs_update} exercice(s) publié(s) à mettre à jour avec leur version actuelle.</Text>}
    <Modal opened={publicationSelection !== null} onClose={() => !publishing && setPublicationSelection(null)}
      closeOnClickOutside={!publishing} closeOnEscape={!publishing} withCloseButton={!publishing}
      title={<Text fw={650}>Tout publier / mettre à jour</Text>}>
      {publicationSelection && <Stack gap="sm">
        <Text size="sm">{publicationSelection.grade_level} · {({ fr: 'France', ci: 'Côte d’Ivoire', all: 'Tous les pays' } as Record<string, string>)[publicationSelection.program_id] ?? publicationSelection.program_id} · {publicationSelection.subject_id === 'math' ? 'Mathématiques' : publicationSelection.subject_id}</Text>
        <Text size="sm">{publicationSelection.pending} exercice(s) et problème(s) à publier ou mettre à jour dans les {publicationSelection.competency_ids.length} compétences affichées.</Text>
        <Text size="sm" c="dimmed">Les versions publiées déjà à jour sont conservées.</Text>
        {publicationSelection.skipped > 0 && <Text size="sm">{publicationSelection.skipped} exercice(s) disliké(s) laissé(s) de côté.</Text>}
        {publicationSelection.unsettled > 0 && <Text size="sm" c="red">{publicationSelection.unsettled} exercice(s) portent encore un signalement non tranché et seront publiés.</Text>}
        <Group justify="flex-end">
          <Button variant="default" disabled={publishing} onClick={() => setPublicationSelection(null)}>Annuler</Button>
          <Button loading={publishing} disabled={!publicationSelection.pending} onClick={publishSelection}>Publier / mettre à jour ({publicationSelection.pending})</Button>
        </Group>
      </Stack>}
    </Modal>
    <Modal opened={confirmPublish} onClose={() => setConfirmPublish(false)} title={<Text fw={650}>Publier {selected?.short_id || selected?.code} ?</Text>}>
      <Stack gap="sm">
        <Text size="sm">{toPublish.length > 1 ? `${toPublish.length} exercices partiront en banque, utilisables` : '1 exercice partira en banque, utilisable'} par les professeurs.</Text>
        {setAside > 0 && <Text size="sm">{setAside} à reprendre (pouce baissé) reste{setAside > 1 ? 'nt' : ''} en brouillon.</Text>}
        {unsettled > 0 && <Text size="sm" c="red">{unsettled > 1 ? `${unsettled} ont encore un signalement que tu n'as pas tranché, et seront publiés` : `1 a encore un signalement que tu n'as pas tranché, et sera publié`} quand même.</Text>}
        <Group justify="flex-end" gap="xs">
          <Button variant="default" onClick={() => setConfirmPublish(false)}>Annuler</Button>
          <Button loading={publishing} onClick={publishAll}>Publier</Button>
        </Group>
      </Stack>
    </Modal>
    {error && <Alert color="red">{error}</Alert>}
    {!selected ? <>
      {admin && <BankHealthPanel />}
      {admin && onlyProblems && summary && domains.length === 0 && <Text c="teal">Aucun exercice ne nécessite votre intervention dans cette sélection.</Text>}
      {summary === null ? (!error && (admin || frameworkIds) ? <Loader /> : !error && <Text c="dimmed">Aucune compétence disponible pour ce cycle.</Text>) : summary.length === 0 ? <Text c="dimmed">Aucune compétence disponible pour ce cycle.</Text> :
        <ScrollArea><CompetencyHierarchy domains={domains} columns={columns} tableMinWidth={360 + columns.length * 100}
          getRowKey={s => s.competency_id} getShortId={s => s.short_id || s.code || ''} getLabel={s => s.label}
          onRowClick={open} columnsInChapterBand chapterCount={chapter => {
            const count = chapter.rows.reduce((sum, row) => sum + (row.total ?? (row.draft ?? 0) + (row.validated ?? 0)), 0)
            return `${count} exercice${count > 1 ? 's' : ''}`
          }} /></ScrollArea>}
    </> : <>
      {navigation}
      <Box>
        <Title order={3}>{selected.short_id || selected.code} — <MathText text={selected.label} /></Title>
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
