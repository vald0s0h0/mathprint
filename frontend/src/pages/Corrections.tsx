// Écran Correction (§9.5) : lots de scans en cartes groupées par classe.
// Chaque carte affiche l'ÉTAPE courante de la pipeline et le BOUTON D'ACTION
// logique qui indique clairement au professeur la prochaine chose à faire
// (déposer / corriger / valider / imprimer), plus un bouton de déblocage
// (relancer) quand la correction est bloquée. Le dépôt d'un scan ne demande pas
// de choisir l'évaluation : le QR signé de chaque page identifie le sujet.
import {
  ActionIcon, Alert, Badge, Box, Button, Card, Checkbox, Divider, FileButton, Group, Kbd,
  Loader, Modal, NumberInput, SegmentedControl, SimpleGrid, Stack, Table, Text, Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, Check, ChevronLeft, ChevronRight, Eye, Inbox, RefreshCw, ScanLine,
  Trash2, Upload,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, getToken } from '../api'
import MathText from '../components/MathText'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import { useAppState } from '../state/AppState'

type SegState = 'green' | 'orange' | 'blue' | 'gray' | 'red'
type Segment = { phase: string; label?: string; state: SegState }
type Batch = {
  id: string; assessment_id: string; status: string; page_count: number
  assessment_title: string; assessment_type: string
  // base de notation d'un contrôle (§ barème) : null pour un entraînement
  note_base: number | null
  class_name: string; class_id: string | null; grade_level: string
  overlay_printed: boolean; overlay_distributed: boolean
  error: string | null; pending_reviews: number; segments: Segment[]; created_at: string
}
// une case à corriger d'un tableau / de cases à trous (table_fill, multi_blank) :
// sa réponse attendue lisible, ce que l'OCR a lu, le verdict auto du moteur
// (auto_ok) et le verdict éventuellement déjà posé par le professeur (teacher_ok).
type Cell = {
  index: number; label: string; expected_display: string
  ocr_text: string; auto_ok: boolean | null; teacher_ok: boolean | null
}
// mode de correction manuelle piloté par le backend (cf. scans._grade_mode)
type GradeMode = 'cells' | 'binary' | 'partial'
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
  // correction manuelle : mode d'UI, réponse attendue lisible, détail par case
  grade_mode: GradeMode; expected_display: string; cells: Cell[]
}
// Unité ATOMIQUE de correction manuelle : UNE case à trous, UN QCM, ou UNE
// réponse rédigée. La file des réponses est aplatie en unités puis regroupée par
// réponse attendue (mêmes cases enchaînées à travers exercices/élèves/sujets) —
// le professeur ne voit qu'UNE réponse à la fois, support d'un OCR défaillant.
type Unit = {
  key: string; respId: string; mode: GradeMode
  cellIndex: number | null   // null hors mode « cases »
  expectedKey: string        // clé de regroupement = réponse attendue normalisée
  attention: boolean         // avait besoin du professeur au chargement (OCR KO)
}
// raccourcis de correction manuelle (paramétrables, cf. Réglages → Pédagogie)
type Shortcuts = { full: string; two_thirds: string; one_third: string; zero: string }
const DEFAULT_SHORTCUTS: Shortcuts = { full: 'f', two_thirds: 'd', one_third: 's', zero: 'q' }
type SandboxResult = {
  filename: string; status: string; pages_added: number
  duplicates_rejected: number; blocked_pages: number; batches_created: string[]
}
type Scope = 'flagged' | 'all'
// récapitulatif prévisionnel montré avant de verrouiller la correction
type SummaryCopy = {
  student: string; points_earned: number; points_total: number
  note: number | null; graded_items: number; flagged: number
}
type BatchSummary = {
  assessment_title: string; note_base: number | null; pending_reviews: number
  scanned_copies: number; copies: SummaryCopy[]
}

const SEG_COLORS: Record<SegState, string> = {
  green: 'var(--mantine-color-green-6)', orange: 'var(--mantine-color-orange-6)',
  blue: 'var(--mantine-color-blue-5)', gray: 'var(--mantine-color-gray-4)',
  red: 'var(--mantine-color-red-6)',
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

// Visualiseur des étapes MÉTIER de la correction : chaque étape porte son
// libellé et sa couleur (vert = fait, bleu = en cours, orange = à corriger,
// gris = à venir, rouge = bloqué). Une flèche montre que le flux avance.
function SegmentBar({ segments }: { segments: Segment[] }) {
  return (
    <Group gap={6} wrap="wrap">
      {segments.map((s, i) => (
        <Group key={s.phase} gap={6} wrap="nowrap">
          <Group gap={5} wrap="nowrap">
            <div style={{
              width: 9, height: 9, borderRadius: '50%', background: SEG_COLORS[s.state],
              boxShadow: s.state === 'orange' || s.state === 'red'
                ? `0 0 0 3px ${SEG_COLORS[s.state]}33` : undefined,
            }} />
            <Text size="xs" c={s.state === 'gray' ? 'dimmed' : undefined}
              fw={s.state === 'orange' || s.state === 'red' ? 700 : 500}>
              {s.label ?? s.phase}
            </Text>
          </Group>
          {i < segments.length - 1 && <Text size="xs" c="dimmed">›</Text>}
        </Group>
      ))}
    </Group>
  )
}

// points à la française pour l'affichage (1,5 — et 2 plutôt que 2,0)
const fmtPts = (v: number) => (Math.round(v * 100) / 100).toString().replace('.', ',')

// clé de regroupement : réponse attendue normalisée (retire $, LaTeX léger,
// accolades, espaces) pour rapprocher les cases IDENTIQUES à travers exercices,
// élèves et sujets — « 8 » et « $8$ » deviennent la même clé.
function normKey(s: string): string {
  return (s || '').replace(/\$/g, '').replace(/\\[a-zA-Z]+/g, '')
    .replace(/[{}\s]/g, '').toLowerCase()
}

// aplatit les réponses en UNITÉS et les regroupe : d'abord les cases (mode
// cells), puis les QCM, puis les rédigées ; à mode égal, mêmes réponses
// attendues consécutives. En scope « à vérifier » on ne garde que les cases où
// l'OCR a échoué (auto_ok null) et les réponses signalées non corrigées.
function buildUnits(items: Item[], scope: Scope): Unit[] {
  const us: Unit[] = []
  for (const it of items) {
    if (it.grade_mode === 'cells') {
      it.cells.forEach((c, ci) => {
        // case VIDE (aucune encre → jamais envoyée à Mathpix) : compte faux et
        // n'est JAMAIS montrée au professeur (§ demande). Son verdict false reste
        // porté par les `verdicts` amorcés → set_cells de la réponse parente OK.
        if (!c.ocr_text.trim() && c.teacher_ok == null) return
        const undecided = c.auto_ok === null && c.teacher_ok == null
        if (scope === 'flagged' && !undecided) return
        us.push({
          key: `${it.response_id}:${ci}`, respId: it.response_id, mode: 'cells',
          cellIndex: ci, expectedKey: normKey(c.expected_display), attention: undecided,
        })
      })
    } else {
      const attention = it.flagged && it.decision_source !== 'teacher'
      if (scope === 'flagged' && !attention) continue
      us.push({
        key: `${it.response_id}:-`, respId: it.response_id, mode: it.grade_mode,
        cellIndex: null, expectedKey: normKey(it.expected_display), attention,
      })
    }
  }
  const rank: Record<GradeMode, number> = { cells: 0, binary: 1, partial: 2 }
  us.sort((a, b) => rank[a.mode] - rank[b.mode]
    || a.expectedKey.localeCompare(b.expectedKey)
    || a.key.localeCompare(b.key))
  return us
}

// image du crop scanné de la zone de réponse : chargée via fetch + token puis
// blob (une balise <img> n'envoie pas nos en-têtes d'auth), comme PdfFrame.
function ScanImage({ responseId, cellIndex }: { responseId: string; cellIndex?: number | null }) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let revoke: string | null = null
    setUrl(null); setFailed(false)
    // en mode « cases », ne montrer QUE la case corrigée (pas tout le tableau).
    const q = cellIndex != null ? `?cell=${cellIndex}` : ''
    fetch(`/api/scans/responses/${responseId}/scan${q}`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`${r.status}`))))
      .then((b) => { revoke = URL.createObjectURL(b); setUrl(revoke) })
      .catch(() => setFailed(true))
    return () => { if (revoke) URL.revokeObjectURL(revoke) }
  }, [responseId, cellIndex])
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
  const [validateBatch, setValidateBatch] = useState<Batch | null>(null)
  const [summary, setSummary] = useState<BatchSummary | null>(null)
  const [mathpixOk, setMathpixOk] = useState(true)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  // file APLATIE en unités (une case / un QCM / une réponse rédigée), regroupées
  // par réponse attendue ; + verdicts Juste(true)/Faux(false)/à trancher(null)
  // par case et par réponse (set_cells exige des verdicts complets à l'envoi).
  const [units, setUnits] = useState<Unit[]>([])
  const [verdicts, setVerdicts] = useState<Record<string, (boolean | null)[]>>({})
  const [scoreInput, setScoreInput] = useState<number | ''>('')
  const [loaded, setLoaded] = useState(false)
  const [resetTarget, setResetTarget] = useState<Batch | null>(null)
  const [resetting, setResetting] = useState(false)
  const [sandboxUploading, setSandboxUploading] = useState(false)
  const [sandboxResults, setSandboxResults] = useState<SandboxResult[]>([])
  const [shortcuts, setShortcuts] = useState<Shortcuts>(DEFAULT_SHORTCUTS)
  const { cycle, matches } = useAppState()

  // raccourcis de correction paramétrés (Réglages → Pédagogie), repli défauts
  useEffect(() => {
    api.get<Record<string, Partial<Shortcuts>>>('/api/settings/system')
      .then((s) => setShortcuts({ ...DEFAULT_SHORTCUTS, ...(s.correction_shortcuts ?? {}) }))
      .catch(() => {})
    // sans clé Mathpix, la correction est indisponible : on prévient et on bloque
    api.get<{ mathpix_configured: boolean }>('/api/scans/config')
      .then((c) => setMathpixOk(c.mathpix_configured))
      .catch(() => {})
  }, [])

  const refresh = useCallback(() => {
    // le .then ne se déclenche qu'en cas de succès : un poll qui échoue ne vide
    // jamais la liste déjà affichée (pas de « plus aucune donnée » transitoire).
    api.get<Batch[]>('/api/scans/batches').then((r) => { setBatches(r); setLoaded(true) })
  }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 4000)
    return () => clearInterval(t)
  }, [refresh])

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
    setItems(rs)
    setUnits(buildUnits(rs, s))
    // amorce les verdicts par case : verdict déjà posé par le prof, sinon celui
    // du moteur (auto_ok) — seules les cases non tranchées (null) restent à faire
    const vmap: Record<string, (boolean | null)[]> = {}
    for (const it of rs)
      if (it.grade_mode === 'cells')
        vmap[it.response_id] = it.cells.map((c) => (c.teacher_ok != null ? c.teacher_ok : c.auto_ok))
    setVerdicts(vmap)
    setIdx(0); setScoreInput('')
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

  function closeCorrection() {
    setReviewBatch(null); setItems([]); setUnits([]); setVerdicts({}); refresh()
  }

  const advance = useCallback(() => setIdx((i) => Math.min(i + 1, units.length - 1)), [units.length])

  // corrige une réponse EN UN BLOC (QCM ou rédigée) par son id, met à jour la
  // note affichée en place (append-only côté serveur), puis passe à la suivante.
  async function gradeBlock(action: string, extra?: { ratio?: number }) {
    const u = units[idx]
    if (!u || u.mode === 'cells') return
    const rid = u.respId
    try {
      await api.post(`/api/scans/responses/${rid}/resolve`, { action, ...extra })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      return
    }
    const r = Math.max(0, Math.min(1, extra?.ratio ?? 0))
    setItems((prev) => prev.map((x) => x.response_id !== rid ? x : (
      action === 'cancel_item'
        ? { ...x, decision_source: 'teacher', cancelled: true, full_credit: false, current_points: 0 }
        : { ...x, decision_source: 'teacher', cancelled: false, full_credit: r >= 0.999,
            current_points: Math.round(r * x.bareme_points * 100) / 100 }
    )))
    setScoreInput('')
    advance()
  }
  const gradeRatio = (ratio: number) => gradeBlock('set_ratio', { ratio })

  // enregistre une réponse à cases dès que TOUTES ses cases sont tranchées
  // (set_cells exige des verdicts complets) : le backend recalcule le barème
  // (points = nombre de cases justes) et rend l'overlay cohérent avec la note.
  async function submitCellsFor(rid: string, arr: (boolean | null)[]) {
    const verdictsB = arr.map((v) => !!v)
    try {
      await api.post(`/api/scans/responses/${rid}/resolve`,
        { action: 'set_cells', cell_verdicts: verdictsB })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      return
    }
    const correct = verdictsB.filter(Boolean).length
    setItems((prev) => prev.map((x) => x.response_id !== rid ? x : ({
      ...x, decision_source: 'teacher', cancelled: false,
      full_credit: correct === verdictsB.length,
      current_points: verdictsB.length
        ? Math.round((correct / verdictsB.length) * x.bareme_points * 100) / 100 : 0,
      cells: x.cells.map((c, ci) => ({ ...c, teacher_ok: verdictsB[ci] })),
    })))
  }

  // pose le verdict d'UNE case (unité courante) et avance ; dès que la réponse
  // parente n'a plus aucune case en attente, elle est enregistrée automatiquement.
  function markCellUnit(val: boolean) {
    const u = units[idx]
    if (!u || u.mode !== 'cells' || u.cellIndex == null) return
    const rid = u.respId, ci = u.cellIndex
    const arr = (verdicts[rid] ?? []).slice()
    arr[ci] = val
    setVerdicts((m) => ({ ...m, [rid]: arr }))
    if (!arr.some((v) => v === null)) submitCellsFor(rid, arr)
    advance()
  }

  // raccourcis clavier de correction manuelle (paramétrés dans les réglages).
  // Navigation ←/→ d'une unité à l'autre ; le sens de F/Q dépend du MODE :
  // case Juste/Faux, QCM Juste/Faux, ou crédit partiel (F/D/S/Q) pour une rédigée.
  useEffect(() => {
    const u = units[idx]
    if (!reviewBatch || !u) return
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      const k = e.key.toLowerCase()
      if (e.key === 'ArrowRight') { setIdx((i) => Math.min(i + 1, units.length - 1)); return }
      if (e.key === 'ArrowLeft') { setIdx((i) => Math.max(i - 1, 0)); return }
      if (u.mode === 'cells') {
        if (k === shortcuts.full) markCellUnit(true)
        else if (k === shortcuts.zero) markCellUnit(false)
        return
      }
      if (u.mode === 'binary') {
        if (k === shortcuts.full) gradeRatio(1)
        else if (k === shortcuts.zero) gradeRatio(0)
        return
      }
      if (k === shortcuts.full) gradeRatio(1)
      else if (k === shortcuts.two_thirds) gradeRatio(2 / 3)
      else if (k === shortcuts.one_third) gradeRatio(1 / 3)
      else if (k === shortcuts.zero) gradeRatio(0)
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

  // ouvre la modale de validation : récapitulatif prévisionnel (note de chaque
  // élève, réponses encore à corriger) À VÉRIFIER avant de verrouiller
  async function openValidate(b: Batch) {
    setValidateBatch(b); setSummary(null)
    try {
      setSummary(await api.get<BatchSummary>(`/api/scans/batches/${b.id}/summary`))
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      setValidateBatch(null)
    }
  }
  function closeValidate() { setValidateBatch(null); setSummary(null) }
  async function confirmValidate() {
    const b = validateBatch
    closeValidate()
    if (b) await finalize(b)
  }

  async function finalize(b: Batch) {
    try {
      const r = await api.post<{ evidence_created: number; overlay_error: string | null }>(
        `/api/scans/batches/${b.id}/finalize`)
      if (r.overlay_error) {
        notifications.show({ color: 'orange', autoClose: 8000,
          message: `Notes validées, mais copies corrigées non générées : ${r.overlay_error}. Utilisez « Relancer » pour réessayer.` })
      } else {
        notifications.show({ color: 'green', message: `Correction validée — ${r.evidence_created} preuve(s) de compétence, copies corrigées prêtes` })
      }
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  // « Effacer la correction » / « Recommencer » : purge le lot (scans, images,
  // notes, overlays) et remet le sujet en attente de scan. Confirmation requise.
  async function resetCorrection() {
    if (!resetTarget) return
    setResetting(true)
    try {
      await api.del(`/api/scans/batches/${resetTarget.id}`)
      notifications.show({ color: 'green', message: 'Correction effacée — vous pouvez re-déposer un scan propre' })
      setResetTarget(null)
      if (reviewBatch?.id === resetTarget.id) closeCorrection()
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setResetting(false)
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

  // unité courante + la réponse (fraîche) et la case dont elle dérive
  const cur = units[idx]
  const curItem = cur ? items.find((x) => x.response_id === cur.respId) ?? null : null
  const curCell = cur && cur.mode === 'cells' && curItem && cur.cellIndex != null
    ? curItem.cells[cur.cellIndex] ?? null : null
  const cellVal = cur && cur.mode === 'cells' && cur.cellIndex != null
    ? verdicts[cur.respId]?.[cur.cellIndex] ?? null : null
  // position dans le groupe des réponses attendues IDENTIQUES (cases enchaînées)
  const sameGroup = cur ? units.filter((u) => u.mode === cur.mode && u.expectedKey === cur.expectedKey) : []
  const samePos = cur ? sameGroup.findIndex((u) => u.key === cur.key) + 1 : 0

  // une unité est « réglée » : case → verdict posé ; bloc → note passée en teacher
  const unitResolved = (u: Unit): boolean => {
    if (u.mode === 'cells' && u.cellIndex != null) return verdicts[u.respId]?.[u.cellIndex] != null
    return items.find((x) => x.response_id === u.respId)?.decision_source === 'teacher'
  }
  // réponses encore à vérifier (unités qui réclamaient le prof, pas encore réglées)
  const attentionUnits = units.filter((u) => u.attention)
  const remaining = attentionUnits.filter((u) => !unitResolved(u)).length

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

      {!mathpixOk && (
        <Alert color="red" variant="light" icon={<AlertTriangle size={18} />}
          title="Clé Mathpix requise pour corriger">
          La correction lit l'écriture manuscrite des élèves via Mathpix. Tant
          qu'aucune clé n'est configurée, le dépôt de scan est bloqué et aucune
          copie ne peut être corrigée. Ajoutez la clé dans{' '}
          <b>Paramètres → API</b>, puis revenez déposer vos scans.
        </Alert>
      )}

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
          <Tooltip label="Configurez d'abord la clé Mathpix (Paramètres → API)" disabled={mathpixOk}>
            <FileButton onChange={uploadSandbox} multiple disabled={!mathpixOk}
              accept="application/pdf,image/jpeg,image/png,image/heic,image/heif">
              {(props) => (
                <Button {...props} size="xs" variant="light" leftSection={<Upload size={14} />}
                  loading={sandboxUploading} disabled={!mathpixOk}>
                  Déposer en vrac
                </Button>
              )}
            </FileButton>
          </Tooltip>
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

      {!loaded && (
        <Card withBorder padding="xl">
          <Group justify="center" gap="sm">
            <Loader size="sm" />
            <Text size="sm" c="dimmed">Chargement des corrections…</Text>
          </Group>
        </Card>
      )}

      {loaded && groups.length === 0 && (
        <Card withBorder padding="xl">
          <Stack align="center" gap="xs">
            <ScanLine size={36} strokeWidth={1.4} opacity={0.5} />
            <Text fw={600}>Aucun lot de scans {cycle !== 'all' && `en ${cycle}`}</Text>
            <Text size="sm" c="dimmed" ta="center">
              Après l'évaluation, scannez les copies et déposez-les dans le bac à
              sable ci-dessus — chaque page est associée au bon sujet par son QR.
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

                    {/* Un bouton principal par étape indique la prochaine action ;
                        « Corriger les copies » (ouvre la modale scan + réponse
                        attendue) est TOUJOURS distinct de « Valider » (verrouille),
                        et un déblocage/effacement est offert quand c'est utile. */}
                    <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
                      {stage === 'awaiting' && (
                        <Text size="xs" c="dimmed" ta="right" style={{ maxWidth: 190 }}>
                          Déposez le scan dans le <b>bac à sable</b> en haut de page.
                        </Text>
                      )}

                      {stage === 'processing' && (
                        <>
                          <Button size="xs" variant="light" loading disabled>Correction en cours…</Button>
                          <Tooltip label="Si la correction semble bloquée, relancez-la">
                            <Button size="xs" variant="subtle" color="gray"
                              leftSection={<RefreshCw size={14} />} onClick={() => retry(b)}>
                              Relancer
                            </Button>
                          </Tooltip>
                        </>
                      )}

                      {stage === 'error' && (
                        <>
                          <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />}
                            onClick={() => retry(b)}>
                            Relancer
                          </Button>
                          <Button size="xs" variant="light" onClick={() => openCorrection(b, 'all')}>
                            Corriger les copies
                          </Button>
                          <Tooltip label="Effacer, puis re-déposez un scan propre dans le bac à sable">
                            <Button size="xs" variant="subtle" color="red"
                              leftSection={<Trash2 size={14} />} onClick={() => setResetTarget(b)}>
                              Effacer
                            </Button>
                          </Tooltip>
                        </>
                      )}

                      {stage === 'review' && (
                        <>
                          <Button size="xs" color="orange" leftSection={<ScanLine size={14} />}
                            onClick={() => openCorrection(b, 'flagged')}>
                            Corriger les copies ({b.pending_reviews})
                          </Button>
                          <Tooltip label="Effacer cette correction et re-scanner depuis zéro">
                            <ActionIcon variant="subtle" color="red" size="lg" onClick={() => setResetTarget(b)}>
                              <Trash2 size={16} />
                            </ActionIcon>
                          </Tooltip>
                        </>
                      )}

                      {stage === 'validate' && (
                        <>
                          <Button size="xs" leftSection={<ScanLine size={14} />}
                            onClick={() => openCorrection(b, 'all')}>
                            Corriger les copies
                          </Button>
                          <Tooltip multiline w={250}
                            label="Ouvre un récapitulatif (note de chaque élève, réponses restant à corriger) à vérifier avant de verrouiller et générer les copies corrigées.">
                            <Button size="xs" color="green" leftSection={<Check size={14} />}
                              onClick={() => openValidate(b)}>
                              Valider la correction
                            </Button>
                          </Tooltip>
                          <Tooltip label="Effacer cette correction et re-scanner depuis zéro">
                            <ActionIcon variant="subtle" color="red" size="lg" onClick={() => setResetTarget(b)}>
                              <Trash2 size={16} />
                            </ActionIcon>
                          </Tooltip>
                        </>
                      )}

                      {stage === 'done' && (
                        <>
                          {overlayReady ? (
                            <PrintButton assessmentId={b.assessment_id}
                              file="correction_overlay.pdf" label="Imprimer les copies corrigées" />
                          ) : (
                            <Button size="xs" variant="light" onClick={() => createOverlay(b)}>
                              Générer les copies corrigées
                            </Button>
                          )}
                          <Button size="xs" variant="subtle" leftSection={<Eye size={14} />}
                            onClick={() => setPreviewId(b.assessment_id)}>
                            Aperçu
                          </Button>
                          <Button size="xs" variant="subtle" leftSection={<ScanLine size={14} />}
                            onClick={() => openCorrection(b, 'all')}>
                            Corriger
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

      <Modal opened={!!resetTarget} onClose={() => setResetTarget(null)}
        title={<Text fw={650}>Effacer la correction</Text>}>
        <Stack>
          <Text size="sm">
            Effacer définitivement la correction de « {resetTarget?.assessment_title} » ?
          </Text>
          <Text size="xs" c="dimmed">
            Supprime les scans, les images recadrées, les notes attribuées et les
            copies corrigées (overlays) de ce lot. Le sujet lui-même, ses copies
            et son barème sont conservés : il repasse « en attente de scan », prêt
            pour un nouveau dépôt.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setResetTarget(null)}>Annuler</Button>
            <Button color="red" loading={resetting} onClick={resetCorrection}>
              Effacer la correction
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={!!validateBatch} onClose={closeValidate} size="lg"
        title={<Text fw={650}>Valider la correction — {validateBatch?.assessment_title}</Text>}>
        {!summary ? (
          <Text c="dimmed" py="md">Calcul du récapitulatif…</Text>
        ) : (
          <Stack>
            {summary.pending_reviews > 0 && (
              <Alert color="orange" variant="light" icon={<AlertTriangle size={18} />}>
                <Group justify="space-between" wrap="nowrap">
                  <Text size="sm">
                    Il reste <b>{summary.pending_reviews}</b> réponse(s) à corriger.
                    Terminez la correction avant de valider.
                  </Text>
                  <Button size="xs" color="orange" style={{ flexShrink: 0 }}
                    onClick={() => { const b = validateBatch; closeValidate(); if (b) openCorrection(b, 'flagged') }}>
                    Corriger les copies
                  </Button>
                </Group>
              </Alert>
            )}
            <Text size="sm" c="dimmed">
              {summary.scanned_copies} copie(s) scannée(s)
              {summary.note_base ? ` · noté sur ${summary.note_base}` : ' · entraînement (non noté)'}.
              Vérifiez les notes ci-dessous : valider les verrouille, calcule la note
              de chaque élève et génère les copies corrigées à imprimer.
            </Text>
            <div style={{ maxHeight: '46vh', overflowY: 'auto' }}>
              <Table stickyHeader highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Élève</Table.Th>
                    <Table.Th w={110} ta="center">À corriger</Table.Th>
                    <Table.Th w={110} ta="right">Points</Table.Th>
                    <Table.Th w={80} ta="right">Note</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {summary.copies.map((c) => (
                    <Table.Tr key={c.student}>
                      <Table.Td>{c.student}</Table.Td>
                      <Table.Td ta="center">
                        {c.flagged > 0
                          ? <Badge size="sm" color="orange" variant="light">{c.flagged}</Badge>
                          : <Text size="sm" c="dimmed">—</Text>}
                      </Table.Td>
                      <Table.Td ta="right" style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {fmtPts(c.points_earned)} / {fmtPts(c.points_total)}
                      </Table.Td>
                      <Table.Td ta="right" fw={600} style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {c.note != null && summary.note_base
                          ? `${fmtPts(c.note)}/${summary.note_base}` : '—'}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                  {summary.copies.length === 0 && (
                    <Table.Tr><Table.Td colSpan={4}>
                      <Text size="sm" c="dimmed">Aucune copie scannée à valider.</Text>
                    </Table.Td></Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </div>
            <Group justify="flex-end">
              <Button variant="subtle" onClick={closeValidate}>Annuler</Button>
              <Button color="green" leftSection={<Check size={14} />}
                disabled={summary.pending_reviews > 0 || summary.copies.length === 0}
                onClick={confirmValidate}>
                Valider et générer les copies corrigées
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <Modal opened={!!reviewBatch} onClose={closeCorrection} size="xl"
        title={<Text fw={650}>Correction — {reviewBatch?.assessment_title}</Text>}>
        <Stack>
          <Group justify="space-between" wrap="wrap">
            <SegmentedControl size="xs" value={scope}
              onChange={(v) => changeScope(v as Scope)}
              data={[
                { label: `À vérifier${attentionUnits.length ? ` (${remaining})` : ''}`, value: 'flagged' },
                { label: 'Toutes les réponses', value: 'all' },
              ]} />
            {units.length > 0 && (
              <Group gap={6} wrap="nowrap">
                <ActionIcon variant="light" disabled={idx <= 0} onClick={() => setIdx((i) => i - 1)}>
                  <ChevronLeft size={16} />
                </ActionIcon>
                <Text size="xs" c="dimmed">{idx + 1} / {units.length}</Text>
                <ActionIcon variant="light" disabled={idx >= units.length - 1}
                  onClick={() => setIdx((i) => i + 1)}>
                  <ChevronRight size={16} />
                </ActionIcon>
              </Group>
            )}
          </Group>

          {cur && curItem ? (
            <>
              {/* en-tête minimal : à qui, l'état de la note, et le REGROUPEMENT par
                  réponse attendue identique — PAS l'énoncé (la modale n'est qu'un
                  support pour un OCR défaillant, case par case). */}
              <Group justify="space-between" wrap="nowrap">
                <Group gap="xs" wrap="nowrap">
                  <Badge variant="filled" color="indigo">{curItem.group_label}</Badge>
                  <ItemStatus it={curItem} />
                  {sameGroup.length > 1 && (
                    <Badge variant="light" color="grape" size="sm">
                      {samePos}/{sameGroup.length} même réponse
                    </Badge>
                  )}
                </Group>
                <Text size="sm" fw={600}>{curItem.student}</Text>
              </Group>

              {/* scan de l'élève À GAUCHE, réponse attendue À DROITE : ajuster vite
                  un OCR défaillant sans le contexte de l'exercice. */}
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
                <Card withBorder padding="xs">
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>
                    Scan de l'élève{cur.mode === 'cells' && curCell?.label ? ` — ${curCell.label}` : ''}
                  </Text>
                  <ScanImage responseId={cur.respId}
                    cellIndex={cur.mode === 'cells' ? cur.cellIndex : null} />
                </Card>
                <Card withBorder padding="sm">
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>
                    Réponse attendue{curCell?.label ? ` — ${curCell.label}` : ''}
                  </Text>
                  <Box fz="1.7rem" fw={700} style={{ lineHeight: 1.3 }}>
                    <MathText text={(cur.mode === 'cells' ? curCell?.expected_display
                      : curItem.expected_display) || '—'} />
                  </Box>
                  <Text size="xs" c="dimmed" mt={8}>
                    OCR / CV a lu : {cur.mode === 'cells'
                      ? (curCell?.ocr_text || '∅')
                      : (curItem.ocr_text || (curItem.selected_choices.length
                          ? `cases ${curItem.selected_choices.join(', ')}` : '∅'))}
                    {cur.mode !== 'cells' && curItem.ocr_confidence != null
                      && ` · confiance ${(curItem.ocr_confidence * 100).toFixed(0)} %`}
                  </Text>
                  {cur.mode !== 'cells' && curItem.reason_code &&
                    <Text size="xs" c="dimmed">Motif : {curItem.reason_code}</Text>}
                </Card>
              </SimpleGrid>

              {/* actions — ordre gauche→droite : Faux … Juste (§ demande) */}
              {cur.mode === 'cells' ? (
                <Group>
                  <Button color="red" variant={cellVal === false ? 'filled' : 'light'}
                    onClick={() => markCellUnit(false)}>
                    Faux <Kbd ml={6}>{shortcuts.zero.toUpperCase()}</Kbd>
                  </Button>
                  <Button color="green" variant={cellVal === true ? 'filled' : 'light'}
                    onClick={() => markCellUnit(true)}>
                    Juste <Kbd ml={6}>{shortcuts.full.toUpperCase()}</Kbd>
                  </Button>
                </Group>
              ) : cur.mode === 'binary' ? (
                <Group>
                  <Button color="red" variant="light" onClick={() => gradeRatio(0)}>
                    Faux — 0 point <Kbd ml={6}>{shortcuts.zero.toUpperCase()}</Kbd>
                  </Button>
                  <Button color="green" onClick={() => gradeRatio(1)}>
                    Juste — {fmtPts(curItem.bareme_points)} <Kbd ml={6}>{shortcuts.full.toUpperCase()}</Kbd>
                  </Button>
                  <Button variant="subtle" color="gray" onClick={() => gradeBlock('cancel_item')}>
                    Annuler la question
                  </Button>
                </Group>
              ) : (
                <>
                  <Group>
                    <Button color="red" variant="light" onClick={() => gradeRatio(0)}>
                      Faux — 0 <Kbd ml={6}>{shortcuts.zero.toUpperCase()}</Kbd>
                    </Button>
                    <Button color="orange" variant="light" onClick={() => gradeRatio(1 / 3)}>
                      1⁄3 — {fmtPts(curItem.bareme_points / 3)} <Kbd ml={6}>{shortcuts.one_third.toUpperCase()}</Kbd>
                    </Button>
                    <Button color="teal" variant="light" onClick={() => gradeRatio(2 / 3)}>
                      2⁄3 — {fmtPts(curItem.bareme_points * 2 / 3)} <Kbd ml={6}>{shortcuts.two_thirds.toUpperCase()}</Kbd>
                    </Button>
                    <Button color="green" onClick={() => gradeRatio(1)}>
                      Juste — {fmtPts(curItem.bareme_points)} <Kbd ml={6}>{shortcuts.full.toUpperCase()}</Kbd>
                    </Button>
                  </Group>
                  <Group>
                    <NumberInput placeholder="points" w={120} min={0} max={curItem.bareme_points} step={0.5}
                      decimalScale={2} value={scoreInput}
                      onChange={(v) => setScoreInput(v === '' ? '' : Number(v))} />
                    <Button variant="light" disabled={scoreInput === '' || !curItem.bareme_points}
                      onClick={() => gradeRatio(Number(scoreInput) / curItem.bareme_points)}>
                      Attribuer ces points
                    </Button>
                    <Button variant="subtle" color="gray" onClick={() => gradeBlock('cancel_item')}>
                      Annuler la question
                    </Button>
                  </Group>
                </>
              )}
              <Text size="xs" c="dimmed">
                <Kbd>←</Kbd>/<Kbd>→</Kbd> pour naviguer{cur.mode === 'cells'
                  ? ' · chaque case validée est enregistrée automatiquement (0,5 pt/case juste)' : ''}.
              </Text>
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
            <Text size="xs" c={remaining ? 'orange' : 'green'}>
              {attentionUnits.length === 0
                ? 'Aucune réponse à vérifier'
                : remaining === 0
                  ? '✓ Toutes les réponses signalées ont été vérifiées'
                  : `${remaining} réponse(s) signalée(s) encore à vérifier`}
            </Text>
            <Group gap="xs">
              {reviewBatch && remaining === 0
                && (reviewBatch.status === 'graded' || reviewBatch.status === 'review_pending') && (
                <Button size="xs" color="green" leftSection={<Check size={14} />}
                  onClick={() => { const b = reviewBatch; closeCorrection(); if (b) openValidate(b) }}>
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
