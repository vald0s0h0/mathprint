// Écran Sujets : assistant en 4 étapes (§3.1), recherche MathALÉA/builtin,
// aperçu PDF intégré (copie facile/médiane/difficile) et impression directe.
import {
  Alert, Badge, Button, Checkbox, Group, Modal, NumberInput, Radio,
  ScrollArea, SegmentedControl, Select, Stack, Stepper, Table, Text, TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useEffect, useState } from 'react'
import { api } from '../api'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'

type Cls = { id: string; name: string; grade_level: string }
type Exo = {
  id: string; title: string; difficulty: number; response_type: string
  automation_tier: string; provider: string; provider_ref: string; grade_level: string
}
type Assessment = { id: string; title: string; type: string; status: string; class_name: string; personalization_mode: string }

const MODES = [
  { value: 'common', label: 'Commun' },
  { value: 'equivalent_variants', label: 'Variantes équivalentes' },
  { value: 'guided_individual', label: 'Individuel encadré' },
  { value: 'free_individual', label: 'Individuel libre' },
]

export default function Subjects() {
  const [list, setList] = useState<Assessment[]>([])
  const [classes, setClasses] = useState<Cls[]>([])
  const [exercises, setExercises] = useState<Exo[]>([])
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)
  const [previewId, setPreviewId] = useState<string | null>(null)

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
    const params = new URLSearchParams()
    if (grade) params.set('grade_level', grade)
    if (search) params.set('search', search)
    if (provider !== 'all') params.set('provider', provider)
    params.set('limit', '120')
    api.get<Exo[]>(`/api/assessments/exercises?${params}`).then(setExercises)
  }, [grade, search, provider])

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
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Sujets</Title>
        <Button onClick={() => setOpen(true)}>+ Créer un sujet</Button>
      </Group>

      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Titre</Table.Th><Table.Th>Classe</Table.Th><Table.Th>Type</Table.Th>
            <Table.Th>Statut</Table.Th><Table.Th>Documents</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {list.map((a) => (
            <Table.Tr key={a.id}>
              <Table.Td>{a.title}</Table.Td>
              <Table.Td>{a.class_name}</Table.Td>
              <Table.Td>{a.type === 'control' ? 'Contrôle' : 'Entraînement'}</Table.Td>
              <Table.Td><Badge color={a.status === 'finalized' ? 'green' : a.status === 'draft' ? 'gray' : 'blue'}>{a.status}</Badge></Table.Td>
              <Table.Td>
                {a.status !== 'draft' && (
                  <Group gap="xs">
                    <Button size="xs" variant="light" onClick={() => setPreviewId(a.id)}>
                      👁 Voir l'aperçu
                    </Button>
                    <PrintButton assessmentId={a.id} file="subject_batch.pdf" label="Imprimer les sujets" />
                    {a.status === 'finalized' && (
                      <PrintButton assessmentId={a.id} file="correction_overlay.pdf" label="Imprimer l'overlay" />
                    )}
                  </Group>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <PdfPreviewModal assessmentId={previewId} opened={!!previewId}
        onClose={() => setPreviewId(null)} />

      <Modal opened={open} onClose={reset} title="Créer un sujet" size="xl">
        <Stepper active={step} onStepClick={setStep} allowNextStepsSelect={false}>
          <Stepper.Step label="Contexte">
            <Stack mt="md">
              <Select label="Classe" data={classes.map((c) => ({ value: c.id, label: `${c.name} (${c.grade_level})` }))}
                value={classId} onChange={setClassId} required />
              <Radio.Group label="Type" value={type} onChange={setType}>
                <Group mt="xs">
                  <Radio value="training" label="Entraînement" />
                  <Radio value="control" label="Contrôle noté" />
                </Group>
              </Radio.Group>
              <TextInput label="Titre" value={title} onChange={(e) => setTitle(e.target.value)} />
              <NumberInput label="Nombre de pages" value={pages} min={1} max={6}
                description="1 = recto seul, 2 = recto/verso, 3+ = feuilles supplémentaires"
                onChange={(v) => setPages(Number(v) || 1)} />
              <Button onClick={createDraft} disabled={!classId}>Continuer</Button>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Objectif">
            <Stack mt="md" gap="xs">
              {suggestReason && <Alert color="blue" p="xs">{suggestReason}</Alert>}
              <Group gap="xs" align="flex-end">
                <Select label="Créer un exercice IA ciblé sur une compétence (DeepSeek)"
                  placeholder="Rechercher une compétence du programme…"
                  searchable size="xs" style={{ flex: 1 }}
                  data={competencies.map((c) => ({ value: c.id, label: `${c.code} — ${c.label}` }))}
                  value={aiComp} onChange={setAiComp} limit={30} />
                <Button size="xs" color="grape" onClick={addAiExercise}
                  loading={aiBusy} disabled={!aiComp}>
                  ✦ Ajouter (5 niveaux)
                </Button>
              </Group>
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
              <ScrollArea h={340}>
                <Stack gap={4}>
                  {exercises.map((e) => (
                    <Group key={e.id} gap="xs" wrap="nowrap">
                      <Checkbox
                        checked={shownSelected.has(e.id)}
                        onChange={(ev) => setSelectedEx(ev.target.checked
                          ? [...selectedEx, e.id] : selectedEx.filter((x) => x !== e.id))} />
                      <Badge size="xs" variant="light"
                        color={e.provider === 'mathalea' ? 'teal' : e.provider === 'deepseek' ? 'grape' : 'blue'}>
                        {e.provider === 'mathalea' ? e.provider_ref.replace('mathalea:', '')
                          : e.provider === 'deepseek' ? 'IA ×5 niv.' : 'builtin'}
                      </Badge>
                      <Text size="sm" style={{ flex: 1 }} lineClamp={1}>{e.title}</Text>
                      <Badge size="xs" color="gray" variant="light">d{e.difficulty}</Badge>
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
              <Radio.Group label="Personnalisation" value={mode} onChange={setMode}>
                <Stack mt="xs" gap="xs">
                  {MODES.map((m) => <Radio key={m.value} value={m.value} label={m.label} />)}
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

          <Stepper.Step label="Validation">
            <Stack mt="md">
              <Text>{selectedEx.length} exercice(s) par copie.</Text>
              {report ? (
                <>
                  <Alert color="green">{report.copies} copies générées.</Alert>
                  {report.warnings.map((w, i) => <Alert key={i} color="orange">{w}</Alert>)}
                  <Group>
                    <Button onClick={() => setPreviewId(assessmentId)}>👁 Voir l'aperçu des copies</Button>
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
