// Suivi des élèves : groupes de besoin après un devoir, prêt pour l'examen, bilan
// pour la famille. Tout est calculé par le serveur, sans LLM, à la date du jour.
// Jamais de rouge, jamais de comparaison d'un élève aux autres dans ce qui sort.
import {
  Badge, Button, Card, Group, Loader, Modal, Progress, ScrollArea, Select, SimpleGrid, Stack,
  Table, Text, Textarea, TextInput, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, download } from '../api'
import MathText from './MathText'

const showError = (e: unknown) => notifications.show({ color: 'orange', message: (e as Error).message })
const pct = (v: number) => `${Math.round(v * 100)} %`
const LEVELS: Record<number, string> = { 1: 'facile', 2: 'base', 3: 'difficile' }

// ------------------------------------------------------------ groupes de besoin

type NeedExercise = {
  id: string; competency: string; level: number; response_type: string
  statement: string; has_figure: boolean; seen_by: number
}
type NeedGroup = {
  index: number; kind: 'error' | 'competency'; label: string; detail: string
  students: { id: string; name: string }[]; exercises: NeedExercise[]; enough_exercises: boolean
}
type NeedGroups = {
  assessment: { id: string; title: string; class_name: string }
  corrected: number; groups: NeedGroup[]
  board: { code: string; label: string; students: number; occasions: number }[]
  unassigned: { id: string; name: string }[]; without_need: number
}

export function NeedGroupsModal({ assessmentId, onClose }: { assessmentId: string | null; onClose: () => void }) {
  const [data, setData] = useState<NeedGroups | null>(null)
  useEffect(() => {
    setData(null)
    if (assessmentId) api.get<NeedGroups>(`/api/grades/assessments/${assessmentId}/need-groups`)
      .then(setData).catch((e) => { showError(e); onClose() })
  }, [assessmentId])
  return (
    <Modal opened={!!assessmentId} onClose={onClose} size="xl"
      title={<Text fw={650}>Groupes de besoin{data ? ` — ${data.assessment.title}` : ''}</Text>}>
      {!data ? <Group justify="center" py="xl"><Loader size="sm" /></Group> : (
        <Stack gap="md">
          <Card withBorder padding="sm">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" mb={4}>À reprendre au tableau</Text>
            {data.board.length === 0
              ? <Text size="sm" c="dimmed">Aucune erreur type relevée sur ce devoir.</Text>
              : data.board.map((b, i) => (
                <Group key={b.code} justify="space-between" wrap="nowrap" py={2}>
                  <Text size="sm">{i + 1}. {b.label}</Text>
                  <Tooltip label="Élèves qui l'ont faite / élèves qui avaient l'occasion de la faire">
                    <Badge variant="light" color="blue" style={{ flexShrink: 0 }}>
                      {b.students} / {b.occasions} élèves
                    </Badge>
                  </Tooltip>
                </Group>
              ))}
          </Card>
          {data.groups.length === 0 && (
            <Text size="sm" c="dimmed">Aucun besoin commun : les {data.corrected} copies corrigées sont solides.</Text>
          )}
          {data.groups.map((g) => (
            <Card key={g.index} withBorder padding="sm">
              <Group justify="space-between" align="flex-start" wrap="nowrap">
                <div style={{ minWidth: 0 }}>
                  <Group gap={6}>
                    <Badge size="xs" variant="light" color={g.kind === 'error' ? 'grape' : 'teal'}>
                      {g.kind === 'error' ? 'Erreur type' : 'Compétence fragile'}
                    </Badge>
                    <Text size="xs" c="dimmed">{g.students.length} élève(s)</Text>
                  </Group>
                  <Text fw={600} size="sm" mt={4}><MathText text={g.label} /></Text>
                  {g.detail && <Text size="xs" c="dimmed"><MathText text={g.detail} /></Text>}
                </div>
                <Button size="compact-xs" variant="light" leftSection={<Download size={12} />}
                  onClick={() => download(`/api/grades/assessments/${data.assessment.id}/need-groups/${g.index}/sheet.pdf`,
                    `groupe-${g.index}.pdf`).catch(showError)}>
                  Fiche
                </Button>
              </Group>
              <Text size="sm" mt={6}>{g.students.map((s) => s.name).join(', ')}</Text>
              <Text size="xs" fw={700} c="dimmed" tt="uppercase" mt="sm">Exercices proposés</Text>
              {g.exercises.length === 0 && <Text size="xs" c="dimmed">Aucune carte de la banque ne vise encore ce besoin.</Text>}
              {!g.enough_exercises && g.exercises.length > 0 &&
                <Text size="xs" c="dimmed">Moins de trois cartes disponibles dans la banque.</Text>}
              {g.exercises.map((e) => (
                <Group key={e.id} gap={6} wrap="nowrap" py={2}>
                  <Badge size="xs" variant="outline" color="gray" style={{ flexShrink: 0 }}>{LEVELS[e.level] ?? e.level}</Badge>
                  <Text size="xs" lineClamp={1} style={{ flex: 1 }}><MathText text={e.statement} /></Text>
                  {e.seen_by > 0 && <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>déjà vu par {e.seen_by}</Text>}
                </Group>
              ))}
            </Card>
          ))}
          {data.unassigned.length > 0 && (
            <Text size="xs" c="dimmed">
              Hors groupe (besoin propre) : {data.unassigned.map((s) => s.name).join(', ')}
            </Text>
          )}
        </Stack>
      )}
    </Modal>
  )
}

// ------------------------------------------------------------ prêt pour l'examen

type ReviewEntry = { competency_id: string; short_id: string; label: string; chapter: string; reason: string; strength: number }
type StudentReadiness = {
  exam: string | null; total: number; solid_share: number; review_share: number; never_share: number
  solid_count: number; review_count: number; never_count: number; weighted: boolean
  to_review: ReviewEntry[]; never: { competency_id: string; short_id: string; label: string }[]
}
type ClassReadiness = {
  class: { id: string; name: string }; exam: string | null; total: number; weighted: boolean
  solid_share: number; never_share: number
  never_for_class: { competency_id: string; short_id: string; label: string }[]
  students: { id: string; name: string; solid_share: number; review_share: number; never_share: number
    review_count: number; never_count: number; top_review: string[] }[]
}

function ShareBar({ solid, review, never }: { solid: number; review: number; never: number }) {
  return (
    <Tooltip label={`Solide ${pct(solid)} · à revoir ${pct(review)} · jamais évaluée ${pct(never)}`}>
      <Progress.Root size={10}>
        <Progress.Section value={solid * 100} color="teal" />
        <Progress.Section value={review * 100} color="yellow" />
        <Progress.Section value={never * 100} color="gray.3" />
      </Progress.Root>
    </Tooltip>
  )
}

const examTitle = (exam: string | null) => exam ? `Prêt pour le ${exam}` : 'Prêt pour la fin d’année'

export function ClassReadinessModal({ classId, subject, opened, onClose }: {
  classId: string | null; subject: string; opened: boolean; onClose: () => void
}) {
  const [data, setData] = useState<ClassReadiness | null>(null)
  useEffect(() => {
    setData(null)
    if (opened && classId) api.get<ClassReadiness>(`/api/grades/classes/${classId}/exam-readiness?subject_id=${subject}`)
      .then(setData).catch(showError)
  }, [opened, classId, subject])
  return (
    <Modal opened={opened} onClose={onClose} size="xl"
      title={<Text fw={650}>{data ? `${examTitle(data.exam)} — ${data.class.name}` : 'Prêt pour l’examen'}</Text>}>
      {!data ? <Group justify="center" py="xl"><Loader size="sm" /></Group> : (
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            Solide = maîtrise encore fraîche aujourd'hui. Une compétence jamais évaluée n'est pas un échec :
            aucun devoir ne l'a encore portée. {data.weighted ? 'Parts pondérées par les heures des leçons.' : ''}
          </Text>
          <SimpleGrid cols={3}>
            <Card withBorder padding="xs"><Text size="xs" c="dimmed">Compétences du niveau</Text><Text fw={700}>{data.total}</Text></Card>
            <Card withBorder padding="xs"><Text size="xs" c="dimmed">Part solide (moyenne)</Text><Text fw={700}>{pct(data.solid_share)}</Text></Card>
            <Card withBorder padding="xs"><Text size="xs" c="dimmed">Jamais évaluées par la classe</Text><Text fw={700}>{data.never_for_class.length}</Text></Card>
          </SimpleGrid>
          <ScrollArea.Autosize mah="60vh">
            <Table verticalSpacing={5} highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Élève</Table.Th><Table.Th w={180}>Répartition</Table.Th>
                  <Table.Th w={70} ta="right">Solide</Table.Th><Table.Th w={90} ta="right">Jamais év.</Table.Th>
                  <Table.Th>À revoir d'abord</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.students.map((s) => (
                  <Table.Tr key={s.id}>
                    <Table.Td><Text size="sm">{s.name}</Text></Table.Td>
                    <Table.Td><ShareBar solid={s.solid_share} review={s.review_share} never={s.never_share} /></Table.Td>
                    <Table.Td ta="right"><Text size="sm" fw={600}>{pct(s.solid_share)}</Text></Table.Td>
                    <Table.Td ta="right"><Text size="sm" c="dimmed">{pct(s.never_share)}</Text></Table.Td>
                    <Table.Td><Text size="xs" c="dimmed">{s.top_review.join(' · ') || '—'}</Text></Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea.Autosize>
        </Stack>
      )}
    </Modal>
  )
}

export function StudentReadinessPanel({ studentId, subject }: { studentId: string; subject: string }) {
  const [data, setData] = useState<StudentReadiness | null>(null)
  useEffect(() => {
    setData(null)
    api.get<StudentReadiness>(`/api/students/${studentId}/exam-readiness?subject_id=${subject}`).then(setData).catch(showError)
  }, [studentId, subject])
  if (!data) return <Group justify="center" py="md"><Loader size="sm" /></Group>
  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Text fw={600} size="sm">{examTitle(data.exam)}</Text>
        <Button size="compact-xs" variant="light" leftSection={<Download size={12} />}
          onClick={() => download(`/api/students/${studentId}/revision-plan.pdf?subject_id=${subject}`, 'plan-de-revision.pdf').catch(showError)}>
          Mon plan de révision
        </Button>
      </Group>
      <ShareBar solid={data.solid_share} review={data.review_share} never={data.never_share} />
      <Text size="xs" c="dimmed">
        {pct(data.solid_share)} solide aujourd'hui · {data.review_count} à revoir · {data.never_count} jamais évaluée(s)
        {data.weighted ? ' (pondéré par les heures)' : ''}
      </Text>
      <ScrollArea.Autosize mah="calc(100vh - 420px)">
        {data.to_review.map((e, i) => (
          <Group key={e.competency_id} justify="space-between" wrap="nowrap" py={2}>
            <Text size="sm" lineClamp={1}>
              <Text span c="dimmed" mr={4}>{i + 1}. {e.short_id}</Text><MathText text={e.label} />
            </Text>
            <Badge size="xs" variant="light" color={e.reason === 'à rafraîchir' ? 'blue' : 'yellow'} style={{ flexShrink: 0 }}>
              {e.reason}
            </Badge>
          </Group>
        ))}
        {data.to_review.length === 0 && <Text size="sm" c="dimmed">Rien à revoir en priorité.</Text>}
        {data.never.length > 0 && (
          <Text size="xs" c="dimmed" mt="xs">
            Jamais évaluées : {data.never.map((n) => n.short_id || n.label).join(' · ')}
          </Text>
        )}
      </ScrollArea.Autosize>
    </Stack>
  )
}

// ------------------------------------------------------------ bilan pour la famille

type FamilyReport = {
  report_id: string | null; status: 'draft' | 'approved' | 'exported' | null
  period: string; period_label: string
  acquired: { competency_id: string; label: string }[]
  to_work: { competency_id: string; label: string; gesture: string } | null
  curve: { date: string; label: string; value: number }[]; vanquished: number; teacher_note: string
}
const STATUS: Record<string, { label: string; color: string }> = {
  draft: { label: 'brouillon', color: 'gray' }, approved: { label: 'validé', color: 'teal' },
  exported: { label: 'imprimé', color: 'blue' },
}

function periodChoices() {
  const now = new Date()
  const y = now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1
  return [y, y - 1].flatMap((year) => [1, 2, 3].map((t) => ({
    value: `${year}-${year + 1}-T${t}`, label: `${t === 1 ? '1er' : `${t}e`} trimestre ${year}-${year + 1}`,
  })))
}
function currentPeriod() {
  const now = new Date()
  const m = now.getMonth() + 1
  const y = m >= 9 ? now.getFullYear() : now.getFullYear() - 1
  const t = m >= 9 && m <= 11 ? 1 : m === 12 || m <= 2 ? 2 : 3
  return `${y}-${y + 1}-T${t}`
}

function MiniCurve({ points }: { points: FamilyReport['curve'] }) {
  if (points.length < 2) return <Text size="xs" c="dimmed">La courbe se dessinera au fil des devoirs.</Text>
  const w = 240, h = 50
  const xy = points.map((p, i) => [4 + i * (w - 8) / (points.length - 1), h - 4 - p.value * (h - 8)])
  return (
    <svg width={w} height={h} role="img" aria-label="Progression de l'élève par rapport à son passé">
      <polyline points={xy.map(([x, y]) => `${x},${y}`).join(' ')} fill="none"
        stroke="var(--mantine-color-teal-6)" strokeWidth={2} />
      {xy.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={2.5} fill="var(--mantine-color-teal-6)" />)}
    </svg>
  )
}

export function StudentFamilyReport({ studentId, subject }: { studentId: string; subject: string }) {
  const [period, setPeriod] = useState(currentPeriod())
  const [data, setData] = useState<FamilyReport | null>(null)
  const [gesture, setGesture] = useState('')
  const [label, setLabel] = useState('')
  const [note, setNote] = useState('')
  const base = `/api/students/${studentId}/family-report?period=${period}&subject_id=${subject}`
  function show(r: FamilyReport) {
    setData(r); setGesture(r.to_work?.gesture ?? ''); setLabel(r.to_work?.label ?? ''); setNote(r.teacher_note ?? '')
  }
  useEffect(() => { setData(null); api.get<FamilyReport>(base).then(show).catch(showError) }, [studentId, subject, period])
  async function patch(body: object) {
    if (!data?.report_id) return
    try { show(await api.patch<FamilyReport>(`/api/students/family-reports/${data.report_id}`, body)) } catch (e) { showError(e) }
  }
  const draft = data?.status === 'draft'
  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Select size="xs" w={220} data={periodChoices()} value={period} onChange={(v) => v && setPeriod(v)} allowDeselect={false} />
        {data?.status && <Badge variant="light" color={STATUS[data.status].color}>{STATUS[data.status].label}</Badge>}
      </Group>
      {!data ? <Loader size="sm" /> : (
        <>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">Trois acquis</Text>
          {data.acquired.length === 0 && <Text size="sm" c="dimmed">Pas encore d'acquis solide.</Text>}
          {data.acquired.map((a) => <Text key={a.competency_id} size="sm">• <MathText text={a.label} /></Text>)}
          <Text size="xs" fw={700} c="dimmed" tt="uppercase" mt="xs">Une chose à travailler</Text>
          {data.to_work ? (draft ? (
            <>
              <TextInput size="xs" value={label} onChange={(e) => setLabel(e.currentTarget.value)} />
              <Textarea size="xs" autosize minRows={2} label="Geste à la maison" value={gesture}
                onChange={(e) => setGesture(e.currentTarget.value)} />
            </>
          ) : (
            <>
              <Text size="sm"><MathText text={data.to_work.label} /></Text>
              <Text size="sm" c="dimmed">À la maison : {data.to_work.gesture}</Text>
            </>
          )) : <Text size="sm" c="dimmed">Rien de particulier.</Text>}
          <Group gap="md" mt="xs" align="flex-end">
            <MiniCurve points={data.curve} />
            <Text size="sm">{data.vanquished} erreur(s) vaincue(s)</Text>
          </Group>
          {draft && <Textarea size="xs" autosize minRows={1} label="Mot du professeur (facultatif)" value={note}
            onChange={(e) => setNote(e.currentTarget.value)} />}
          <Group gap="xs" mt="xs">
            {(!data.report_id || draft) && (
              <Button size="xs" variant="light" onClick={() => api.post<FamilyReport>(base).then(show).catch(showError)}>
                {data.report_id ? 'Recalculer' : 'Préparer le brouillon'}
              </Button>
            )}
            {draft && <Button size="xs" variant="light"
              onClick={() => patch({ to_work_label: label, gesture, teacher_note: note })}>Enregistrer</Button>}
            {draft && <Button size="xs" color="teal"
              onClick={async () => { await patch({ to_work_label: label, gesture, teacher_note: note }); await patch({ status: 'approved' }) }}>
              Valider</Button>}
            {data.report_id && !draft && <Button size="xs" variant="subtle" onClick={() => patch({ status: 'draft' })}>Repasser en brouillon</Button>}
            {data.report_id && !draft && <Button size="xs" leftSection={<Download size={12} />}
              onClick={() => download(`/api/students/family-reports/${data.report_id}/sheet.pdf`, 'bilan-famille.pdf')
                .then(() => api.get<FamilyReport>(base).then(show)).catch(showError)}>Imprimer</Button>}
          </Group>
        </>
      )}
    </Stack>
  )
}

type ClassFamily = { period: string; period_label: string; students: { id: string; name: string; report_id: string | null; status: string | null }[] }

export function ClassFamilyReportsModal({ classId, subject, opened, onClose }: {
  classId: string | null; subject: string; opened: boolean; onClose: () => void
}) {
  const [period, setPeriod] = useState(currentPeriod())
  const [data, setData] = useState<ClassFamily | null>(null)
  const base = `/api/students/by-class/${classId}/family-reports`
  const q = `?period=${period}&subject_id=${subject}`
  const load = () => api.get<ClassFamily>(base + q).then(setData).catch(showError)
  useEffect(() => { setData(null); if (opened && classId) void load() }, [opened, classId, subject, period])
  const approved = data?.students.filter((s) => s.status === 'approved' || s.status === 'exported').length ?? 0
  return (
    <Modal opened={opened} onClose={onClose} size="lg" title={<Text fw={650}>Bilans pour les familles</Text>}>
      <Stack gap="sm">
        <Text size="xs" c="dimmed">
          Une page par élève : trois acquis, une chose à travailler avec un geste à la maison, sa courbe comparée à son
          propre passé, ses erreurs vaincues. Relisez et validez chaque bilan (volet de l'élève) avant d'imprimer :
          seuls les bilans validés partent à l'impression.
        </Text>
        <Group justify="space-between">
          <Select size="xs" w={220} data={periodChoices()} value={period} onChange={(v) => v && setPeriod(v)} allowDeselect={false} />
          <Group gap="xs">
            <Button size="xs" variant="light" onClick={() => api.post<{ prepared: number }>(base + q)
              .then((r) => { notifications.show({ color: 'teal', message: `${r.prepared} brouillon(s) préparé(s)` }); void load() })
              .catch(showError)}>Préparer les brouillons</Button>
            <Button size="xs" leftSection={<Download size={12} />} disabled={!approved}
              onClick={() => download(`${base}.pdf${q}`, 'bilans-famille.pdf').then(load).catch(showError)}>
              Imprimer les {approved} validé(s)
            </Button>
          </Group>
        </Group>
        {!data ? <Loader size="sm" /> : (
          <Table verticalSpacing={4}>
            <Table.Tbody>
              {data.students.map((s) => (
                <Table.Tr key={s.id}>
                  <Table.Td><Text size="sm">{s.name}</Text></Table.Td>
                  <Table.Td ta="right">
                    {s.status ? <Badge size="sm" variant="light" color={STATUS[s.status]?.color}>{STATUS[s.status]?.label ?? s.status}</Badge>
                      : <Text size="xs" c="dimmed">pas préparé</Text>}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Modal>
  )
}
