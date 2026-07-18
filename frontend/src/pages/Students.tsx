// Écran Élèves (§9.4) : classes du cycle filtré, assistant de création de
// classe (nom, cycle, année, élèves collés), tableau enrichi qui se compacte
// quand le volet de détail s'ouvre à droite.
import {
  ActionIcon, Badge, Button, Card, Grid, Group, Modal, Progress, ScrollArea,
  Select, Stack, Table, Tabs, Text, Textarea, TextInput, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { AlertTriangle, Lock, Plus, RefreshCw, Users, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { CYCLES, useAppState } from '../state/AppState'
import { masteryColor } from '../utils/mastery'

type Cls = {
  id: string; name: string; grade_level: string; school_year: string | null
  student_count: number
}
type StudentRow = {
  id: string; first_name: string; last_name: string; pseudonym: string
  level: number | null; level_locked: boolean
  avg_mastery: number | null; due_count: number; evidence_count: number
}
type Detail = {
  id: string; first_name: string; last_name: string; pseudonym: string
  class_name: string | null; level: number | null; level_locked: boolean
  evidence_count: number
  competencies: { code: string; short_id: string; label: string; domain: string; chapter: string; mastery: number; confidence: number; recall_probability: number; due_at: string }[]
  due: { code: string; short_id: string; label: string; chapter: string; reason: string; recall_probability: number }[]
}
type Report = { id: string; period: string; content: string; status: string }
type Year = { id: string; label: string; active: boolean }

// ligne compacte : ID court + chapitre + libellé (un libellé isolé comme
// "Automatismes" ne dit rien sans son chapitre) + mini-barre + % rappel
function CompetencyRow({ c }: { c: Detail['competencies'][0] }) {
  const full = c.chapter ? `${c.chapter} · ${c.label}` : c.label
  return (
    <Group gap={8} wrap="nowrap" py={2} title={full}>
      <Text size="xs" style={{ flex: 1, minWidth: 0 }} lineClamp={1}>
        {c.short_id && <Text span c="dimmed" mr={4}>{c.short_id}</Text>}
        {full}
      </Text>
      <Progress value={c.mastery * 100} size={6} w={70} color={masteryColor(c.mastery)}
        style={{ flexShrink: 0 }} />
      <Text size="xs" c="dimmed" w={34} ta="right" style={{ flexShrink: 0 }}>
        {(c.mastery * 100).toFixed(0)} %
      </Text>
      <Tooltip label="Probabilité de rappel (courbe d'oubli)">
        <Badge size="xs" variant="light" w={64} style={{ flexShrink: 0 }}
          color={c.recall_probability < 0.8 ? 'orange' : 'gray'}>
          R {(c.recall_probability * 100).toFixed(0)} %
        </Badge>
      </Tooltip>
    </Group>
  )
}

export default function Students() {
  const [classes, setClasses] = useState<Cls[]>([])
  const [years, setYears] = useState<Year[]>([])
  const [selClass, setSelClass] = useState<Cls | null>(null)
  const [students, setStudents] = useState<StudentRow[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [reports, setReports] = useState<Report[]>([])
  const [batchText, setBatchText] = useState('')
  const [batchOpen, setBatchOpen] = useState(false)
  const { cycle, matches } = useAppState()

  // assistant de création de classe
  const [wizardOpen, setWizardOpen] = useState(false)
  // le cycle proposé suit le filtre global au moment de l'ouverture
  function openWizard() {
    if (cycle !== 'all') setNewGrade(cycle)
    setWizardOpen(true)
  }
  const [newName, setNewName] = useState('')
  const [newGrade, setNewGrade] = useState<string>(cycle !== 'all' ? cycle : '5e')
  const [newYear, setNewYear] = useState<string | null>(null)
  const [newStudents, setNewStudents] = useState('')
  const [creating, setCreating] = useState(false)

  function refreshClasses() { api.get<Cls[]>('/api/classes').then(setClasses) }
  useEffect(() => {
    refreshClasses()
    api.get<Year[]>('/api/years').then((ys) => {
      setYears(ys)
      const active = ys.find((y) => y.active)
      if (active) setNewYear(active.id)
    })
  }, [])

  const cycleClasses = useMemo(
    () => classes.filter((c) => matches(c.grade_level)), [classes, matches])

  // si la classe sélectionnée sort du filtre cycle, on la désélectionne
  useEffect(() => {
    if (selClass && !matches(selClass.grade_level)) {
      setSelClass(null); setStudents([]); setDetail(null)
    }
  }, [matches, selClass])

  async function pickClass(c: Cls) {
    setSelClass(c); setDetail(null)
    setStudents(await api.get<StudentRow[]>(`/api/classes/${c.id}/students`))
  }

  async function pickStudent(id: string) {
    setDetail(await api.get<Detail>(`/api/students/${id}`))
    setReports(await api.get<Report[]>(`/api/students/${id}/reports`))
  }

  const parsedCount = newStudents.split('\n').filter((l) => l.trim()).length

  async function createClass() {
    setCreating(true)
    try {
      const r = await api.post<{ id: string; students_created: number }>('/api/classes', {
        name: newName, grade_level: newGrade, school_year_id: newYear,
        students_text: newStudents,
      })
      notifications.show({
        color: 'green',
        message: `Classe ${newName} créée${r.students_created ? ` avec ${r.students_created} élève(s)` : ''}`,
      })
      setWizardOpen(false); setNewName(''); setNewStudents('')
      refreshClasses()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setCreating(false)
    }
  }

  async function addBatch() {
    if (!selClass) return
    const r = await api.post<{ created: number }>(
      `/api/classes/${selClass.id}/students/batch`, { text: batchText })
    notifications.show({ color: 'green', message: `${r.created} élève(s) ajouté(s)` })
    setBatchText(''); setBatchOpen(false)
    pickClass(selClass); refreshClasses()
  }

  async function recomputeLevel() {
    if (!detail) return
    try {
      const r = await api.post<{ level: number; reason: string }>(
        `/api/students/${detail.id}/level/recompute`)
      notifications.show({ color: 'blue', message: `Niveau ${r.level} — ${r.reason}` })
      pickStudent(detail.id)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function makeReport() {
    if (!detail) return
    await api.post(`/api/students/${detail.id}/reports?period=période courante`)
    setReports(await api.get<Report[]>(`/api/students/${detail.id}/reports`))
  }

  const compact = !!detail // le tableau se simplifie quand le volet est ouvert

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2}>Élèves</Title>
          <Text size="sm" c="dimmed">
            {cycle === 'all' ? 'Tous les cycles' : `Cycle ${cycle}`} — le niveau 1-10
            est privé professeur, jamais imprimé sur les copies.
          </Text>
        </div>
        <Button leftSection={<Plus size={16} />} onClick={openWizard}>
          Nouvelle classe
        </Button>
      </Group>

      <Grid gutter="lg">
        <Grid.Col span={compact ? 2.5 : 3}>
          <Stack gap="xs">
            {cycleClasses.length === 0 && (
              <Card withBorder padding="lg">
                <Stack align="center" gap={6}>
                  <Users size={28} strokeWidth={1.4} opacity={0.5} />
                  <Text size="sm" c="dimmed" ta="center">
                    Aucune classe {cycle !== 'all' && `en ${cycle}`}.
                  </Text>
                </Stack>
              </Card>
            )}
            {cycleClasses.map((c) => (
              <Card key={c.id} withBorder padding="sm"
                style={{
                  cursor: 'pointer',
                  borderColor: selClass?.id === c.id ? 'var(--mantine-primary-color-filled)' : undefined,
                }}
                onClick={() => pickClass(c)}>
                <Group justify="space-between" wrap="nowrap">
                  <Text fw={600} size="sm">{c.name}</Text>
                  <Group gap={4}>
                    <Badge size="xs" variant="light">{c.grade_level}</Badge>
                  </Group>
                </Group>
                <Text size="xs" c="dimmed">
                  {c.student_count} élèves{c.school_year ? ` · ${c.school_year}` : ''}
                </Text>
              </Card>
            ))}
          </Stack>
        </Grid.Col>

        <Grid.Col span={compact ? 4 : 9}>
          {selClass ? (
            <Stack gap="xs">
              <Group justify="space-between">
                <Title order={4}>{selClass.name}</Title>
                <Button size="xs" variant="light" onClick={() => setBatchOpen(true)}>
                  Ajouter des élèves
                </Button>
              </Group>
              <Table highlightOnHover verticalSpacing={6}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Élève</Table.Th>
                    <Table.Th w={80}>Niveau</Table.Th>
                    {!compact && <Table.Th w={160}>Maîtrise moyenne</Table.Th>}
                    {!compact && <Table.Th w={90}>À revoir</Table.Th>}
                    {!compact && <Table.Th w={80}>Preuves</Table.Th>}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {students.map((s) => (
                    <Table.Tr key={s.id} style={{ cursor: 'pointer' }}
                      bg={detail?.id === s.id ? 'var(--mantine-primary-color-light)' : undefined}
                      onClick={() => pickStudent(s.id)}>
                      <Table.Td>
                        <Text size="sm" fw={detail?.id === s.id ? 650 : 450}>
                          {s.last_name} {s.first_name}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {s.level != null ? (
                          <Badge size="sm" variant="light" color="grape"
                            rightSection={s.level_locked ? <Lock size={10} /> : undefined}>
                            {s.level}/10
                          </Badge>
                        ) : <Text size="xs" c="dimmed">—</Text>}
                      </Table.Td>
                      {!compact && (
                        <Table.Td>
                          {s.avg_mastery != null ? (
                            <Group gap={6} wrap="nowrap">
                              <Progress value={s.avg_mastery * 100} size="sm" w={90}
                                color={masteryColor(s.avg_mastery)} />
                              <Text size="xs" c="dimmed">{(s.avg_mastery * 100).toFixed(0)} %</Text>
                            </Group>
                          ) : <Text size="xs" c="dimmed">aucune donnée</Text>}
                        </Table.Td>
                      )}
                      {!compact && (
                        <Table.Td>
                          {s.due_count > 0
                            ? <Badge size="sm" color="orange" variant="light"
                                leftSection={<AlertTriangle size={10} />}>{s.due_count}</Badge>
                            : <Text size="xs" c="dimmed">0</Text>}
                        </Table.Td>
                      )}
                      {!compact && (
                        <Table.Td><Text size="xs" c="dimmed">{s.evidence_count}</Text></Table.Td>
                      )}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              {students.length === 0 && (
                <Text size="sm" c="dimmed" ta="center" py="md">
                  Aucun élève — utilisez « Ajouter des élèves » pour coller la liste.
                </Text>
              )}
            </Stack>
          ) : (
            <Card withBorder padding="xl">
              <Text size="sm" c="dimmed" ta="center">
                Sélectionnez une classe à gauche pour voir ses élèves.
              </Text>
            </Card>
          )}
        </Grid.Col>

        {detail && (
          <Grid.Col span={5.5}>
            <Card withBorder padding="lg">
              <Group justify="space-between" align="flex-start">
                <div>
                  <Title order={4}>{detail.first_name} {detail.last_name}</Title>
                  <Text size="xs" c="dimmed">
                    {detail.class_name} · {detail.evidence_count} preuve(s) de compétence
                  </Text>
                </div>
                <Group gap="xs">
                  {detail.level != null && (
                    <Tooltip label="Niveau pédagogique 1-10 — privé professeur">
                      <Badge color="grape" variant="light"
                        rightSection={detail.level_locked ? <Lock size={10} /> : undefined}>
                        Niveau {detail.level}/10
                      </Badge>
                    </Tooltip>
                  )}
                  <ActionIcon variant="subtle" color="gray" onClick={() => setDetail(null)}
                    aria-label="Fermer le volet">
                    <X size={16} />
                  </ActionIcon>
                </Group>
              </Group>
              <Tabs defaultValue="comp" mt="md">
                <Tabs.List>
                  <Tabs.Tab value="comp">Compétences</Tabs.Tab>
                  <Tabs.Tab value="oubli">
                    Oubli {detail.due.length > 0 && `(${detail.due.length})`}
                  </Tabs.Tab>
                  <Tabs.Tab value="rapports">Comptes rendus</Tabs.Tab>
                </Tabs.List>
                <Tabs.Panel value="comp" pt="sm">
                  <ScrollArea.Autosize mah="calc(100vh - 340px)">
                    <Stack gap="xs">
                      {detail.competencies.length === 0 &&
                        <Text c="dimmed" size="sm">Aucune preuve finalisée pour l'instant.</Text>}
                      {Object.entries(
                        detail.competencies.reduce<Record<string, Detail['competencies']>>((acc, c) => {
                          (acc[c.domain || 'Autres'] ??= []).push(c)
                          return acc
                        }, {}),
                      ).map(([domain, comps]) => (
                        <div key={domain}>
                          <Text size="xs" fw={700} c="dimmed" tt="uppercase" mb={2}>
                            {domain} ({comps.length})
                          </Text>
                          {comps.sort((a, b) => a.chapter.localeCompare(b.chapter) || a.label.localeCompare(b.label))
                            .map((c) => <CompetencyRow key={c.code} c={c} />)}
                        </div>
                      ))}
                    </Stack>
                  </ScrollArea.Autosize>
                  <Button size="xs" variant="light" mt="sm"
                    leftSection={<RefreshCw size={13} />} onClick={recomputeLevel}>
                    Recalculer le niveau
                  </Button>
                </Tabs.Panel>
                <Tabs.Panel value="oubli" pt="sm">
                  {detail.due.length === 0
                    ? <Text c="dimmed" size="sm">Aucune compétence à réviser.</Text>
                    : detail.due.map((d, i) => {
                      const full = d.chapter ? `${d.chapter} · ${d.label}` : d.label
                      return (
                        <Group key={i} justify="space-between" mt="xs" wrap="nowrap">
                          <Text size="sm" lineClamp={1} title={full}>
                            {d.short_id && <Text span c="dimmed" mr={4}>{d.short_id}</Text>}
                            {full}
                          </Text>
                          <Badge color="orange" variant="light">{d.reason}</Badge>
                        </Group>
                      )
                    })}
                </Tabs.Panel>
                <Tabs.Panel value="rapports" pt="sm">
                  <Button size="xs" onClick={makeReport} mb="sm">Générer un compte rendu</Button>
                  {reports.map((r) => (
                    <Card key={r.id} withBorder mt="xs" padding="sm">
                      <Group justify="space-between">
                        <Badge size="xs" variant="light">{r.period}</Badge>
                        <Badge size="xs" variant="light"
                          color={r.status === 'approved' ? 'green' : 'gray'}>
                          {r.status === 'approved' ? 'approuvé' : r.status === 'draft' ? 'brouillon' : r.status}
                        </Badge>
                      </Group>
                      <Text size="sm" mt="xs">{r.content}</Text>
                      {r.status === 'draft' && (
                        <Button size="compact-xs" variant="light" mt="xs"
                          onClick={async () => {
                            await api.patch(`/api/students/reports/${r.id}`, { status: 'approved' })
                            pickStudent(detail.id)
                          }}>
                          Approuver
                        </Button>
                      )}
                    </Card>
                  ))}
                </Tabs.Panel>
              </Tabs>
            </Card>
          </Grid.Col>
        )}
      </Grid>

      {/* assistant de création de classe : nom, cycle, année, élèves */}
      <Modal opened={wizardOpen} onClose={() => setWizardOpen(false)}
        title={<Text fw={650}>Nouvelle classe</Text>} size="md">
        <Stack>
          <TextInput label="Nom de la classe" required placeholder="ex. 5eA"
            value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Select label="Cycle" required value={newGrade}
            description="Le cycle détermine le programme officiel appliqué"
            onChange={(v) => v && setNewGrade(v)}
            data={CYCLES.map((c) => ({ value: c, label: c }))} />
          <Select label="Année scolaire" value={newYear} onChange={setNewYear}
            data={years.map((y) => ({ value: y.id, label: y.label + (y.active ? ' (en cours)' : '') }))} />
          <Textarea label="Élèves (facultatif)" rows={7} value={newStudents}
            description="Un élève par ligne : « Nom Prénom » ou « Nom;Prénom » — vous pourrez en ajouter plus tard"
            placeholder={'Durand Camille\nMartin Jules\n…'}
            onChange={(e) => setNewStudents(e.target.value)} />
          {parsedCount > 0 && (
            <Text size="xs" c="dimmed">{parsedCount} élève(s) détecté(s)</Text>
          )}
          <Button onClick={createClass} loading={creating} disabled={!newName.trim()}>
            Créer la classe{parsedCount > 0 ? ` et ${parsedCount} élève(s)` : ''}
          </Button>
        </Stack>
      </Modal>

      <Modal opened={batchOpen} onClose={() => setBatchOpen(false)}
        title={<Text fw={650}>Ajouter des élèves — {selClass?.name}</Text>}>
        <Stack>
          <Text size="sm" c="dimmed">
            Coller une liste : un élève par ligne, « Nom Prénom » ou « Nom;Prénom ».
          </Text>
          <Textarea rows={8} value={batchText} onChange={(e) => setBatchText(e.target.value)} />
          <Button onClick={addBatch} disabled={!batchText.trim()}>Ajouter</Button>
        </Stack>
      </Modal>
    </Stack>
  )
}
