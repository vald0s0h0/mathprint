// Écran Élèves (§9.4) : classes, ajout en lot, détail élève (compétences, oubli, niveau, rapports).
import {
  Badge, Button, Card, Grid, Group, Modal, Stack, Table, Tabs, Text,
  Textarea, TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useEffect, useState } from 'react'
import { api } from '../api'

type Cls = { id: string; name: string; grade_level: string; student_count: number; is_mock: boolean }
type StudentRow = { id: string; first_name: string; last_name: string; pseudonym: string }
type Detail = {
  id: string; first_name: string; last_name: string; pseudonym: string
  class_name: string | null; level: number | null; level_locked: boolean
  evidence_count: number
  competencies: { code: string; label: string; domain: string; theme: string; mastery: number; confidence: number; recall_probability: number; due_at: string }[]
  due: { code: string; label: string; reason: string; recall_probability: number }[]
}

function masteryColor(m: number) {
  return m > 0.6 ? '#2f9e44' : m > 0.3 ? '#e8a013' : '#e03131'
}

// ligne compacte : code + libellé tronqué + mini-barre + % rappel, tout sur une ligne
function CompetencyRow({ c }: { c: Detail['competencies'][0] }) {
  return (
    <Group gap={8} wrap="nowrap" py={2} title={c.label}>
      <Text size="xs" ff="monospace" c="dimmed" w={118} style={{ flexShrink: 0 }}>{c.code}</Text>
      <Text size="xs" style={{ flex: 1, minWidth: 0 }} lineClamp={1}>{c.label}</Text>
      <div style={{ width: 70, height: 6, background: '#e9ecef', borderRadius: 3, flexShrink: 0 }}>
        <div style={{ width: `${Math.round(c.mastery * 100)}%`, height: 6,
          background: masteryColor(c.mastery), borderRadius: 3 }} />
      </div>
      <Text size="xs" c="dimmed" w={34} ta="right" style={{ flexShrink: 0 }}>
        {(c.mastery * 100).toFixed(0)} %
      </Text>
      <Badge size="xs" variant="light" w={64} style={{ flexShrink: 0 }}
        color={c.recall_probability < 0.8 ? 'orange' : 'gray'}>
        R {(c.recall_probability * 100).toFixed(0)} %
      </Badge>
    </Group>
  )
}
type Report = { id: string; period: string; content: string; status: string }

export default function Students() {
  const [classes, setClasses] = useState<Cls[]>([])
  const [selClass, setSelClass] = useState<Cls | null>(null)
  const [students, setStudents] = useState<StudentRow[]>([])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [reports, setReports] = useState<Report[]>([])
  const [batchText, setBatchText] = useState('')
  const [newClassName, setNewClassName] = useState('')
  const [batchOpen, setBatchOpen] = useState(false)

  function refreshClasses() { api.get<Cls[]>('/api/classes').then(setClasses) }
  useEffect(refreshClasses, [])

  async function pickClass(c: Cls) {
    setSelClass(c); setDetail(null)
    setStudents(await api.get<StudentRow[]>(`/api/classes/${c.id}/students`))
  }

  async function pickStudent(id: string) {
    setDetail(await api.get<Detail>(`/api/students/${id}`))
    setReports(await api.get<Report[]>(`/api/students/${id}/reports`))
  }

  async function addBatch() {
    if (!selClass) return
    const r = await api.post<{ created: number }>(
      `/api/classes/${selClass.id}/students/batch`, { text: batchText })
    notifications.show({ color: 'green', message: `${r.created} élève(s) ajouté(s)` })
    setBatchText(''); setBatchOpen(false)
    pickClass(selClass); refreshClasses()
  }

  async function createClass() {
    if (!newClassName.trim()) return
    await api.post('/api/classes', { name: newClassName })
    setNewClassName(''); refreshClasses()
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

  return (
    <Grid>
      <Grid.Col span={3}>
        <Stack>
          <Title order={3}>Classes</Title>
          {classes.map((c) => (
            <Card key={c.id} withBorder padding="sm"
              style={{ cursor: 'pointer', borderColor: selClass?.id === c.id ? '#228be6' : undefined }}
              onClick={() => pickClass(c)}>
              <Group justify="space-between">
                <Text fw={600}>{c.name}</Text>
                {c.is_mock && <Badge size="xs" color="gray">mock</Badge>}
              </Group>
              <Text size="sm" c="dimmed">{c.grade_level} — {c.student_count} élèves</Text>
            </Card>
          ))}
          <Group gap="xs">
            <TextInput placeholder="Nouvelle classe" value={newClassName} size="xs"
              onChange={(e) => setNewClassName(e.target.value)} style={{ flex: 1 }} />
            <Button size="xs" onClick={createClass}>+</Button>
          </Group>
        </Stack>
      </Grid.Col>

      <Grid.Col span={detail ? 3 : 9}>
        {selClass && (
          <Stack>
            <Group justify="space-between">
              <Title order={4}>{selClass.name}</Title>
              <Button size="xs" variant="light" onClick={() => setBatchOpen(true)}>
                Ajouter en lot
              </Button>
            </Group>
            <Table highlightOnHover>
              <Table.Tbody>
                {students.map((s) => (
                  <Table.Tr key={s.id} style={{ cursor: 'pointer' }} onClick={() => pickStudent(s.id)}>
                    <Table.Td>{s.last_name} {s.first_name}</Table.Td>
                    <Table.Td><Text size="xs" c="dimmed">{s.pseudonym}</Text></Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Stack>
        )}
      </Grid.Col>

      {detail && (
        <Grid.Col span={6}>
          <Card withBorder>
            <Group justify="space-between">
              <Title order={4}>{detail.first_name} {detail.last_name}</Title>
              <Group gap="xs">
                <Badge>{detail.pseudonym}</Badge>
                {detail.level != null && (
                  <Badge color="grape">
                    Niveau {detail.level}/10 {detail.level_locked && '🔒'}
                  </Badge>
                )}
              </Group>
            </Group>
            <Text size="xs" c="dimmed">
              Le niveau 1-10 est réservé au professeur ; il n'apparaît jamais sur les copies.
            </Text>
            <Tabs defaultValue="comp" mt="md">
              <Tabs.List>
                <Tabs.Tab value="comp">Compétences</Tabs.Tab>
                <Tabs.Tab value="oubli">Oubli</Tabs.Tab>
                <Tabs.Tab value="rapports">Rapports</Tabs.Tab>
              </Tabs.List>
              <Tabs.Panel value="comp" pt="sm">
                <Stack gap="xs">
                  {detail.competencies.length === 0 && <Text c="dimmed">Aucune preuve finalisée.</Text>}
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
                      {comps.sort((a, b) => a.code.localeCompare(b.code))
                        .map((c) => <CompetencyRow key={c.code} c={c} />)}
                    </div>
                  ))}
                  <Button size="xs" variant="light" onClick={recomputeLevel}>
                    Recalculer le niveau
                  </Button>
                </Stack>
              </Tabs.Panel>
              <Tabs.Panel value="oubli" pt="sm">
                {detail.due.length === 0
                  ? <Text c="dimmed">Aucune compétence due.</Text>
                  : detail.due.map((d, i) => (
                    <Group key={i} justify="space-between" mt="xs" wrap="nowrap">
                      <Text size="sm" lineClamp={1} title={d.label}>{d.label}</Text>
                      <Badge color="orange">{d.reason}</Badge>
                    </Group>
                  ))}
              </Tabs.Panel>
              <Tabs.Panel value="rapports" pt="sm">
                <Button size="xs" onClick={makeReport} mb="sm">Générer un compte rendu</Button>
                {reports.map((r) => (
                  <Card key={r.id} withBorder mt="xs" padding="sm">
                    <Group justify="space-between">
                      <Badge size="xs">{r.period}</Badge>
                      <Badge size="xs" color={r.status === 'approved' ? 'green' : 'gray'}>{r.status}</Badge>
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

      <Modal opened={batchOpen} onClose={() => setBatchOpen(false)} title="Ajouter des élèves en lot">
        <Stack>
          <Text size="sm" c="dimmed">
            Coller une liste : un élève par ligne, « Nom Prénom » ou « Nom;Prénom ».
          </Text>
          <Textarea rows={8} value={batchText} onChange={(e) => setBatchText(e.target.value)} />
          <Button onClick={addBatch}>Ajouter</Button>
        </Stack>
      </Modal>
    </Grid>
  )
}
