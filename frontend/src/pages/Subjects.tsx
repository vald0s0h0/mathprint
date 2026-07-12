// Écran Sujets : assistant en 4 étapes (§3.1), recherche MathALÉA/builtin,
// aperçu PDF intégré et impression directe. Les sujets sont groupés par
// classe et filtrés par le cycle global.
import {
  Alert, Badge, Button, Card, Checkbox, Divider, Group, Modal, NumberInput,
  Radio, ScrollArea, SegmentedControl, Select, Stack, Stepper, Text, TextInput,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Eye, FileText, Plus, Sparkles } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import MathText from '../components/MathText'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import { useAppState } from '../state/AppState'

type Cls = { id: string; name: string; grade_level: string }
type Exo = {
  id: string; title: string; difficulty: number; response_type: string
  automation_tier: string; provider: string; provider_ref: string; grade_level: string
}
type Assessment = {
  id: string; title: string; type: string; status: string
  class_name: string; class_id: string; grade_level: string
  personalization_mode: string
}

const MODES = [
  { value: 'common', label: 'Commun', desc: 'Le même sujet pour toute la classe' },
  { value: 'equivalent_variants', label: 'Variantes équivalentes', desc: 'Mêmes exercices, nombres différents (anti-copie)' },
  { value: 'guided_individual', label: 'Individuel encadré', desc: 'Difficulté adaptée au niveau, blueprint commun' },
  { value: 'free_individual', label: 'Individuel libre', desc: 'Chaque copie optimisée pour l’élève' },
]

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  draft: { label: 'brouillon', color: 'gray' },
  generated: { label: 'généré', color: 'blue' },
  printed: { label: 'imprimé', color: 'cyan' },
  scanning: { label: 'scan en cours', color: 'orange' },
  finalized: { label: 'corrigé', color: 'green' },
}

export default function Subjects() {
  const [list, setList] = useState<Assessment[]>([])
  const [classes, setClasses] = useState<Cls[]>([])
  const [exercises, setExercises] = useState<Exo[]>([])
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const { cycle, matches } = useAppState()
  const [params, setParams] = useSearchParams()

  // étape 1 : contexte
  const [classId, setClassId] = useState<string | null>(null)
  const [type, setType] = useState('training')
  const [title, setTitle] = useState('')
  const [pages, setPages] = useState(1)
  // compétences IA (exercices DeepSeek)
  const [competencies, setCompetencies] = useState<{ id: string; code: string; label: string }[]>([])
  const [aiComp, setAiComp] = useState<string | null>(null)
  const [aiBusy, setAiBusy] = useState(false)
  // étape 2 : objectif
  const [selectedEx, setSelectedEx] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [provider, setProvider] = useState('all')
  const [suggestReason, setSuggestReason] = useState('')
  // étape 3/4
  const [mode, setMode] = useState('common')
  const [assessmentId, setAssessmentId] = useState<string | null>(null)
  const [report, setReport] = useState<{ copies: number; warnings: string[] } | null>(null)
  const [generating, setGenerating] = useState(false)

  function refresh() {
    api.get<Assessment[]>('/api/assessments').then(setList)
    api.get<Cls[]>('/api/classes').then(setClasses)
  }
  useEffect(refresh, [])

  // ouverture directe depuis le Dashboard (+ Créer un sujet)
  useEffect(() => {
    if (params.get('nouveau')) {
      setOpen(true)
      params.delete('nouveau')
      setParams(params, { replace: true })
    }
  }, [params, setParams])

  const cycleClasses = classes.filter((c) => matches(c.grade_level))
  const grade = classes.find((c) => c.id === classId)?.grade_level

  useEffect(() => {
    if (!grade) return
    api.get<{ id: string; grade_level: string }[]>('/api/competencies/frameworks').then(async (fws) => {
      const fw = fws.find((f) => f.grade_level === grade)
      if (fw) {
        const comps = await api.get<{ id: string; code: string; label: string }[]>(
          `/api/competencies?framework_id=${fw.id}`)
        setCompetencies(comps)
      }
    })
  }, [grade])
  useEffect(() => {
    const p = new URLSearchParams()
    if (grade) p.set('grade_level', grade)
    if (search) p.set('search', search)
    if (provider !== 'all') p.set('provider', provider)
    p.set('limit', '120')
    api.get<Exo[]>(`/api/assessments/exercises?${p}`).then(setExercises)
  }, [grade, search, provider])

  const groups = useMemo(() => {
    const filtered = list.filter((a) => matches(a.grade_level))
    const by = new Map<string, { cls: string; grade: string; rows: Assessment[] }>()
    for (const a of filtered) {
      const key = a.class_id || a.class_name
      if (!by.has(key)) by.set(key, { cls: a.class_name, grade: a.grade_level, rows: [] })
      by.get(key)!.rows.push(a)
    }
    return [...by.values()].sort((x, y) => x.cls.localeCompare(y.cls))
  }, [list, matches])

  async function addAiExercise() {
    if (!aiComp) return
    setAiBusy(true)
    try {
      const r = await api.post<{ id: string; title: string }>(
        '/api/assessments/exercises/ai-prepare', { competency_id: aiComp })
      setSelectedEx((prev) => (prev.includes(r.id) ? prev : [...prev, r.id]))
      setExercises((prev) => (prev.some((e) => e.id === r.id) ? prev
        : [{ id: r.id, title: r.title, difficulty: 5, response_type: 'short_text',
             automation_tier: 'auto', provider: 'deepseek', provider_ref: '',
             grade_level: grade ?? '' }, ...prev]))
      notifications.show({ color: 'green', message: `${r.title} — banque prête` })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setAiBusy(false)
    }
  }

  async function createDraft() {
    const r = await api.post<{ id: string }>('/api/assessments', {
      class_id: classId, type, title: title || 'Sans titre',
      pages, personalization_mode: mode,
    })
    setAssessmentId(r.id)
    const s = await api.get<{ exercise_ids: string[]; reason: string }>(
      `/api/assessments/${r.id}/suggestion`)
    setSelectedEx(s.exercise_ids)
    setSuggestReason(s.reason)
    setStep(1)
  }

  async function generate() {
    if (!assessmentId) return
    setGenerating(true)
    try {
      const r = await api.post<{ copies: number; warnings: string[] }>(
        `/api/assessments/${assessmentId}/generate`, { exercise_ids: selectedEx })
      setReport(r)
      notifications.show({ color: 'green', message: `${r.copies} copies générées` })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setGenerating(false)
    }
  }

  function reset() {
    setOpen(false); setStep(0); setAssessmentId(null); setReport(null)
    setSelectedEx([]); setTitle(''); setSuggestReason(''); setSearch('')
  }

  const shownSelected = new Set(selectedEx)

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2}>Sujets</Title>
          <Text size="sm" c="dimmed">
            {cycle === 'all' ? 'Tous les cycles' : `Cycle ${cycle}`} — groupés par classe
          </Text>
        </div>
        <Button leftSection={<Plus size={18} />} onClick={() => setOpen(true)}>
          Créer un sujet
        </Button>
      </Group>

      {groups.length === 0 && (
        <Card withBorder padding="xl">
          <Stack align="center" gap="xs">
            <FileText size={36} strokeWidth={1.4} opacity={0.5} />
            <Text fw={600}>Aucun sujet {cycle !== 'all' && `en ${cycle}`}</Text>
            <Text size="sm" c="dimmed" ta="center">
              Créez votre premier sujet : choix de la classe, des exercices,
              du mode d'adaptation, puis génération des copies PDF.
            </Text>
            <Button mt="xs" leftSection={<Plus size={16} />} onClick={() => setOpen(true)}>
              Créer un sujet
            </Button>
          </Stack>
        </Card>
      )}

      {groups.map((g) => (
        <div key={g.cls}>
          <Group gap={8} mb="xs">
            <Text fw={700}>{g.cls}</Text>
            <Badge size="sm" variant="light">{g.grade}</Badge>
            <Text size="xs" c="dimmed">{g.rows.length} sujet(s)</Text>
          </Group>
          <Stack gap="xs">
            {g.rows.map((a) => {
              const st = STATUS_LABEL[a.status] ?? { label: a.status, color: 'gray' }
              return (
                <Card key={a.id} withBorder padding="sm">
                  <Group justify="space-between" wrap="nowrap">
                    <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
                      <Badge variant="light" color={a.type === 'control' ? 'red' : 'blue'} w={104}>
                        {a.type === 'control' ? 'Contrôle' : 'Entraînement'}
                      </Badge>
                      <Text fw={600} lineClamp={1}>{a.title}</Text>
                      <Badge size="sm" variant="dot" color={st.color}>{st.label}</Badge>
                    </Group>
                    {a.status !== 'draft' && (
                      <Group gap="xs" wrap="nowrap">
                        <Button size="xs" variant="light" leftSection={<Eye size={14} />}
                          onClick={() => setPreviewId(a.id)}>
                          Aperçu
                        </Button>
                        <PrintButton assessmentId={a.id} file="subject_batch.pdf"
                          label="Imprimer les sujets" />
                        {a.status === 'finalized' && (
                          <PrintButton assessmentId={a.id} file="correction_overlay.pdf"
                            label="Imprimer l'overlay" />
                        )}
                      </Group>
                    )}
                  </Group>
                </Card>
              )
            })}
          </Stack>
        </div>
      ))}

      <PdfPreviewModal assessmentId={previewId} opened={!!previewId}
        onClose={() => setPreviewId(null)} />

      <Modal opened={open} onClose={reset} title={<Text fw={650}>Créer un sujet</Text>} size="xl">
        <Stepper active={step} onStepClick={setStep} allowNextStepsSelect={false} size="sm">
          <Stepper.Step label="Contexte">
            <Stack mt="md">
              <Select label="Classe" required value={classId} onChange={setClassId}
                placeholder={cycleClasses.length ? 'Choisir une classe'
                  : `Aucune classe ${cycle !== 'all' ? `en ${cycle}` : ''} — créez-en une dans Élèves`}
                data={cycleClasses.map((c) => ({ value: c.id, label: `${c.name} (${c.grade_level})` }))} />
              <Radio.Group label="Type" value={type} onChange={setType}>
                <Group mt="xs">
                  <Radio value="training" label="Entraînement" />
                  <Radio value="control" label="Contrôle noté" />
                </Group>
              </Radio.Group>
              <TextInput label="Titre" placeholder="ex. Fractions — semaine 12"
                value={title} onChange={(e) => setTitle(e.target.value)} />
              <NumberInput label="Nombre de pages" value={pages} min={1} max={6}
                description="1 = recto seul, 2 = recto/verso, 3+ = feuilles supplémentaires"
                onChange={(v) => setPages(Number(v) || 1)} />
              <Button onClick={createDraft} disabled={!classId}>Continuer</Button>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Exercices">
            <Stack mt="md" gap="xs">
              {suggestReason && <Alert color="blue" p="xs">{suggestReason}</Alert>}
              <Group gap="xs" align="flex-end">
                <Select label="Créer un exercice IA ciblé sur une compétence"
                  placeholder="Rechercher une compétence du programme…"
                  searchable size="xs" style={{ flex: 1 }}
                  data={competencies.map((c) => ({ value: c.id, label: c.label }))}
                  value={aiComp} onChange={setAiComp} limit={30} />
                <Button size="xs" color="grape" onClick={addAiExercise}
                  leftSection={<Sparkles size={14} />} loading={aiBusy} disabled={!aiComp}>
                  Ajouter (5 niveaux)
                </Button>
              </Group>
              <Divider />
              <Group>
                <TextInput placeholder="Rechercher un exercice…" value={search} size="xs"
                  onChange={(e) => setSearch(e.target.value)} style={{ flex: 1 }} />
                <SegmentedControl size="xs" value={provider} onChange={setProvider}
                  data={[{ value: 'all', label: 'Tous' },
                         { value: 'deepseek', label: 'IA' },
                         { value: 'mathalea', label: 'MathALÉA' },
                         { value: 'builtin', label: 'Intégrés' }]} />
                <Badge variant="light">{selectedEx.length} sélectionné(s)</Badge>
              </Group>
              <ScrollArea h={330}>
                <Stack gap={4}>
                  {exercises.map((e) => (
                    <Group key={e.id} gap="xs" wrap="nowrap">
                      <Checkbox
                        checked={shownSelected.has(e.id)}
                        onChange={(ev) => setSelectedEx(ev.target.checked
                          ? [...selectedEx, e.id] : selectedEx.filter((x) => x !== e.id))} />
                      <Badge size="xs" variant="light"
                        color={e.provider === 'mathalea' ? 'teal' : e.provider === 'deepseek' ? 'grape' : 'blue'}>
                        {e.provider === 'mathalea' ? 'MathALÉA'
                          : e.provider === 'deepseek' ? 'IA ×5 niv.' : 'intégré'}
                      </Badge>
                      <Text size="sm" style={{ flex: 1 }} lineClamp={1}>
                        <MathText text={e.title} />
                      </Text>
                      <Badge size="xs" color="gray" variant="light">difficulté {e.difficulty}</Badge>
                      {e.automation_tier !== 'auto' && (
                        <Badge size="xs" color="orange" variant="light">revue</Badge>
                      )}
                    </Group>
                  ))}
                </Stack>
              </ScrollArea>
              <Button onClick={() => setStep(2)} disabled={!selectedEx.length}>Continuer</Button>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Adaptation">
            <Stack mt="md">
              <Radio.Group label="Personnalisation des copies" value={mode} onChange={setMode}>
                <Stack mt="xs" gap="xs">
                  {MODES.map((m) => (
                    <Radio key={m.value} value={m.value}
                      label={<span><Text component="span" fw={550} size="sm">{m.label}</Text>
                        <Text component="span" size="xs" c="dimmed"> — {m.desc}</Text></span>} />
                  ))}
                </Stack>
              </Radio.Group>
              {type === 'control' && mode !== 'common' && (
                <Alert color="orange">
                  Règle d'équité : un contrôle personnalisé conserve un blueprint commun de
                  compétences ; les notes brutes ne seront pas naïvement comparables.
                </Alert>
              )}
              <Button onClick={() => setStep(3)}>Continuer</Button>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Génération">
            <Stack mt="md">
              <Text size="sm">{selectedEx.length} exercice(s) par copie.</Text>
              {report ? (
                <>
                  <Alert color="green">{report.copies} copies générées.</Alert>
                  {report.warnings.map((w, i) => <Alert key={i} color="orange">{w}</Alert>)}
                  <Group>
                    <Button leftSection={<Eye size={16} />}
                      onClick={() => setPreviewId(assessmentId)}>
                      Aperçu des copies
                    </Button>
                    {assessmentId && <PrintButton assessmentId={assessmentId}
                      file="subject_batch.pdf" label="Imprimer le lot" size="sm" />}
                    <Button variant="light" onClick={reset}>Fermer</Button>
                  </Group>
                </>
              ) : (
                <Button onClick={generate} loading={generating}>Générer le PDF</Button>
              )}
            </Stack>
          </Stepper.Step>
        </Stepper>
      </Modal>
    </Stack>
  )
}
