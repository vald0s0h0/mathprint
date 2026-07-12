// Écran Sujets : assistant en 4 étapes (contexte, exercices, adaptation,
// génération). La génération tourne dans un worker de fond côté API : la
// modale se ferme dès la mise en file, et la liste (groupée par classe,
// filtrée par le cycle global) affiche la progression jusqu'à "prêt".
import {
  Alert, Badge, Button, Card, Group, Modal, NumberInput, Radio, Select,
  Stack, Stepper, Text, TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { AlertTriangle, Eye, FileText, Plus, RotateCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import AdaptationStep from './subjects/AdaptationStep'
import CompetencyMatrixStep from './subjects/CompetencyMatrixStep'
import { useAppState } from '../state/AppState'

type Cls = { id: string; name: string; grade_level: string }
type Assessment = {
  id: string; title: string; type: string; status: string
  class_name: string; class_id: string; grade_level: string
  personalization_mode: string; error_message: string | null
}

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  draft: { label: 'brouillon', color: 'gray' },
  queued: { label: 'en file', color: 'yellow' },
  generating: { label: 'génération…', color: 'orange' },
  ready: { label: 'prêt', color: 'blue' },
  error: { label: 'échec', color: 'red' },
  printed: { label: 'imprimé', color: 'cyan' },
  scanning: { label: 'scan en cours', color: 'orange' },
  finalized: { label: 'corrigé', color: 'green' },
}

export default function Subjects() {
  const [list, setList] = useState<Assessment[]>([])
  const [classes, setClasses] = useState<Cls[]>([])
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
  // étape 2 : compétences cochées
  const [competencyIds, setCompetencyIds] = useState<string[]>([])
  const [suggestReason, setSuggestReason] = useState('')
  // étape 3 : adaptation
  const [mode, setMode] = useState('common')
  // étape 4
  const [assessmentId, setAssessmentId] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)

  const refresh = useCallback(() => {
    api.get<Assessment[]>('/api/assessments').then(setList)
    api.get<Cls[]>('/api/classes').then(setClasses)
  }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 4000)
    return () => clearInterval(t)
  }, [refresh])

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

  async function createDraft() {
    const r = await api.post<{ id: string }>('/api/assessments', {
      class_id: classId, type, title: title || 'Sans titre', pages,
    })
    setAssessmentId(r.id)
    try {
      const s = await api.get<{ competency_ids: string[]; reason: string }>(
        `/api/assessments/${r.id}/suggested-competencies`)
      setCompetencyIds(s.competency_ids)
      setSuggestReason(s.reason)
    } catch { /* proposition facultative */ }
    setStep(1)
  }

  async function confirmCompetencies() {
    if (!assessmentId) return
    await api.patch(`/api/assessments/${assessmentId}`, { competency_ids: competencyIds })
    setStep(2)
  }

  async function confirmAdaptation() {
    if (!assessmentId) return
    await api.patch(`/api/assessments/${assessmentId}`, { personalization_mode: mode })
    setStep(3)
  }

  async function generate() {
    if (!assessmentId) return
    setGenerating(true)
    try {
      await api.post(`/api/assessments/${assessmentId}/generate`, { font_size: 10 })
      notifications.show({ color: 'blue', message: 'Sujet en file de génération' })
      reset()
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setGenerating(false)
    }
  }

  async function retry(a: Assessment) {
    try {
      await api.post(`/api/assessments/${a.id}/generate`, { font_size: 10 })
      notifications.show({ color: 'blue', message: 'Nouvel essai en file de génération' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  function reset() {
    setOpen(false); setStep(0); setAssessmentId(null)
    setCompetencyIds([]); setTitle(''); setSuggestReason('')
    setMode('common'); setType('training'); setPages(1)
  }

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
              Créez votre premier sujet : choix de la classe, des compétences,
              du mode d'adaptation, puis génération en file de fond.
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
                    <Group gap="xs" wrap="nowrap">
                      {a.status === 'error' && (
                        <Button size="xs" color="red" variant="light"
                          leftSection={<RotateCcw size={14} />} onClick={() => retry(a)}>
                          Réessayer
                        </Button>
                      )}
                      {['ready', 'printed', 'scanning', 'finalized'].includes(a.status) && (
                        <>
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
                        </>
                      )}
                    </Group>
                  </Group>
                  {a.status === 'error' && a.error_message && (
                    <Alert mt="xs" color="red" p="xs" icon={<AlertTriangle size={14} />}>
                      {a.error_message}
                    </Alert>
                  )}
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
              <CompetencyMatrixStep gradeLevel={grade} selected={competencyIds}
                onChange={setCompetencyIds} />
              <Button onClick={confirmCompetencies} disabled={!competencyIds.length}>
                Continuer
              </Button>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Adaptation">
            <AdaptationStep mode={mode} onChange={setMode} type={type} />
            <Button mt="md" onClick={confirmAdaptation}>Continuer</Button>
          </Stepper.Step>

          <Stepper.Step label="Génération">
            <Stack mt="md">
              <Text size="sm">{competencyIds.length} compétence(s) sélectionnée(s).</Text>
              <Text size="xs" c="dimmed">
                La génération (et, si besoin, la création d'exercices manquants) se fait en
                file de fond : la fenêtre se ferme aussitôt, le sujet apparaît dans la liste
                dès qu'il est prêt.
              </Text>
              <Button onClick={generate} loading={generating}>Générer le sujet</Button>
            </Stack>
          </Stepper.Step>
        </Stepper>
      </Modal>
    </Stack>
  )
}
