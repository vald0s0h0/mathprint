import {
  ActionIcon, Box, Card, Checkbox, Grid, Group, Loader, ScrollArea, SegmentedControl, Stack, Table,
  Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Copy } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import CompactClassSelector, {
  sortClassChoices, type ClassChoice,
} from '../components/CompactClassSelector'
import { useAppState } from '../state/AppState'

type GradeValue = {
  note: number | null; note_raw: number | null; note_base: number
  points_earned: number | null; points_total: number | null; absent: boolean
  mastery_delta: number | null; level_delta: -1 | 0 | 1 | null
}
type Gradebook = {
  class: ClassChoice
  students: { id: string; name: string; order_index: number }[]
  assessments: {
    id: string; title: string; type: 'control' | 'training'
    note_base: number; pronote_entered: boolean; created_at: string
  }[]
  values: Record<string, Record<string, GradeValue>>
}

function formatNote(value: number) {
  return Number(value.toFixed(3)).toLocaleString('fr-FR')
}

type GradeView = 'all' | 'control' | 'training' | 'mastery' | 'level'

function masteryStyle(delta: number) {
  if (delta === 0) return { color: 'var(--mantine-color-gray-6)' }
  const strength = Math.min(0.28, 0.07 + Math.abs(delta) / 80)
  return {
    color: delta > 0 ? 'var(--mantine-color-green-8)' : 'var(--mantine-color-red-8)',
    backgroundColor: delta > 0
      ? `rgba(47, 158, 68, ${strength})`
      : `rgba(224, 49, 49, ${strength})`,
  }
}

export default function Grades() {
  const [classes, setClasses] = useState<ClassChoice[]>([])
  const [classId, setClassId] = useState<string | null>(null)
  const [view, setView] = useState<GradeView>('all')
  const [gradebook, setGradebook] = useState<Gradebook | null>(null)
  const [loading, setLoading] = useState(false)
  const { cycle, matches } = useAppState()

  useEffect(() => {
    api.get<ClassChoice[]>('/api/classes').then(setClasses)
  }, [])

  const cycleClasses = useMemo(
    () => sortClassChoices(classes.filter((schoolClass) => matches(schoolClass.grade_level))),
    [classes, matches],
  )

  useEffect(() => {
    if (cycleClasses.length === 0) {
      setClassId(null)
      setGradebook(null)
    } else if (!classId || !cycleClasses.some((schoolClass) => schoolClass.id === classId)) {
      setClassId(cycleClasses[0].id)
    }
  }, [cycleClasses, classId])

  useEffect(() => {
    if (!classId) return
    let active = true
    setLoading(true)
    setGradebook(null)
    const kind = view === 'control' || view === 'training' ? view : 'all'
    api.get<Gradebook>(`/api/grades/classes/${classId}?kind=${kind}`)
      .then((book) => { if (active) setGradebook(book) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [classId, view])

  async function copyAssessment(assessmentId: string) {
    if (!gradebook) return
    const content = gradebook.students.map((student) => {
      const value = gradebook.values[student.id]?.[assessmentId]
      if (value?.absent) return 'Abs'
      return value?.note != null ? formatNote(value.note) : ''
    }).join('\n')
    try {
      await navigator.clipboard.writeText(content)
      notifications.show({ color: 'green', message: 'Notes copiées dans l’ordre des élèves' })
    } catch {
      notifications.show({ color: 'red', message: 'Impossible de copier les notes' })
    }
  }

  async function setPronoteStatus(assessmentId: string, entered: boolean) {
    setGradebook((current) => current ? {
      ...current,
      assessments: current.assessments.map((assessment) => assessment.id === assessmentId
        ? { ...assessment, pronote_entered: entered }
        : assessment),
    } : current)
    try {
      await api.patch(`/api/grades/assessments/${assessmentId}/pronote`, { entered })
    } catch (error) {
      setGradebook((current) => current ? {
        ...current,
        assessments: current.assessments.map((assessment) => assessment.id === assessmentId
          ? { ...assessment, pronote_entered: !entered }
          : assessment),
      } : current)
      notifications.show({ color: 'red', message: (error as Error).message })
    }
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Notes</Title>
          <Text size="sm" c="dimmed">
            {cycle === 'all' ? 'Tous les cycles' : `Cycle ${cycle}`} — contrôles et entraînements corrigés
          </Text>
        </div>
        <SegmentedControl size="sm" value={view}
          onChange={(value) => setView(value as GradeView)}
          data={[
            { value: 'all', label: 'Tout' },
            { value: 'control', label: 'Notes' },
            { value: 'training', label: 'Entraînements' },
            { value: 'mastery', label: 'Maîtrise' },
            { value: 'level', label: 'Niveau' },
          ]} />
      </Group>

      <Grid gutter="md">
        <Grid.Col span={1.5}>
          <CompactClassSelector classes={cycleClasses} value={classId}
            onChange={(schoolClass) => setClassId(schoolClass.id)} />
        </Grid.Col>
        <Grid.Col span={10.5}>
          {loading && !gradebook ? (
            <Group justify="center" py="xl"><Loader size="sm" /></Group>
          ) : gradebook ? (
            <Card withBorder padding={0}>
              <ScrollArea type="auto">
                <Table withColumnBorders highlightOnHover verticalSpacing={7}
                  miw={230 + gradebook.assessments.length * 48}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th w={42} miw={42} ta="center"
                        style={{ position: 'sticky', left: 0, zIndex: 3,
                          background: 'var(--mantine-color-body)' }}>
                        N°
                      </Table.Th>
                      <Table.Th miw={180} w={180}
                        style={{ position: 'sticky', left: 42, zIndex: 3,
                          background: 'var(--mantine-color-body)' }}>
                        Élève
                      </Table.Th>
                      {gradebook.assessments.map((assessment) => {
                        const training = assessment.type === 'training'
                        return (
                          <Table.Th key={assessment.id} w={48} miw={48} h={230} p={0}
                            style={{
                              verticalAlign: 'bottom',
                              background: assessment.pronote_entered
                                ? 'var(--mantine-color-gray-2)'
                                : training ? 'var(--mantine-color-gray-light)' : undefined,
                              color: training ? 'var(--mantine-color-gray-6)' : undefined,
                            }}>
                            <Stack gap={4} align="center" pb={6}>
                              <Tooltip label={`${training ? 'Entraînement' : 'Note'} /${assessment.note_base}`}>
                                <Box style={{
                                  writingMode: 'vertical-rl', transform: 'rotate(180deg)',
                                  maxHeight: 145, overflow: 'hidden', whiteSpace: 'nowrap',
                                }}>
                                  <Text size="xs" fw={650}>{assessment.title}</Text>
                                </Box>
                              </Tooltip>
                              <Text size="xs" fw={700}>/{assessment.note_base}</Text>
                              <Tooltip label="Copier les notes de cette colonne">
                                <ActionIcon size="sm" variant="subtle" color="gray"
                                  aria-label={`Copier les notes de ${assessment.title}`}
                                  onClick={() => copyAssessment(assessment.id)}>
                                  <Copy size={14} />
                                </ActionIcon>
                              </Tooltip>
                              <Tooltip label="Notes saisies dans Pronote">
                                <Checkbox size="xs" checked={assessment.pronote_entered}
                                  aria-label={`Notes de ${assessment.title} saisies dans Pronote`}
                                  onChange={(event) => setPronoteStatus(
                                    assessment.id, event.currentTarget.checked)} />
                              </Tooltip>
                            </Stack>
                          </Table.Th>
                        )
                      })}
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {gradebook.students.map((student, index) => (
                      <Table.Tr key={student.id}>
                        <Table.Td ta="center"
                          style={{ position: 'sticky', left: 0, zIndex: 2,
                            background: 'var(--mantine-color-body)' }}>
                          <Text size="xs" c="dimmed">{index + 1}</Text>
                        </Table.Td>
                        <Table.Td style={{ position: 'sticky', left: 42, zIndex: 2,
                          background: 'var(--mantine-color-body)' }}>
                          <Text size="sm" fw={550} truncate>{student.name}</Text>
                        </Table.Td>
                        {gradebook.assessments.map((assessment) => {
                          const value = gradebook.values[student.id]?.[assessment.id]
                          const training = assessment.type === 'training'
                          const mastery = value?.mastery_delta
                          const level = value?.level_delta
                          return (
                            <Table.Td key={assessment.id} ta="center" px={4}
                              style={{
                                ...(training && view !== 'mastery' && view !== 'level'
                                  ? { background: 'var(--mantine-color-gray-light)',
                                      color: 'var(--mantine-color-gray-6)' }
                                  : {}),
                                ...(view === 'mastery' && mastery != null
                                  ? masteryStyle(mastery) : {}),
                                ...(assessment.pronote_entered
                                  ? { background: 'var(--mantine-color-gray-2)' } : {}),
                              }}>
                              {value?.absent ? (
                                <Text size="xs" fw={650} fs="italic" c="dimmed">Abs</Text>
                              ) : view === 'mastery' ? (
                                mastery == null ? <Text size="xs" c="dimmed">—</Text> : (
                                  <Text size="xs" fw={700}>
                                    {mastery > 0 ? '+' : ''}{formatNote(mastery)} %
                                  </Text>
                                )
                              ) : view === 'level' ? (
                                <Text size="md" fw={800}
                                  c={level === 1 ? 'green.7' : level === -1 ? 'red.7' : undefined}>
                                  {level === 1 ? '+' : level === -1 ? '−' : ''}
                                </Text>
                              ) : value?.note != null ? (
                                <Text size="xs" fw={training ? 500 : 700}>
                                  {formatNote(value.note)}
                                </Text>
                              ) : <Text size="xs" c="dimmed">—</Text>}
                            </Table.Td>
                          )
                        })}
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </ScrollArea>
              {gradebook.assessments.length === 0 && (
                <Text size="sm" c="dimmed" ta="center" py="xl">
                  Aucun {view === 'training' ? 'entraînement' : view === 'control' ? 'contrôle' : 'sujet'} corrigé.
                </Text>
              )}
            </Card>
          ) : (
            <Text size="sm" c="dimmed" ta="center" py="xl">Aucune classe sélectionnée.</Text>
          )}
        </Grid.Col>
      </Grid>
    </Stack>
  )
}
