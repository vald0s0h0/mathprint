// Écran Correction (§9.5) : lots de scans en cartes groupées par classe.
// Chaque carte affiche l'ÉTAPE courante de la pipeline et le BOUTON D'ACTION
// logique qui indique clairement au professeur la prochaine chose à faire
// (déposer / corriger / valider / imprimer), plus un bouton de déblocage
// (relancer) quand la correction est bloquée. Le dépôt d'un scan ne demande pas
// de choisir l'évaluation : le QR signé de chaque page identifie le sujet.
import {
  ActionIcon, Badge, Button, Card, Checkbox, Divider, FileButton, Group, Kbd,
  Modal, NumberInput, SegmentedControl, Stack, Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  Check, ChevronLeft, ChevronRight, Eye, Inbox, RefreshCw, ScanLine, Upload,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, getToken } from '../api'
import MathText from '../components/MathText'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import { useAppState } from '../state/AppState'

type Segment = { phase: string; state: 'green' | 'orange' | 'gray' }
type Batch = {
  id: string; assessment_id: string; status: string; page_count: number
  assessment_title: string; assessment_type: string
  // base de notation d'un contrôle (§ barème) : null pour un entraînement
  note_base: number | null
  class_name: string; class_id: string | null; grade_level: string
  overlay_printed: boolean; overlay_distributed: boolean
  error: string | null; pending_reviews: number; segments: Segment[]; created_at: string
}
// une réponse d'élève à (re)corriger : signalée par le moteur (flagged) ou
// simplement relue par le professeur. Clé de résolution = response_id.
type Item = {
  response_id: string; review_id: string | null; flagged: boolean
  category: string | null; student: string; statement: string
  expected: Record<string, unknown>; correction: string; ocr_text: string
  selected_choices: number[]; ocr_confidence: number | null; reason_code: string
  decision_source: string; proposed_score: number; max_score: number
  current_points: number; full_credit: boolean; cancelled: boolean
  bareme_points: number; zone_id: string | null; has_scan: boolean
  group_key: string; group_label: string; response_type: string; sequence: number
}
// raccourcis de correction manuelle (paramétrables, cf. Réglages → Pédagogie)
type Shortcuts = { full: string; two_thirds: string; one_third: string; zero: string }
const DEFAULT_SHORTCUTS: Shortcuts = { full: 'f', two_thirds: 'd', one_third: 's', zero: 'q' }
type SandboxResult = {
  filename: string; status: string; pages_added: number
  duplicates_rejected: number; blocked_pages: number; batches_created: string[]
}
type Scope = 'flagged' | 'all'

const SEG_COLORS = { green: 'var(--mantine-color-green-6)', orange: 'var(--mantine-color-orange-6)', gray: 'var(--mantine-color-gray-4)' }
const PHASE_LABELS: Record<string, string> = {
  uploaded: 'déposé', split: 'découpé', identified: 'identifié', registered: 'recalé',
  cropped: 'zones extraites', ocr_complete: 'OCR', graded: 'corrigé',
  review_pending: 'validation', finalized: 'finalisé', overlay_ready: 'overlay',
}
const CATEGORY_LABELS: Record<string, string> = {
  rature: 'Rature', double_coche: 'Double coche', ocr_ambigu: 'OCR ambigu',
  scan_faible: 'Scan faible', bareme: 'Barème',
  trace_dessin: 'Tracé / dessin', points_a_relier: 'Points à relier',
}

// Étape « métier » d'un lot, dérivée de son statut technique — c'est elle qui
// pilote le libellé de la carte et l'action proposée au professeur.
type Stage = 'awaiting' | 'processing' | 'error' | 'review' | 'validate' | 'done'
function stageOf(b: Batch): Stage {
  if (b.status === 'awaiting_scan') return 'awaiting'
  if (b.error) return 'error'
  if (b.status === 'finalized' || b.status === 'overlay_ready') return 'done'
  if (b.status === 'graded' || b.status === 'review_pending')
    return b.pending_reviews > 0 ? 'review' : 'validate'
  return 'processing'  // uploaded → split → … → ocr_complete
}
const STAGE_BADGE: Record<Stage, { label: string; color: string }> = {
  awaiting: { label: 'en attente de scan', color: 'gray' },
  processing: { label: 'correction en cours', color: 'blue' },
  error: { label: 'bloqué', color: 'red' },
  review: { label: 'à corriger', color: 'orange' },
  validate: { label: 'corrigé — à valider', color: 'teal' },
  done: { label: 'prêt à imprimer', color: 'green' },
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

// points à la française pour l'affichage (1,5 — et 2 plutôt que 2,0)
const fmtPts = (v: number) => (Math.round(v * 100) / 100).toString().replace('.', ',')

// image du crop scanné de la zone de réponse : chargée via fetch + token puis
// blob (une balise <img> n'envoie pas nos en-têtes d'auth), comme PdfFrame.
function ScanImage({ responseId }: { responseId: string }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let revoke: string | null = null
    setUrl(null); setFailed(false)
    fetch(`/api/scans/responses/${responseId}/scan`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`${r.status}`))))
      .then((b) => { revoke = URL.createObjectURL(b); setUrl(revoke) })
      .catch(() => setFailed(true))
    return () => { if (revoke) URL.revokeObjectURL(revoke) }
  }, [responseId])
  if (failed) return (
    <Text size="xs" c="dimmed" p="sm">
      Zone non scannée (vide, ou lot sans scan) — rien à visualiser ici.
    </Text>
  )
  if (!url) return <Text size="xs" c="dimmed" p="sm">Chargement du scan…</Text>
  return (
    <img src={url} alt="Scan de la réponse de l'élève"
      style={{ maxWidth: '100%', maxHeight: 260, objectFit: 'contain',
        border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4, background: '#fff' }} />
  )
}

// pastille d'état de la note courante d'une réponse dans la file de correction
function ItemStatus({ it }: { it: Item }) {
  if (it.cancelled) return <Badge size="sm" variant="light" color="gray">question annulée</Badge>
  if (it.decision_source === 'teacher')
    return <Badge size="sm" variant="light" color="indigo">corrigé — {fmtPts(it.current_points)}/{fmtPts(it.bareme_points)}</Badge>
  if (it.flagged)
    return <Badge size="sm" variant="light" color="orange">à vérifier{it.category ? ` — ${CATEGORY_LABELS[it.category] ?? it.category}` : ''}</Badge>
  if (it.full_credit) return <Badge size="sm" variant="light" color="green">auto ✓ {fmtPts(it.bareme_points)}/{fmtPts(it.bareme_points)}</Badge>
  return <Badge size="sm" variant="light" color="yellow">auto — {fmtPts(it.current_points)}/{fmtPts(it.bareme_points)}</Badge>
}

export default function Corrections() {
  const [batches, setBatches] = useState<Batch[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [reviewBatch, setReviewBatch] = useState<Batch | null>(null)
  const [scope, setScope] = useState<Scope>('flagged')
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  const [scoreInput, setScoreInput] = useState<number | ''>('')
  const [uploading, setUploading] = useState(false)
  const [sandboxUploading, setSandboxUploading] = useState(false)
  const [sandboxResults, setSandboxResults] = useState<SandboxResult[]>([])
  const [shortcuts, setShortcuts] = useState<Shortcuts>(DEFAULT_SHORTCUTS)
  const { cycle, matches } = useAppState()

  // raccourcis de correction paramétrés (Réglages → Pédagogie), repli défauts
  useEffect(() => {
    api.get<Record<string, Partial<Shortcuts>>>('/api/settings/system')
      .then((s) => setShortcuts({ ...DEFAULT_SHORTCUTS, ...(s.correction_shortcuts ?? {}) }))
      .catch(() => {})
  }, [])

  const refresh = useCallback(() => {
    api.get<Batch[]>('/api/scans/batches').then(setBatches)
  }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 4000)
    return () => clearInterval(t)
  }, [refresh])

  async function upload(file: File | null, assessmentId?: string) {
    if (!file) return
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const qs = assessmentId ? `?assessment_id=${assessmentId}` : ''
      await api.post(`/api/scans/batches${qs}`, fd)
      notifications.show({ color: 'green', message: 'Scan déposé — correction en cours' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setUploading(false)
    }
  }

  async function uploadSandbox(files: File[]) {
    if (!files.length) return
    setSandboxUploading(true)
    try {
      const fd = new FormData()
      for (const f of files) fd.append('files', f)
      const r = await api.post<{ results: SandboxResult[] }>('/api/scans/sandbox', fd)
      setSandboxResults(r.results)
      const pages = r.results.reduce((n, x) => n + x.pages_added, 0)
      const dups = r.results.reduce((n, x) => n + x.duplicates_rejected +
        (x.status === 'duplicate_file' ? 1 : 0), 0)
      notifications.show({
        color: 'green',
        message: `${pages} page(s) identifiée(s)${dups ? `, ${dups} doublon(s) ignoré(s)` : ''}`,
      })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSandboxUploading(false)
    }
  }

  const loadItems = useCallback(async (b: Batch, s: Scope) => {
    const rs = await api.get<Item[]>(`/api/scans/batches/${b.id}/items?scope=${s}`)
    setItems(rs); setIdx(0); setScoreInput('')
  }, [])

  async function openCorrection(b: Batch, s: Scope) {
    setReviewBatch(b); setScope(s)
    try {
      await loadItems(b, s)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      setReviewBatch(null)
    }
  }

  async function changeScope(s: Scope) {
    if (!reviewBatch) return
    setScope(s)
    await loadItems(reviewBatch, s)
  }

  function closeCorrection() { setReviewBatch(null); setItems([]); refresh() }

  // enregistre la note (append-only côté serveur) et met à jour l'affichage de
  // la réponse courante en place, puis passe à la suivante
  async function grade(action: string, extra?: { ratio?: number }) {
    const it = items[idx]
    if (!it) return
    try {
      await api.post(`/api/scans/responses/${it.response_id}/resolve`, { action, ...extra })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      return
    }
    const r = Math.max(0, Math.min(1, extra?.ratio ?? 0))
    setItems((prev) => prev.map((x, i) => i !== idx ? x : (
      action === 'cancel_item'
        ? { ...x, decision_source: 'teacher', cancelled: true, full_credit: false, current_points: 0 }
        : { ...x, decision_source: 'teacher', cancelled: false, full_credit: r >= 0.999,
            current_points: Math.round(r * x.bareme_points * 100) / 100 }
    )))
    setScoreInput('')
    setIdx((i) => Math.min(i + 1, items.length - 1))
  }
  const gradeRatio = (ratio: number) => grade('set_ratio', { ratio })

  // raccourcis clavier de correction manuelle (paramétrés dans les réglages)
  useEffect(() => {
    if (!reviewBatch || !items[idx]) return
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      const k = e.key.toLowerCase()
      if (k === shortcuts.full) gradeRatio(1)
      else if (k === shortcuts.two_thirds) gradeRatio(2 / 3)
      else if (k === shortcuts.one_third) gradeRatio(1 / 3)
      else if (k === shortcuts.zero) gradeRatio(0)
      else if (e.key === 'ArrowRight') setIdx((i) => Math.min(i + 1, items.length - 1))
      else if (e.key === 'ArrowLeft') setIdx((i) => Math.max(i - 1, 0))
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

  async function retry(b: Batch) {
    try {
      await api.post(`/api/scans/batches/${b.id}/retry`)
      notifications.show({ color: 'blue', message: 'Correction relancée' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function finalize(b: Batch) {
    try {
      const r = await api.post<{ evidence_created: number }>(`/api/scans/batches/${b.id}/finalize`)
      notifications.show({ color: 'green', message: `Correction validée — ${r.evidence_created} preuve(s) de compétence, copies corrigées prêtes` })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function createOverlay(b: Batch) {
    try {
      await api.post<{ download: string }>(`/api/scans/batches/${b.id}/overlays`)
      notifications.show({ color: 'green', message: 'Overlay de correction régénéré' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
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

  const cur = items[idx]
  // réponses encore à vérifier (signalées non résolues) dans la file chargée
  const remainingFlagged = items.filter((x) => x.flagged && x.decision_source !== 'teacher').length
  const flaggedTotal = items.filter((x) => x.flagged).length

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2}>Corrections</Title>
          <Text size="sm" c="dimmed">
            Déposez le PDF scanné — le QR de chaque page l'associe au bon sujet, puis
            corrigez et validez pour imprimer les copies corrigées.
          </Text>
        </div>
      </Group>

      <Card withBorder padding="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Group gap="xs" wrap="nowrap" align="flex-start">
            <Inbox size={20} strokeWidth={1.6} style={{ marginTop: 2 }} />
            <div>
              <Text fw={600} size="sm">Bac à sable</Text>
              <Text size="xs" c="dimmed">
                Déposez en une fois tous les PDFs et photos (JPEG, PNG, HEIC) même
                mélangés entre sujets et classes — chaque page est identifiée
                individuellement, les doublons sont ignorés automatiquement.
              </Text>
            </div>
          </Group>
          <FileButton onChange={uploadSandbox} multiple
            accept="application/pdf,image/jpeg,image/png,image/heic,image/heif">
            {(props) => (
              <Button {...props} size="xs" variant="light" leftSection={<Upload size={14} />}
                loading={sandboxUploading}>
                Déposer en vrac
              </Button>
            )}
          </FileButton>
        </Group>
        {sandboxResults.length > 0 && (
          <Stack gap={4} mt="sm">
            {sandboxResults.map((r, i) => (
              <Group key={i} gap="xs" wrap="nowrap">
                <Badge size="xs" variant="light"
                  color={r.status === 'processed' ? 'green'
                    : r.status === 'unrecognized' || r.status === 'error' ? 'red' : 'gray'}>
                  {r.status === 'processed' ? `${r.pages_added} page(s)`
                    : r.status === 'duplicate_file' ? 'doublon' : r.status}
                </Badge>
                <Text size="xs" c="dimmed" lineClamp={1}>{r.filename}</Text>
                {r.duplicates_rejected > 0 && (
                  <Text size="xs" c="dimmed">— {r.duplicates_rejected} page(s) déjà scannée(s) ignorée(s)</Text>
                )}
                {r.blocked_pages > 0 && (
                  <Text size="xs" c="orange">— {r.blocked_pages} page(s) non identifiée(s)</Text>
                )}
              </Group>
            ))}
          </Stack>
        )}
      </Card>

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
              const stage = stageOf(b)
              const overlayReady = b.status === 'overlay_ready'
              const done = stage === 'done' && b.overlay_printed && b.overlay_distributed
              const badge = done ? { label: 'terminé', color: 'gray' } : STAGE_BADGE[stage]
              return (
                <Card key={b.id} withBorder padding="md" style={done ? { opacity: 0.55 } : undefined}>
                  <Group justify="space-between" wrap="nowrap" align="flex-start">
                    <Stack gap={6} style={{ minWidth: 0, flex: 1 }}>
                      <Group gap="xs" wrap="nowrap">
                        <Badge variant="light" size="sm"
                          color={b.assessment_type === 'control' ? 'red' : 'blue'}>
                          {b.assessment_type === 'control' ? 'Contrôle' : 'Entraînement'}
                        </Badge>
                        {b.note_base && (
                          <Tooltip label={`Noté sur ${b.note_base} points`}>
                            <Badge size="sm" variant="outline" color="red">/{b.note_base}</Badge>
                          </Tooltip>
                        )}
                        <Text fw={600} lineClamp={1}>{b.assessment_title}</Text>
                        <Badge size="sm" variant="dot" color={badge.color}>
                          {badge.label}{stage === 'review' && b.pending_reviews ? ` (${b.pending_reviews})` : ''}
                        </Badge>
                      </Group>
                      {stage !== 'awaiting' && (
                        <Group gap="md">
                          <SegmentBar segments={b.segments} />
                          <Text size="xs" c="dimmed">{b.page_count} page(s)</Text>
                        </Group>
                      )}
                      {b.error && (
                        <Text size="xs" c="red">
                          Correction bloquée : {b.error} — relancez, ou re-déposez le scan.
                        </Text>
                      )}
                      {stage === 'done' && (
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

                    {/* Action(s) logique(s) selon l'étape : un bouton principal qui
                        indique la prochaine chose à faire, + déblocage si besoin. */}
                    <Group gap="xs" wrap="nowrap">
                      {stage === 'awaiting' && (
                        <FileButton onChange={(f) => upload(f, b.assessment_id)}
                          accept="application/pdf,image/jpeg,image/png,image/heic,image/heif">
                          {(props) => (
                            <Button {...props} size="xs" leftSection={<Upload size={14} />} loading={uploading}>
                              Déposer le scan
                            </Button>
                          )}
                        </FileButton>
                      )}

                      {stage === 'processing' && (
                        <>
                          <Button size="xs" variant="light" loading disabled>Correction en cours…</Button>
                          <Tooltip label="Si la correction semble bloquée, relancez-la">
                            <ActionIcon variant="subtle" color="gray" size="lg" onClick={() => retry(b)}>
                              <RefreshCw size={16} />
                            </ActionIcon>
                          </Tooltip>
                        </>
                      )}

                      {stage === 'error' && (
                        <>
                          <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />}
                            onClick={() => retry(b)}>
                            Relancer la correction
                          </Button>
                          {b.pending_reviews > 0 && (
                            <Button size="xs" variant="light" color="orange"
                              onClick={() => openCorrection(b, 'flagged')}>
                              Corriger ({b.pending_reviews})
                            </Button>
                          )}
                          <FileButton onChange={(f) => upload(f, b.assessment_id)}
                            accept="application/pdf,image/jpeg,image/png,image/heic,image/heif">
                            {(props) => (
                              <Button {...props} size="xs" variant="subtle" leftSection={<Upload size={14} />}
                                loading={uploading}>
                                Re-déposer le scan
                              </Button>
                            )}
                          </FileButton>
                        </>
                      )}

                      {stage === 'review' && (
                        <Button size="xs" color="orange" onClick={() => openCorrection(b, 'flagged')}>
                          Corriger ({b.pending_reviews})
                        </Button>
                      )}

                      {stage === 'validate' && (
                        <>
                          <Tooltip multiline w={240}
                            label="Verrouille les notes, calcule la note de chaque élève et prépare les copies corrigées à imprimer.">
                            <Button size="xs" leftSection={<Check size={14} />} onClick={() => finalize(b)}>
                              Valider la correction
                            </Button>
                          </Tooltip>
                          <Button size="xs" variant="light" onClick={() => openCorrection(b, 'all')}>
                            Revoir la correction
                          </Button>
                        </>
                      )}

                      {stage === 'done' && (
                        <>
                          {overlayReady ? (
                            <PrintButton assessmentId={b.assessment_id}
                              file="correction_overlay.pdf" label="Imprimer les copies corrigées" />
                          ) : (
                            <Button size="xs" variant="light" onClick={() => createOverlay(b)}>
                              Générer l'overlay
                            </Button>
                          )}
                          <Button size="xs" variant="subtle" leftSection={<Eye size={14} />}
                            onClick={() => setPreviewId(b.assessment_id)}>
                            Aperçu
                          </Button>
                          <Button size="xs" variant="subtle" onClick={() => openCorrection(b, 'all')}>
                            Revoir
                          </Button>
                          <Tooltip label="Recalculer les notes et régénérer les copies corrigées après un ajustement">
                            <ActionIcon variant="subtle" color="gray" size="lg" onClick={() => finalize(b)}>
                              <RefreshCw size={16} />
                            </ActionIcon>
                          </Tooltip>
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

      <PdfPreviewModal assessmentId={previewId} opened={!!previewId} initialMode="review"
        onClose={() => setPreviewId(null)} />

      <Modal opened={!!reviewBatch} onClose={closeCorrection} size="xl"
        title={<Text fw={650}>Correction — {reviewBatch?.assessment_title}</Text>}>
        <Stack>
          <Group justify="space-between" wrap="wrap">
            <SegmentedControl size="xs" value={scope}
              onChange={(v) => changeScope(v as Scope)}
              data={[
                { label: `À vérifier${flaggedTotal ? ` (${remainingFlagged})` : ''}`, value: 'flagged' },
                { label: 'Toutes les réponses', value: 'all' },
              ]} />
            {items.length > 0 && (
              <Group gap={6} wrap="nowrap">
                <ActionIcon variant="light" disabled={idx <= 0} onClick={() => setIdx((i) => i - 1)}>
                  <ChevronLeft size={16} />
                </ActionIcon>
                <Text size="xs" c="dimmed">{idx + 1} / {items.length}</Text>
                <ActionIcon variant="light" disabled={idx >= items.length - 1}
                  onClick={() => setIdx((i) => i + 1)}>
                  <ChevronRight size={16} />
                </ActionIcon>
              </Group>
            )}
          </Group>

          {cur ? (
            <>
              <Group justify="space-between" wrap="nowrap">
                <Group gap="xs" wrap="nowrap">
                  <Badge variant="filled" color="indigo">{cur.group_label}</Badge>
                  <ItemStatus it={cur} />
                </Group>
                <Text size="sm" fw={600}>{cur.student}</Text>
              </Group>
              <Card withBorder padding="sm">
                <MathText text={cur.statement} centered />
              </Card>
              {/* scan de l'élève + réponse attendue, côte à côte pour corriger vite */}
              <Group grow align="stretch">
                <Card withBorder padding="xs">
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>Scan de l'élève</Text>
                  <ScanImage responseId={cur.response_id} />
                </Card>
                <Card withBorder padding="xs">
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>Réponse attendue</Text>
                  <Text ff="monospace">
                    {JSON.stringify(cur.expected.value ?? cur.expected.correct ?? cur.expected)}
                  </Text>
                  {cur.correction && <Text size="sm" mt={4}>{cur.correction}</Text>}
                  <Divider my="xs" />
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={2}>Lecture OCR / CV</Text>
                  <Text ff="monospace">{cur.ocr_text || (cur.selected_choices.length
                    ? `cases ${cur.selected_choices.join(', ')}` : '∅')}</Text>
                  {cur.ocr_confidence != null &&
                    <Text size="xs" c="dimmed">confiance {(cur.ocr_confidence * 100).toFixed(0)} %</Text>}
                  {cur.reason_code && <Text size="xs" c="dimmed" mt={4}>Motif : {cur.reason_code}</Text>}
                </Card>
              </Group>
              <Group>
                <Button color="green" onClick={() => gradeRatio(1)}>
                  Tous les points — {fmtPts(cur.bareme_points)} <Kbd ml={6}>{shortcuts.full.toUpperCase()}</Kbd>
                </Button>
                <Button color="teal" variant="light" onClick={() => gradeRatio(2 / 3)}>
                  2⁄3 — {fmtPts(cur.bareme_points * 2 / 3)} <Kbd ml={6}>{shortcuts.two_thirds.toUpperCase()}</Kbd>
                </Button>
                <Button color="orange" variant="light" onClick={() => gradeRatio(1 / 3)}>
                  1⁄3 — {fmtPts(cur.bareme_points / 3)} <Kbd ml={6}>{shortcuts.one_third.toUpperCase()}</Kbd>
                </Button>
                <Button color="red" variant="light" onClick={() => gradeRatio(0)}>
                  0 point <Kbd ml={6}>{shortcuts.zero.toUpperCase()}</Kbd>
                </Button>
              </Group>
              <Group>
                <NumberInput placeholder="points" w={120} min={0} max={cur.bareme_points} step={0.5}
                  decimalScale={2} value={scoreInput}
                  onChange={(v) => setScoreInput(v === '' ? '' : Number(v))} />
                <Button variant="light" disabled={scoreInput === '' || !cur.bareme_points}
                  onClick={() => gradeRatio(Number(scoreInput) / cur.bareme_points)}>
                  Attribuer ces points
                </Button>
                <Button variant="subtle" color="gray" onClick={() => grade('cancel_item')}>
                  Annuler la question
                </Button>
              </Group>
            </>
          ) : (
            <Text c="dimmed" py="md">
              {scope === 'flagged'
                ? 'Aucune réponse signalée — tout a été corrigé automatiquement.'
                : 'Aucune réponse scannée à corriger pour ce lot.'}
            </Text>
          )}

          <Divider />
          <Group justify="space-between">
            <Text size="xs" c={remainingFlagged ? 'orange' : 'green'}>
              {flaggedTotal === 0
                ? 'Aucune réponse à vérifier'
                : remainingFlagged === 0
                  ? '✓ Toutes les réponses signalées ont été vérifiées'
                  : `${remainingFlagged} réponse(s) signalée(s) encore à vérifier`}
            </Text>
            <Group gap="xs">
              {reviewBatch && remainingFlagged === 0
                && (reviewBatch.status === 'graded' || reviewBatch.status === 'review_pending') && (
                <Button size="xs" color="green" leftSection={<Check size={14} />}
                  onClick={() => { const b = reviewBatch; closeCorrection(); if (b) finalize(b) }}>
                  Valider la correction
                </Button>
              )}
              <Button size="xs" variant="default" onClick={closeCorrection}>Fermer</Button>
            </Group>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}
