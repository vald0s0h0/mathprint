// Écran Correction (§9.5) : lots, barre segmentée par palier, file de validation clavier.
import {
  Alert, Badge, Button, Card, Checkbox, FileButton, Group, Kbd, Modal, NumberInput,
  Select, Stack, Table, Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'

type Segment = { phase: string; state: 'green' | 'orange' | 'gray' }
type Batch = {
  id: string; assessment_id: string; status: string; page_count: number
  assessment_title: string; class_name: string
  overlay_printed: boolean; overlay_distributed: boolean
  error: string | null; pending_reviews: number; segments: Segment[]; created_at: string
}
type Review = {
  review_id: string; category: string; student: string; statement: string
  expected: Record<string, unknown>; correction: string; ocr_text: string
  selected_choices: number[]; ocr_confidence: number | null; reason_code: string
  proposed_score: number; max_score: number
}
type Assessment = { id: string; title: string; status: string }

const SEG_COLORS = { green: '#2f9e44', orange: '#e8590c', gray: '#ced4da' }

function SegmentBar({ segments }: { segments: Segment[] }) {
  return (
    <Group gap={2}>
      {segments.map((s) => (
        <Tooltip key={s.phase} label={s.phase}>
          <div style={{ width: 26, height: 8, borderRadius: 2, background: SEG_COLORS[s.state] }} />
        </Tooltip>
      ))}
    </Group>
  )
}

export default function Corrections() {
  const [batches, setBatches] = useState<Batch[]>([])
  const [assessments, setAssessments] = useState<Assessment[]>([])
  const [selAssessment, setSelAssessment] = useState<string | null>(null)
  const [reviews, setReviews] = useState<Review[]>([])
  const [reviewBatch, setReviewBatch] = useState<Batch | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  const [scoreInput, setScoreInput] = useState<number | ''>('')

  const refresh = useCallback(() => {
    api.get<Batch[]>('/api/scans/batches').then(setBatches)
    api.get<Assessment[]>('/api/assessments').then(setAssessments)
  }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 4000)
    return () => clearInterval(t)
  }, [refresh])

  async function upload(file: File | null) {
    if (!selAssessment) {
      notifications.show({ color: 'orange', message: 'Choisir une évaluation d’abord' })
      return
    }
    const fd = new FormData()
    if (file) fd.append('file', file)
    await api.post(`/api/scans/batches?assessment_id=${selAssessment}`, file ? fd : undefined)
    notifications.show({ color: 'green', message: 'Lot créé, traitement en cours' })
    refresh()
  }

  async function openReviews(b: Batch) {
    const rs = await api.get<Review[]>(`/api/scans/batches/${b.id}/reviews`)
    setReviews(rs); setReviewBatch(b); setIdx(0)
  }

  async function resolve(action: string, score?: number) {
    const r = reviews[idx]
    if (!r) return
    await api.post(`/api/scans/reviews/${r.review_id}/resolve`,
      { action, score: score ?? null })
    const rest = reviews.filter((_, i) => i !== idx)
    setReviews(rest)
    setIdx(Math.min(idx, rest.length - 1))
    setScoreInput('')
    if (!rest.length) { setReviewBatch(null); refresh() }
  }

  // raccourcis clavier : A accepter, 0 zéro point, N note saisie (§6.7)
  useEffect(() => {
    if (!reviewBatch) return
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      if (e.key === 'a' || e.key === 'A') resolve('accept')
      if (e.key === '0') resolve('set_score', 0)
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

  async function finalize(b: Batch) {
    try {
      const r = await api.post<{ evidence_created: number }>(`/api/scans/batches/${b.id}/finalize`)
      notifications.show({ color: 'green', message: `Finalisé : ${r.evidence_created} preuves de compétence` })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function createOverlay(b: Batch) {
    const r = await api.post<{ download: string }>(`/api/scans/batches/${b.id}/overlays`)
    window.open(r.download, '_blank')
    refresh()
  }

  async function setFlag(b: Batch, flag: 'overlay_printed' | 'overlay_distributed', value: boolean) {
    await api.patch(`/api/scans/batches/${b.id}`, { [flag]: value })
    refresh()
  }

  const cur = reviews[idx]

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Corrections</Title>
        <Group>
          <Select placeholder="Évaluation" w={280}
            data={assessments.filter((a) => a.status !== 'draft')
              .map((a) => ({ value: a.id, label: a.title }))}
            value={selAssessment} onChange={setSelAssessment} />
          <FileButton onChange={upload} accept="application/pdf">
            {(props) => <Button {...props}>Déposer un scan (PDF)</Button>}
          </FileButton>
          <Button variant="light" onClick={() => upload(null)}>Simuler un lot (mock)</Button>
        </Group>
      </Group>

      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Classe</Table.Th><Table.Th>Nom du lot</Table.Th><Table.Th>Pages</Table.Th>
            <Table.Th>Progression</Table.Th><Table.Th>Imprimé</Table.Th><Table.Th>Distribué</Table.Th>
            <Table.Th>Statut</Table.Th><Table.Th>Actions</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {batches.map((b) => {
            const overlayReady = b.status === 'overlay_ready'
            const done = overlayReady && b.overlay_printed && b.overlay_distributed
            return (
              <Table.Tr key={b.id} style={done ? { opacity: 0.55 } : undefined}>
                <Table.Td>{b.class_name}</Table.Td>
                <Table.Td>{b.assessment_title}</Table.Td>
                <Table.Td>{b.page_count}</Table.Td>
                <Table.Td><SegmentBar segments={b.segments} /></Table.Td>
                <Table.Td>
                  <Checkbox disabled={!overlayReady} checked={b.overlay_printed}
                    onChange={(e) => setFlag(b, 'overlay_printed', e.target.checked)} />
                </Table.Td>
                <Table.Td>
                  <Checkbox disabled={!overlayReady} checked={b.overlay_distributed}
                    onChange={(e) => setFlag(b, 'overlay_distributed', e.target.checked)} />
                </Table.Td>
                <Table.Td>
                  <Badge color={done ? 'gray' : overlayReady ? 'green' : b.pending_reviews ? 'orange' : 'blue'}>
                    {b.status}{b.pending_reviews ? ` (${b.pending_reviews})` : ''}
                  </Badge>
                  {b.error && <Text size="xs" c="red">{b.error}</Text>}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    {b.pending_reviews > 0 && (
                      <Button size="xs" color="orange" onClick={() => openReviews(b)}>
                        Valider ({b.pending_reviews})
                      </Button>
                    )}
                    {b.status !== 'finalized' && b.status !== 'overlay_ready' && !b.pending_reviews && (
                      <Button size="xs" onClick={() => finalize(b)}>Finaliser</Button>
                    )}
                    {(b.status === 'finalized' || b.status === 'overlay_ready') && (
                      <>
                        <Button size="xs" variant="light" onClick={() => createOverlay(b)}>
                          Créer l'overlay
                        </Button>
                        <Button size="xs" variant="subtle" onClick={() => setPreviewId(b.assessment_id)}>
                          👁 Voir l'aperçu
                        </Button>
                        {overlayReady && (
                          <PrintButton assessmentId={b.assessment_id}
                            file="correction_overlay.pdf" label="Imprimer l'overlay" />
                        )}
                      </>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            )
          })}
        </Table.Tbody>
      </Table>

      <PdfPreviewModal assessmentId={previewId} opened={!!previewId}
        onClose={() => setPreviewId(null)} />

      <Modal opened={!!reviewBatch} onClose={() => { setReviewBatch(null); refresh() }}
        title={`Validation — ${reviews.length} restante(s)`} size="lg">
        {cur ? (
          <Stack>
            <Group justify="space-between">
              <Badge color="orange">{cur.category}</Badge>
              <Text size="sm" c="dimmed">{cur.student}</Text>
            </Group>
            <Card withBorder>
              <Text fw={600}>{cur.statement}</Text>
              <Group mt="sm" grow>
                <div>
                  <Text size="xs" c="dimmed">Lecture OCR</Text>
                  <Text ff="monospace">{cur.ocr_text || (cur.selected_choices.length
                    ? `cases ${cur.selected_choices.join(', ')}` : '∅')}</Text>
                  {cur.ocr_confidence != null &&
                    <Text size="xs" c="dimmed">confiance {(cur.ocr_confidence * 100).toFixed(0)} %</Text>}
                </div>
                <div>
                  <Text size="xs" c="dimmed">Réponse attendue</Text>
                  <Text ff="monospace">{JSON.stringify(cur.expected.value ?? cur.expected.correct)}</Text>
                  <Text size="xs" c="dimmed">{cur.correction}</Text>
                </div>
              </Group>
              <Alert mt="sm" color="yellow">
                Motif : {cur.reason_code} — proposition {cur.proposed_score}/{cur.max_score}
              </Alert>
            </Card>
            <Group>
              <Button color="green" onClick={() => resolve('accept')}>
                Accepter <Kbd ml={6}>A</Kbd>
              </Button>
              <Button color="red" variant="light" onClick={() => resolve('set_score', 0)}>
                0 point <Kbd ml={6}>0</Kbd>
              </Button>
              <NumberInput placeholder="points" w={90} min={0} max={cur.max_score}
                value={scoreInput} onChange={(v) => setScoreInput(v === '' ? '' : Number(v))} />
              <Button variant="light" disabled={scoreInput === ''}
                onClick={() => resolve('set_score', Number(scoreInput))}>
                Attribuer
              </Button>
              <Button variant="subtle" color="gray" onClick={() => resolve('cancel_item')}>
                Annuler la question
              </Button>
            </Group>
          </Stack>
        ) : <Text>Aucune revue restante.</Text>}
      </Modal>
    </Stack>
  )
}
