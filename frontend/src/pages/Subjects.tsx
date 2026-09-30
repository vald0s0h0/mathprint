// Écran Sujets : la liste des sujets, groupés par classe et filtrés par le
// cycle global, et UN assistant « Créer un sujet » (création automatique
// personnalisée par élève, ou composition manuelle — cf. SubjectWizard). La
// génération tourne dans un worker de fond côté API : l'assistant se ferme dès
// la mise en file, et la liste affiche la progression jusqu'à "prêt".
import {
  Alert, Badge, Button, Card, Group, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { AlertTriangle, Copy, Eye, FileText, Plus, RotateCcw, ScrollText } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import GenerationLogModal from '../components/GenerationLogModal'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import SubjectWizard, { type WizardClass } from './subjects/SubjectWizard'
import { useAppState } from '../state/AppState'

type Cls = WizardClass
type Assessment = {
  id: string; title: string; type: string; status: string
  class_name: string; class_id: string; grade_level: string
  duplex: boolean
  personalization_mode: string; error_message: string | null
  // base de scoring du sujet ; un entraînement ne l'imprime pas
  note_base: number
  // sujet composé à la main (mode manuel) et nature de ses variantes :
  // '' | 'none' | 'anticheat' | 'level' ; ou personnalisé par élève (mode
  // automatique), avec ou sans problèmes
  manual: boolean; variant_kind: string; duplicate_version: number
  auto: boolean; problems: boolean
  overlay_distributed: boolean
}

const VARIANT_LABEL: Record<string, string> = {
  anticheat: 'variantes aléatoires',
  level: 'variantes par niveau',
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
  const [wizardOpen, setWizardOpen] = useState(false)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [logAssessment, setLogAssessment] = useState<Assessment | null>(null)
  const [duplicatingId, setDuplicatingId] = useState<string | null>(null)
  const { cycle, matches } = useAppState()
  const [params, setParams] = useSearchParams()

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
      setWizardOpen(true)
      params.delete('nouveau')
      setParams(params, { replace: true })
    }
  }, [params, setParams])

  const cycleClasses = useMemo(
    () => classes.filter((c) => matches(c.grade_level)), [classes, matches])

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

  async function retry(a: Assessment) {
    try {
      await api.post(`/api/assessments/${a.id}/generate`, { font_size: 10 })
      notifications.show({ color: 'blue', message: 'Nouvel essai en file de génération' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function duplicate(a: Assessment) {
    setDuplicatingId(a.id)
    try {
      const created = await api.post<{ title: string }>(`/api/assessments/${a.id}/duplicate`)
      notifications.show({
        color: 'blue',
        message: `« ${created.title} » est en file de génération`,
      })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setDuplicatingId(null)
    }
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
        <Button leftSection={<Plus size={18} />} onClick={() => setWizardOpen(true)}>
          Créer un sujet
        </Button>
      </Group>

      {groups.length === 0 && (
        <Card withBorder padding="xl">
          <Stack align="center" gap="xs">
            <FileText size={36} strokeWidth={1.4} opacity={0.5} />
            <Text fw={600}>Aucun sujet {cycle !== 'all' && `en ${cycle}`}</Text>
            <Text size="sm" c="dimmed" ta="center" maw={520}>
              Composez vos pages vous-même, ou laissez la plateforme préparer une
              copie personnalisée pour chaque élève dès que la classe compte assez
              de sujets corrigés.
            </Text>
            <Button mt="xs" leftSection={<Plus size={16} />} onClick={() => setWizardOpen(true)}>
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
              const done = a.overlay_distributed
              const st = done ? { label: 'terminé', color: 'gray' }
                : STATUS_LABEL[a.status] ?? { label: a.status, color: 'gray' }
              return (
                <Card key={a.id} withBorder padding="sm" style={done ? {
                  opacity: 0.55, background: 'var(--mantine-color-gray-1)',
                } : undefined}>
                  <Group justify="space-between" wrap="nowrap">
                    <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
                      <Badge variant="light" color={a.type === 'control' ? 'red' : 'blue'} w={104}>
                        {a.type === 'control' ? 'Contrôle' : 'Entraînement'}
                      </Badge>
                      <Tooltip label={`${a.type === 'control' ? 'Noté' : 'Scoré'} sur ${a.note_base} points`}>
                        <Badge size="sm" variant="outline"
                          color={a.type === 'control' ? 'red' : 'gray'}>/{a.note_base}</Badge>
                      </Tooltip>
                      <Text fw={600} lineClamp={1}>{a.title}</Text>
                      {a.duplicate_version > 1 && (
                        <Badge size="sm" variant="filled" color="indigo">
                          v{a.duplicate_version}
                        </Badge>
                      )}
                      <Badge size="sm" variant="dot" color={st.color}>{st.label}</Badge>
                      {a.manual && (
                        <Tooltip label={VARIANT_LABEL[a.variant_kind]
                          ? `Composé à la main — ${VARIANT_LABEL[a.variant_kind]}`
                          : 'Composé à la main'}>
                          <Badge size="sm" variant="light" color="grape">sur mesure</Badge>
                        </Tooltip>
                      )}
                      {a.auto && (
                        <Tooltip label={a.problems
                          ? 'Une copie personnalisée par élève, avec problèmes pour les plus forts'
                          : 'Une copie personnalisée par élève'}>
                          <Badge size="sm" variant="light" color="violet">personnalisé</Badge>
                        </Tooltip>
                      )}
                    </Group>
                    <Group gap="xs" wrap="nowrap">
                      {['queued', 'generating', 'error'].includes(a.status) && (
                        <Button size="xs" variant="light" color="gray"
                          leftSection={<ScrollText size={14} />}
                          onClick={() => setLogAssessment(a)}>
                          Voir log
                        </Button>
                      )}
                      {a.status === 'error' && (
                        <Button size="xs" color="red" variant="light"
                          leftSection={<RotateCcw size={14} />} onClick={() => retry(a)}>
                          Réessayer
                        </Button>
                      )}
                      {['ready', 'printed', 'scanning', 'finalized'].includes(a.status) && (
                        <>
                          {a.manual && (
                            <Tooltip label="Recréer le même sujet avec la même variante pour chaque élève">
                              <Button size="xs" variant="light" color="indigo"
                                leftSection={<Copy size={14} />}
                                loading={duplicatingId === a.id}
                                disabled={duplicatingId !== null && duplicatingId !== a.id}
                                onClick={() => duplicate(a)}>
                                Dupliquer
                              </Button>
                            </Tooltip>
                          )}
                          <Button size="xs" variant="light" leftSection={<Eye size={14} />}
                            onClick={() => setPreviewId(a.id)}>
                            Aperçu
                          </Button>
                          {a.status !== 'finalized' && (
                            <PrintButton assessmentId={a.id} file="subject_batch.pdf"
                              label="Imprimer les sujets" assessmentDuplex={a.duplex} />
                          )}
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

      <GenerationLogModal assessmentId={logAssessment?.id ?? null}
        title={logAssessment?.title} onClose={() => setLogAssessment(null)} />

      <SubjectWizard opened={wizardOpen} classes={cycleClasses}
        onClose={() => setWizardOpen(false)} onCreated={refresh} />
    </Stack>
  )
}
