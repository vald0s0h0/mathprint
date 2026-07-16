// Banque de contenus : exercices générés et rappels de leçon, par compétence.
// La banque grandit à la demande (cycles réellement enseignés) ; cette page
// donne la couverture, l'aperçu fidèle (KaTeX + figures identiques au PDF),
// le retrait d'un contenu douteux et la génération ciblée.
import {
  ActionIcon, Badge, Box, Button, Card, Collapse, Group, Loader, Paper, ScrollArea,
  SegmentedControl, Select, Stack, Table, Tabs, Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, BookOpen, ChevronDown, ChevronUp, Lightbulb, Library, RefreshCw, Trash2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import FigurePreview from '../components/FigurePreview'
import MathText from '../components/MathText'
import { useAppState } from '../state/AppState'

type Summary = {
  competency_id: string; code: string; short_id: string; label: string; grade_level: string
  domain_name: string; chapter_name: string
  by_level: Record<string, number>; total: number
  lessons: { level_min: number; level_max: number; validated: boolean }[]
}
type Exercise = {
  id: string; competency_id: string; level: number; variant: number
  statement: string; correction: string; response_type: string
  choices: string[]; source: string; kind: string
  quality: Record<string, number>; figure: Record<string, any> | null
  // extraction brute (source="sesamaths" uniquement) dont provient cette
  // ligne, avant adaptation au format de la plateforme
  raw: { number?: string; title?: string | null; text?: string
    table?: Record<string, any>; matching?: Record<string, any> } | null
}
type Lesson = {
  id: string; competency_id: string; level_min: number; level_max: number
  title: string; validated: boolean
  blocks: {
    essentiel?: string; methode?: string[]
    exemple?: { enonce: string; etapes: string[]; resultat: string }
    encarts?: { type: 'conseil' | 'attention'; texte: string }[]
    astuce?: string; figure?: Record<string, any> | null
  } | null
  content: string; example: string; figure: Record<string, any> | null
}
type Framework = { id: string; name: string; grade_level: string }
type Comp = { id: string; code: string; label: string }

const RESPONSE_LABELS: Record<string, string> = {
  short_text: 'réponse courte', multiline_text: 'raisonnement rédigé',
  qcm_single: 'QCM', qcm_multiple: 'QCM multiple',
  table_fill: 'tableau à remplir', matching: 'points à relier',
  manual_drawing: 'tracé / dessin (correction manuelle)',
}

const SOURCE_LABELS: Record<string, string> = {
  mathalea: 'MathALÉA', sesamaths: 'Sésamaths', sesamaths_deepseek: 'Sésamaths (IA)',
}
const SOURCE_COLORS: Record<string, string> = {
  mathalea: 'green', sesamaths: 'teal', sesamaths_deepseek: 'teal',
}

function QualityBadge({ quality }: { quality: Record<string, number> }) {
  const vals = Object.values(quality || {})
  if (!vals.length) return null
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length
  const color = avg >= 4.5 ? 'teal' : avg >= 3.5 ? 'yellow' : 'red'
  return (
    <Tooltip label={Object.entries(quality).map(([k, v]) => `${k} : ${v}/5`).join(' · ')}>
      <Badge size="xs" variant="light" color={color}>qualité {avg.toFixed(1)}</Badge>
    </Tooltip>
  )
}

function ExerciseCard({ ex, onRetire }: { ex: Exercise; onRetire: (id: string) => void }) {
  const [showRaw, setShowRaw] = useState(false)
  return (
    <Card withBorder radius="md" p="sm">
      <Group justify="space-between" wrap="nowrap" align="flex-start" mb={6}>
        <Group gap={6}>
          <Badge size="xs" variant="filled" color="indigo">Niv. {ex.level}</Badge>
          {ex.kind === 'probleme' && <Badge size="xs" variant="light" color="orange">problème</Badge>}
          <Badge size="xs" variant="light" color="gray">{RESPONSE_LABELS[ex.response_type] ?? ex.response_type}</Badge>
          <Badge size="xs" variant="light" color={SOURCE_COLORS[ex.source] ?? 'blue'}>
            {SOURCE_LABELS[ex.source] ?? 'IA vérifiée'}
          </Badge>
          <QualityBadge quality={ex.quality} />
        </Group>
        <Group gap={4} wrap="nowrap">
          {ex.raw && (
            <Tooltip label={showRaw ? 'Masquer le texte original' : 'Voir le texte original extrait du manuel'}>
              <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => setShowRaw((v) => !v)}>
                {showRaw ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </ActionIcon>
            </Tooltip>
          )}
          <Tooltip label="Retirer de la banque (remplacé à la prochaine génération)">
            <ActionIcon variant="subtle" color="red" size="sm" onClick={() => onRetire(ex.id)}>
              <Trash2 size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      {ex.raw && (
        <Collapse in={showRaw}>
          <Paper bg="var(--mantine-color-default-hover)" p={8} radius="sm" mb={8}
            style={{ borderLeft: '3px solid var(--mantine-color-gray-5)' }}>
            <Text size="xs" c="dimmed" fw={600} mb={2}>
              Texte original extrait{ex.raw.number ? ` (exercice ${ex.raw.number})` : ''}
            </Text>
            {ex.raw.text && <MathText text={ex.raw.text} size="sm" />}
            {(ex.raw.table || ex.raw.matching) && (
              <Text size="xs" c="dimmed" mt={4} style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>
                {JSON.stringify(ex.raw.table ?? ex.raw.matching, null, 1)}
              </Text>
            )}
          </Paper>
        </Collapse>
      )}
      <MathText text={ex.statement} />
      {ex.choices.length > 0 && (
        <Group gap="md" mt={6}>
          {ex.choices.map((c, i) => (
            <Group key={i} gap={4} wrap="nowrap">
              <Box w={12} h={12} style={{ border: '1.5px solid var(--mantine-color-gray-5)', borderRadius: 3 }} />
              <MathText text={c} size="sm" />
            </Group>
          ))}
        </Group>
      )}
      {ex.figure && <Box mt={8}><FigurePreview figureJson={ex.figure} /></Box>}
      <Paper bg="var(--mantine-color-default-hover)" p={8} radius="sm" mt={8}>
        <Text size="xs" c="dimmed" fw={600} mb={2}>Correction</Text>
        <MathText text={ex.correction} size="sm" />
      </Paper>
    </Card>
  )
}

// Encarts typés d'un rappel de leçon : icône dans une marge dédiée, teinte
// distincte selon le type — reconnaissables au premier coup d'œil quel que
// soit le thème choisi pour la carte.
const ADMONITION: Record<'rappel' | 'conseil' | 'attention',
  { icon: typeof BookOpen; bg: string; border: string; text: string }> = {
  rappel: { icon: BookOpen, bg: 'var(--mantine-color-yellow-0)',
    border: 'var(--mantine-color-yellow-6)', text: 'var(--mantine-color-yellow-9)' },
  conseil: { icon: Lightbulb, bg: 'var(--mantine-color-teal-0)',
    border: 'var(--mantine-color-teal-6)', text: 'var(--mantine-color-teal-9)' },
  attention: { icon: AlertTriangle, bg: 'var(--mantine-color-orange-0)',
    border: 'var(--mantine-color-orange-7)', text: 'var(--mantine-color-orange-9)' },
}

function Admonition({ type, children }: { type: 'rappel' | 'conseil' | 'attention'; children: React.ReactNode }) {
  const s = ADMONITION[type]
  const Icon = s.icon
  return (
    <Group align="flex-start" gap={8} wrap="nowrap" p={7}
      style={{ background: s.bg, borderLeft: `3px solid ${s.border}`, borderRadius: 5 }}>
      <Icon size={15} color={s.border} style={{ flexShrink: 0, marginTop: 2 }} />
      <Box style={{ flex: 1, minWidth: 0, color: s.text }}>{children}</Box>
    </Group>
  )
}

function LessonCard({ lesson, onRetire }: { lesson: Lesson; onRetire: (id: string) => void }) {
  const b = lesson.blocks
  return (
    <Card withBorder radius="md" p="sm"
      style={{ borderColor: 'var(--mantine-color-yellow-4)' }}>
      <Group justify="space-between" wrap="nowrap" mb={6}>
        <Group gap={6}>
          <BookOpen size={15} />
          <Text fw={600} size="sm">{lesson.title}</Text>
          <Badge size="xs" variant="light" color="gray">niveaux {lesson.level_min}-{lesson.level_max}</Badge>
          {lesson.validated && <Badge size="xs" variant="light" color="teal">vérifié</Badge>}
        </Group>
        <Tooltip label="Retirer (regénéré à la prochaine demande)">
          <ActionIcon variant="subtle" color="red" size="sm" onClick={() => onRetire(lesson.id)}>
            <Trash2 size={14} />
          </ActionIcon>
        </Tooltip>
      </Group>
      {b ? (
        <Stack gap={8}>
          {b.essentiel && <Admonition type="rappel"><MathText text={b.essentiel} /></Admonition>}
          {(b.methode ?? []).length > 0 && (
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={700} tt="uppercase">Méthode</Text>
              {b.methode!.map((m, i) => (
                <Group key={i} gap={6} wrap="nowrap" align="flex-start">
                  <Text size="sm" fw={600} c="dimmed">{i + 1}.</Text>
                  <MathText text={m} size="sm" />
                </Group>
              ))}
            </Stack>
          )}
          {b.exemple?.enonce && (
            <Paper bg="var(--mantine-color-default-hover)" p={8} radius="sm"
              style={{ borderLeft: '3px solid var(--mantine-color-gray-5)' }}>
              <Text size="xs" c="dimmed" fw={700} tt="uppercase" mb={2}>Exemple résolu</Text>
              <MathText text={b.exemple.enonce} size="sm" />
              {b.exemple.etapes.map((e, i) => (
                <Box key={i} ml={10}><MathText text={e} size="sm" /></Box>
              ))}
              <MathText text={b.exemple.resultat} size="sm" />
            </Paper>
          )}
          {(b.encarts?.length ? b.encarts : b.astuce ? [{ type: 'conseil' as const, texte: b.astuce }] : [])
            .map((enc, i) => (
              <Admonition key={i} type={enc.type === 'attention' ? 'attention' : 'conseil'}>
                <MathText text={enc.texte} size="sm" />
              </Admonition>
            ))}
          {(b.figure || lesson.figure) && <FigurePreview figureJson={b.figure ?? lesson.figure} />}
        </Stack>
      ) : (
        <Stack gap={4}>
          <MathText text={lesson.content} size="sm" />
          <MathText text={lesson.example} size="sm" />
        </Stack>
      )}
    </Card>
  )
}

export default function Bank() {
  const { cycle, matches } = useAppState()
  const [summary, setSummary] = useState<Summary[] | null>(null)
  const [selected, setSelected] = useState<Summary | null>(null)
  const [exercises, setExercises] = useState<Exercise[] | null>(null)
  const [lessons, setLessons] = useState<Lesson[] | null>(null)
  const [levelFilter, setLevelFilter] = useState('all')
  const [busy, setBusy] = useState(false)
  // ajout d'une compétence pas encore en banque
  const [allComps, setAllComps] = useState<Comp[]>([])
  const [newComp, setNewComp] = useState<string | null>(null)

  const loadSummary = useCallback(() => {
    const qs = cycle !== 'all' ? `?grade_level=${cycle}` : ''
    api.get<Summary[]>(`/api/content/summary${qs}`).then(setSummary)
  }, [cycle])

  useEffect(() => { loadSummary() }, [loadSummary])

  useEffect(() => {
    api.get<Framework[]>('/api/competencies/frameworks').then(async (fws) => {
      const wanted = fws.filter((f) => cycle === 'all' || f.grade_level === cycle)
      const lists = await Promise.all(
        wanted.map((f) => api.get<Comp[]>(`/api/competencies?framework_id=${f.id}`)))
      setAllComps(lists.flat())
    }).catch(() => setAllComps([]))
  }, [cycle])

  const loadDetail = useCallback((s: Summary) => {
    setSelected(s)
    setExercises(null)
    setLessons(null)
    api.get<Exercise[]>(`/api/content/exercises?competency_id=${s.competency_id}`).then(setExercises)
    api.get<Lesson[]>(`/api/content/lessons?competency_id=${s.competency_id}`).then(setLessons)
  }, [])

  const retireExercise = (id: string) => {
    api.post(`/api/content/exercises/${id}/retire`).then(() => {
      setExercises((xs) => (xs ?? []).filter((x) => x.id !== id))
      loadSummary()
    })
  }
  const retireLesson = (id: string) => {
    api.post(`/api/content/lessons/${id}/retire`).then(() => {
      setLessons((ls) => (ls ?? []).filter((l) => l.id !== id))
      loadSummary()
    })
  }

  const generate = (kind: 'exercises' | 'lessons', level: number) => {
    if (!selected) return
    setBusy(true)
    api.post(`/api/content/${kind}/generate`, { competency_id: selected.competency_id, level })
      .then(() => {
        notifications.show({ message: 'Génération terminée', color: 'teal' })
        loadDetail(selected)
        loadSummary()
      })
      .catch((e) => notifications.show({ message: String(e.message ?? e), color: 'red' }))
      .finally(() => setBusy(false))
  }

  const addCompetency = () => {
    if (!newComp) return
    setBusy(true)
    api.post('/api/content/exercises/generate', { competency_id: newComp, level: 3 })
      .then(() => {
        notifications.show({ message: 'Banque amorcée (niveau 3)', color: 'teal' })
        setNewComp(null)
        loadSummary()
      })
      .catch((e) => notifications.show({ message: String(e.message ?? e), color: 'red' }))
      .finally(() => setBusy(false))
  }

  const rows = useMemo(
    () => (summary ?? []).filter((s) => matches(s.grade_level)),
    [summary, matches])

  const shownExercises = useMemo(
    () => (exercises ?? []).filter((e) => levelFilter === 'all' || e.level === Number(levelFilter)),
    [exercises, levelFilter])

  const inBank = new Set(rows.map((r) => r.competency_id))
  const addable = allComps.filter((c) => !inBank.has(c.id))

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <Box>
          <Title order={2}><Group gap={8}><Library size={22} /> Banque de contenus</Group></Title>
          <Text c="dimmed" size="sm">
            Exercices et rappels générés, vérifiés et réutilisés — la banque grandit avec vos sujets.
          </Text>
        </Box>
        <Group gap="xs">
          <Select size="xs" w={340} searchable clearable placeholder="Amorcer une compétence…"
            data={addable.map((c) => ({ value: c.id, label: `${c.code} — ${c.label}` }))}
            value={newComp} onChange={setNewComp} disabled={busy} />
          <Button size="xs" onClick={addCompetency} disabled={!newComp} loading={busy}>
            Générer la banque
          </Button>
        </Group>
      </Group>

      <Group align="flex-start" gap="md" wrap="nowrap">
        <Paper withBorder radius="md" p="xs" style={{ flex: selected ? '0 0 40%' : 1, minWidth: 0 }}>
          {summary === null ? <Loader size="sm" m="md" /> : rows.length === 0 ? (
            <Text c="dimmed" size="sm" p="md">
              Aucun contenu en banque pour ce cycle. Amorcez une compétence ci-dessus,
              ou créez un sujet : la banque se remplit automatiquement.
            </Text>
          ) : (
            <ScrollArea.Autosize mah="70vh">
              <Table highlightOnHover verticalSpacing={4} fz="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Compétence</Table.Th>
                    {!selected && <Table.Th>Chapitre</Table.Th>}
                    <Table.Th ta="center">Niv. 1-5</Table.Th>
                    <Table.Th ta="center">Rappels</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((s) => (
                    <Table.Tr key={s.competency_id}
                      style={{ cursor: 'pointer' }}
                      bg={selected?.competency_id === s.competency_id ? 'var(--mantine-color-default-hover)' : undefined}
                      onClick={() => loadDetail(s)}>
                      <Table.Td>
                        <Text size="sm" fw={500} lineClamp={1}>{s.short_id || s.code} — {s.label}</Text>
                      </Table.Td>
                      {!selected && <Table.Td><Text size="xs" c="dimmed" lineClamp={1}>{s.chapter_name}</Text></Table.Td>}
                      <Table.Td ta="center">
                        <Group gap={3} justify="center" wrap="nowrap">
                          {[1, 2, 3, 4, 5].map((l) => (
                            <Tooltip key={l} label={`Niveau ${l} : ${s.by_level[String(l)]} exercice(s)`}>
                              <Box w={9} h={9} style={{
                                borderRadius: 2,
                                background: s.by_level[String(l)] > 0
                                  ? 'var(--mantine-color-indigo-5)'
                                  : 'var(--mantine-color-default-border)',
                              }} />
                            </Tooltip>
                          ))}
                        </Group>
                      </Table.Td>
                      <Table.Td ta="center">
                        <Text size="xs">{s.lessons.length}</Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea.Autosize>
          )}
        </Paper>

        {selected && (
          <Paper withBorder radius="md" p="md" style={{ flex: 1, minWidth: 0 }}>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Box>
                <Text fw={600}>{selected.short_id || selected.code} — {selected.label}</Text>
                <Text size="xs" c="dimmed">{selected.grade_level} · {selected.domain_name} · {selected.chapter_name}</Text>
              </Box>
              <Button size="compact-xs" variant="subtle" onClick={() => setSelected(null)}>Fermer</Button>
            </Group>
            <Tabs defaultValue="exercises">
              <Tabs.List mb="xs">
                <Tabs.Tab value="exercises">Exercices ({(exercises ?? []).length})</Tabs.Tab>
                <Tabs.Tab value="lessons">Rappels de leçon ({(lessons ?? []).length})</Tabs.Tab>
              </Tabs.List>

              <Tabs.Panel value="exercises">
                <Group justify="space-between" mb="xs">
                  <SegmentedControl size="xs" value={levelFilter} onChange={setLevelFilter}
                    data={[{ value: 'all', label: 'Tous' },
                      ...[1, 2, 3, 4, 5].map((l) => ({ value: String(l), label: `Niv. ${l}` }))]} />
                  <Button size="compact-xs" variant="light" loading={busy}
                    leftSection={<RefreshCw size={13} />}
                    onClick={() => generate('exercises', levelFilter === 'all' ? 3 : Number(levelFilter))}>
                    Compléter la banque
                  </Button>
                </Group>
                {exercises === null ? <Loader size="sm" /> : (
                  <ScrollArea.Autosize mah="58vh">
                    <Stack gap="xs">
                      {shownExercises.map((ex) => (
                        <ExerciseCard key={ex.id} ex={ex} onRetire={retireExercise} />
                      ))}
                      {shownExercises.length === 0 && (
                        <Text c="dimmed" size="sm">Aucun exercice pour ce filtre — utilisez « Compléter la banque ».</Text>
                      )}
                    </Stack>
                  </ScrollArea.Autosize>
                )}
              </Tabs.Panel>

              <Tabs.Panel value="lessons">
                <Group mb="xs" gap="xs">
                  <Button size="compact-xs" variant="light" loading={busy}
                    onClick={() => generate('lessons', 2)}>Générer (niveaux 1-3)</Button>
                  <Button size="compact-xs" variant="light" loading={busy}
                    onClick={() => generate('lessons', 4)}>Générer (niveaux 4-5)</Button>
                </Group>
                {lessons === null ? <Loader size="sm" /> : (
                  <ScrollArea.Autosize mah="58vh">
                    <Stack gap="xs">
                      {lessons.map((l) => (
                        <LessonCard key={l.id} lesson={l} onRetire={retireLesson} />
                      ))}
                      {lessons.length === 0 && (
                        <Text c="dimmed" size="sm">
                          Aucun rappel — généré automatiquement pour les élèves fragiles, ou à la demande ci-dessus.
                        </Text>
                      )}
                    </Stack>
                  </ScrollArea.Autosize>
                )}
              </Tabs.Panel>
            </Tabs>
          </Paper>
        )}
      </Group>
    </Stack>
  )
}
