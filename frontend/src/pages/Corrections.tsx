// Écran Correction (§9.5) : lots de scans en cartes groupées par classe,
// file de validation clavier. Le dépôt d'un scan ne demande plus de choisir
// l'évaluation : le QR signé de chaque page identifie le sujet.
import {
  Alert, Badge, Button, Card, Checkbox, FileButton, Group, Kbd, Modal,
  NumberInput, Select, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Eye, FlaskConical, ScanLine, Upload } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import MathText from '../components/MathText'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import { useAppState } from '../state/AppState'

type Segment = { phase: string; state: 'green' | 'orange' | 'gray' }
type Batch = {
  id: string; assessment_id: string; status: string; page_count: number
  assessment_title: string; assessment_type: string
  class_name: string; class_id: string | null; grade_level: string
  overlay_printed: boolean; overlay_distributed: boolean
  error: string | null; pending_reviews: number; segments: Segment[]; created_at: string
}
type Review = {
  review_id: string; category: string; student: string; statement: string
  expected: Record<string, unknown>; correction: string; ocr_text: string
  selected_choices: number[]; ocr_confidence: number | null; reason_code: string
  proposed_score: number; max_score: number
}
type Assessment = { id: string; title: string; status: string; grade_level: string }

const SEG_COLORS = { green: 'var(--mantine-color-green-6)', orange: 'var(--mantine-color-orange-6)', gray: 'var(--mantine-color-gray-4)' }
const PHASE_LABELS: Record<string, string> = {
  uploaded: 'déposé', split: 'découpé', identified: 'identifié', registered: 'recalé',
  cropped: 'zones extraites', ocr_complete: 'OCR', graded: 'corrigé',
  review_pending: 'validation', finalized: 'finalisé', overlay_ready: 'overlay',
}
const STATUS_LABEL: Record<string, string> = {
  uploaded: 'déposé', graded: 'corrigé', review_pending: 'à valider',
  finalized: 'finalisé', overlay_ready: 'overlay prêt', ocr_complete: 'OCR terminé',
}
const CATEGORY_LABELS: Record<string, string> = {
  rature: 'Rature', double_coche: 'Double coche', ocr_ambigu: 'OCR ambigu',
  scan_faible: 'Scan faible', bareme: 'Barème',
}

function SegmentBar({ segments }: { segments: Segment[] }) {
  return (
    <Group gap={3} wrap="nowrap">
      {segments.map((s) => (
        <Tooltip key={s.phase} label={PHASE_LABELS[s.phase] ?? s.phase}>
          <div style={{ width: 22, height: 7, borderRadius: 3, background: SEG_COLORS[s.state] }} />
        </Tooltip>
      ))}
    </Group>
  )
}

export default function Corrections() {
  const [batches, setBatches] = useState<Batch[]>([])
  const [assessments, setAssessments] = useState<Assessment[]>([])
  const [reviews, setReviews] = useState<Review[]>([])
  const [reviewBatch, setReviewBatch] = useState<Batch | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  const [scoreInput, setScoreInput] = useState<number | ''>('')
  const [uploading, setUploading] = useState(false)
  const [mockOpen, setMockOpen] = useState(false)
  const [mockAssessment, setMockAssessment] = useState<string | null>(null)
  const { cycle, matches, mockMode } = useAppState()

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
    if (!file) return
    setUploading(true)
    try {
      // pas d'évaluation à choisir : le QR de la première page identifiée
      // associe automatiquement le lot au bon sujet
      const fd = new FormData()
      fd.append('file', file)
      await api.post('/api/scans/batches', fd)
      notifications.show({ color: 'green', message: 'Sujet reconnu par QR — traitement en cours' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setUploading(false)
    }
  }

  async function simulateMock() {
    if (!mockAssessment) return
    await api.post(`/api/scans/batches?assessment_id=${mockAssessment}`)
    notifications.show({ color: 'green', message: 'Lot simulé créé' })
    setMockOpen(false); setMockAssessment(null)
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

  // raccourcis clavier : A accepter, 0 zéro point (§6.7)
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

  const groups = useMemo(() => {
    const filtered = batches.filter((b) => matches(b.grade_level))
    const by = new Map<string, { cls: string; grade: string; rows: Batch[] }>()
    for (const b of filtered) {
      const key = b.class_id || b.class_name
      if (!by.has(key)) by.set(key, { cls: b.class_name, grade: b.grade_level, rows: [] })
      by.get(key)!.rows.push(b)
    }
    return [...by.values()].sort((x, y) => x.cls.localeCompare(y.cls))
  }, [batches, matches])

  const cur = reviews[idx]

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2}>Corrections</Title>
          <Text size="sm" c="dimmed">
            Déposez le PDF scanné — le QR de chaque page l'associe au bon sujet.
          </Text>
        </div>
        <Group>
          {mockMode && (
            <Button variant="light" color="grape" leftSection={<FlaskConical size={16} />}
              onClick={() => setMockOpen(true)}>
              Simuler un lot
            </Button>
          )}
          <FileButton onChange={upload} accept="application/pdf">
            {(props) => (
              <Button {...props} leftSection={<Upload size={16} />} loading={uploading}>
                Déposer un scan
              </Button>
            )}
          </FileButton>
        </Group>
      </Group>

      {groups.length === 0 && (
        <Card withBorder padding="xl">
          <Stack align="center" gap="xs">
            <ScanLine size={36} strokeWidth={1.4} opacity={0.5} />
            <Text fw={600}>Aucun lot de scans {cycle !== 'all' && `en ${cycle}`}</Text>
            <Text size="sm" c="dimmed" ta="center">
              Après l'évaluation, scannez les copies en un seul PDF et déposez-le ici.
            </Text>
          </Stack>
        </Card>
      )}

      {groups.map((g) => (
        <div key={g.cls}>
          <Group gap={8} mb="xs">
            <Text fw={700}>{g.cls}</Text>
            <Badge size="sm" variant="light">{g.grade}</Badge>
            <Text size="xs" c="dimmed">{g.rows.length} lot(s)</Text>
          </Group>
          <Stack gap="xs">
            {g.rows.map((b) => {
              const overlayReady = b.status === 'overlay_ready'
              const done = overlayReady && b.overlay_printed && b.overlay_distributed
              return (
                <Card key={b.id} withBorder padding="md" style={done ? { opacity: 0.55 } : undefined}>
                  <Group justify="space-between" wrap="nowrap" align="flex-start">
                    <Stack gap={6} style={{ minWidth: 0, flex: 1 }}>
                      <Group gap="xs" wrap="nowrap">
                        <Badge variant="light" size="sm"
                          color={b.assessment_type === 'control' ? 'red' : 'blue'}>
                          {b.assessment_type === 'control' ? 'Contrôle' : 'Entraînement'}
                        </Badge>
                        <Text fw={600} lineClamp={1}>{b.assessment_title}</Text>
                        <Badge size="sm" variant="dot"
                          color={done ? 'gray' : overlayReady ? 'green' : b.pending_reviews ? 'orange' : 'blue'}>
                          {STATUS_LABEL[b.status] ?? b.status}
                          {b.pending_reviews ? ` (${b.pending_reviews})` : ''}
                        </Badge>
                      </Group>
                      <Group gap="md">
                        <SegmentBar segments={b.segments} />
                        <Text size="xs" c="dimmed">{b.page_count} page(s)</Text>
                        {b.error && <Text size="xs" c="red">{b.error}</Text>}
                      </Group>
                      {(b.status === 'finalized' || overlayReady) && (
                        <Group gap="lg" mt={2}>
                          <Checkbox size="xs" label="Overlay imprimé" disabled={!overlayReady}
                            checked={b.overlay_printed}
                            onChange={(e) => setFlag(b, 'overlay_printed', e.target.checked)} />
                          <Checkbox size="xs" label="Distribué aux élèves" disabled={!overlayReady}
                            checked={b.overlay_distributed}
                            onChange={(e) => setFlag(b, 'overlay_distributed', e.target.checked)} />
                        </Group>
                      )}
                    </Stack>
                    <Group gap="xs" wrap="nowrap">
                      {b.pending_reviews > 0 && (
                        <Button size="xs" color="orange" onClick={() => openReviews(b)}>
                          Valider ({b.pending_reviews})
                        </Button>
                      )}
                      {b.status !== 'finalized' && b.status !== 'overlay_ready' && !b.pending_reviews && (
                        <Button size="xs" onClick={() => finalize(b)}>Finaliser</Button>
                      )}
                      {(b.status === 'finalized' || overlayReady) && (
                        <>
                          <Button size="xs" variant="light" onClick={() => createOverlay(b)}>
                            Créer l'overlay
                          </Button>
                          <Button size="xs" variant="subtle" leftSection={<Eye size={14} />}
                            onClick={() => setPreviewId(b.assessment_id)}>
                            Aperçu
                          </Button>
                          {overlayReady && (
                            <PrintButton assessmentId={b.assessment_id}
                              file="correction_overlay.pdf" label="Imprimer l'overlay" />
                          )}
                        </>
                      )}
                    </Group>
                  </Group>
                </Card>
              )
            })}
          </Stack>
        </div>
      ))}

      <PdfPreviewModal assessmentId={previewId} opened={!!previewId}
        onClose={() => setPreviewId(null)} />

      {/* simulation mock : la seule action qui demande de choisir un sujet */}
      <Modal opened={mockOpen} onClose={() => setMockOpen(false)}
        title={<Text fw={650}>Simuler un lot (démo)</Text>}>
        <Stack>
          <Text size="sm" c="dimmed">
            Génère des réponses d'élèves simulées pour exercer toute la chaîne de
            correction, sans scanner. Choisir le sujet à simuler :
          </Text>
          <Select placeholder="Évaluation générée"
            data={assessments.filter((a) => a.status !== 'draft' && matches(a.grade_level))
              .map((a) => ({ value: a.id, label: a.title }))}
            value={mockAssessment} onChange={setMockAssessment} />
          <Button onClick={simulateMock} disabled={!mockAssessment}>Lancer la simulation</Button>
        </Stack>
      </Modal>

      <Modal opened={!!reviewBatch} onClose={() => { setReviewBatch(null); refresh() }}
        title={<Text fw={650}>Validation — {reviews.length} restante(s)</Text>} size="lg">
        {cur ? (
          <Stack>
            <Group justify="space-between">
              <Badge color="orange" variant="light">
                {CATEGORY_LABELS[cur.category] ?? cur.category}
              </Badge>
              <Text size="sm" c="dimmed">{cur.student}</Text>
            </Group>
            <Card withBorder>
              <MathText text={cur.statement} centered />
              <Group mt="md" grow align="flex-start">
                <div>
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase">Lecture OCR</Text>
                  <Text ff="monospace" mt={2}>{cur.ocr_text || (cur.selected_choices.length
                    ? `cases ${cur.selected_choices.join(', ')}` : '∅')}</Text>
                  {cur.ocr_confidence != null &&
                    <Text size="xs" c="dimmed">confiance {(cur.ocr_confidence * 100).toFixed(0)} %</Text>}
                </div>
                <div>
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase">Réponse attendue</Text>
                  <Text ff="monospace" mt={2}>
                    {JSON.stringify(cur.expected.value ?? cur.expected.correct)}
                  </Text>
                  <Text size="xs" c="dimmed">{cur.correction}</Text>
                </div>
              </Group>
              <Alert mt="sm" color="yellow" p="xs">
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
