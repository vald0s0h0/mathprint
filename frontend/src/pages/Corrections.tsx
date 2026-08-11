// Écran Correction (§9.5) : lots de scans en cartes groupées par classe.
// Chaque carte affiche l'ÉTAPE courante de la pipeline et le BOUTON D'ACTION
// logique qui indique clairement au professeur la prochaine chose à faire
// (déposer / corriger / valider / imprimer), plus un bouton de déblocage
// (relancer) quand la correction est bloquée. Le dépôt d'un scan ne demande pas
// de choisir l'évaluation : le QR signé de chaque page identifie le sujet.
import {
  ActionIcon, Alert, Badge, Box, Button, Card, Checkbox, Divider, FileButton, Group, Kbd,
  Loader, Modal, NumberInput, SegmentedControl, SimpleGrid, Stack, Table, Text,
  Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, Check, ChevronLeft, ChevronRight, Eye, Inbox, RefreshCw, ScanLine,
  Trash2, Upload,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, getToken } from '../api'
import { MathAnswer } from '../components/MathText'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import { useAppState } from '../state/AppState'

type SegState = 'green' | 'orange' | 'blue' | 'gray' | 'red'
type Segment = { phase: string; label?: string; state: SegState }
type Batch = {
  id: string; assessment_id: string; status: string; page_count: number
  assessment_title: string; assessment_type: string
  // base de scoring ; les entraînements ne l'impriment pas
  note_base: number | null
  class_name: string; class_id: string | null; grade_level: string
  overlay_printed: boolean; overlay_distributed: boolean
  error: string | null; pending_reviews: number; pending_ocr: number; pending_llm: number
  ocr_threshold: number
  segments: Segment[]; created_at: string
}
// une case à corriger d'un tableau / de cases à trous (table_fill, multi_blank) :
// sa réponse attendue lisible, ce que l'OCR a lu, le crédit calculé par le moteur
// (auto_credit) et celui éventuellement déjà posé par le professeur (teacher_credit).
type Cell = {
  index: number; label: string; expected_display: string
  // crédit 1 (juste) / 0.5 (demi-point) / 0 (faux) ; null = non tranché
  ocr_text: string; ocr_latex: string
  auto_credit: number | null; teacher_credit: number | null
}
// mode de correction manuelle piloté par le backend (cf. scans._grade_mode)
type GradeMode = 'cells' | 'binary' | 'partial'
// une réponse d'élève à (re)corriger : signalée par le moteur (flagged) ou
// simplement relue par le professeur. Clé de résolution = response_id.
type Item = {
  response_id: string; review_id: string | null; flagged: boolean
  category: string | null; student: string; student_order: number; statement: string
  expected: Record<string, unknown>; correction: string
  ocr_text: string; ocr_latex: string
  selected_choices: number[]; selected_pairs: number[][]
  ocr_confidence: number | null; reason_code: string
  decision_source: string; proposed_score: number; max_score: number
  current_points: number; full_credit: boolean; cancelled: boolean
  bareme_points: number; zone_id: string | null; has_scan: boolean
  group_key: string; group_label: string; response_type: string; sequence: number
  // correction manuelle : mode d'UI, réponse attendue lisible, détail par case
  grade_mode: GradeMode; expected_display: string; cells: Cell[]
  cell_confidences?: number[]
  ocr_controls?: {
    kind: 'text' | 'boxes' | 'matching'
    boxes?: { index: number; row?: number | null; col?: number | null
      x: number; y: number; w: number; h: number }[]
    left?: { index: number; x: number; y: number }[]
    right?: { index: number; x: number; y: number }[]
  }
  // verdicts du correcteur LLM (raisonnements, réponses écrites longues) : le
  // professeur relit ce qu'il a décidé, champ par champ, avant de valider
  llm_notes: { champ: string; cell_index: number | null; points: number; bareme: number
               verdict: string; motif: string; confidence?: number | null
               requires_review?: boolean }[]
  llm_threshold: number; llm_min_confidence: number | null
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
  responseRank: number       // ordre métier de la file (QCM → matching → écrit → raisonnement)
}
type OcrUnit = { key: string; respId: string; cellIndex: number | null }
// raccourcis de correction manuelle (paramétrables, cf. Réglages → Pédagogie)
type Shortcuts = { full: string; two_thirds: string; one_third: string; zero: string }
const DEFAULT_SHORTCUTS: Shortcuts = { full: 'f', two_thirds: 'd', one_third: 's', zero: 'q' }
type SandboxResult = {
  filename: string; file_kind: 'image' | 'pdf' | 'unknown'; status: string; pages_added: number
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
type Stage = 'awaiting' | 'processing' | 'error' | 'ocr_review' | 'review' | 'validate' | 'done'
function stageOf(b: Batch): Stage {
  if (b.status === 'awaiting_scan') return 'awaiting'
  if (b.error) return 'error'
  if (b.status === 'finalized' || b.status === 'overlay_ready') return 'done'
  if (b.status === 'ocr_review_pending' || b.pending_ocr > 0) return 'ocr_review'
  if (b.status === 'graded' || b.status === 'review_pending')
    return b.pending_reviews > 0 ? 'review' : 'validate'
  return 'processing'  // uploaded → split → … → ocr_complete
}
const STAGE_BADGE: Record<Stage, { label: string; color: string }> = {
  awaiting: { label: 'en attente de scan', color: 'gray' },
  processing: { label: 'correction en cours', color: 'blue' },
  error: { label: 'bloqué', color: 'red' },
  ocr_review: { label: 'lecture à vérifier', color: 'blue' },
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
// arrondi au millième pour l'AFFICHAGE seulement : les points d'un exercice ne
// sont jamais arrondis dans le calcul (§ barème), et le pas est 0,125.
const fmtPts = (v: number) => (Math.round(v * 1000) / 1000).toString().replace('.', ',')

function llmVerdictColor(verdict: string): 'green' | 'red' | 'orange' {
  const value = (verdict || '').trim().toLowerCase()
  if (value.startsWith('juste')) return 'green'
  if (value.startsWith('faux') || value.startsWith('illis')) return 'red'
  return 'orange' // partiel ou correcteur indisponible
}

// clé de regroupement : réponse attendue normalisée (retire $, LaTeX léger,
// accolades, espaces) pour rapprocher les cases IDENTIQUES à travers exercices,
// élèves et sujets — « 8 » et « $8$ » deviennent la même clé.
function normKey(s: string): string {
  return (s || '').replace(/\$/g, '').replace(/\\[a-zA-Z]+/g, '')
    .replace(/[{}\s]/g, '').toLowerCase()
}

// Aplatit les réponses en UNITÉS et les ordonne par coût de relecture : QCM,
// matching, réponses manuscrites, puis raisonnements avancés. Dans chaque
// famille, les mêmes réponses attendues restent consécutives à travers tous les
// élèves. En scope « à vérifier », UNE REVUE OUVERTE sur une
// réponse à cellules rend toutes ses cellules visibles : un verdict automatique
// par cellule n'est qu'une proposition, jamais une validation du professeur.
// L'ancien filtre ne gardait que `auto_credit === null` : une réponse signalée
// pour confiance globale faible pouvait donc compter dans le badge backend tout
// en disparaissant entièrement de la modale.
function responseRank(item: Item): number {
  if (item.response_type.startsWith('qcm') || item.response_type === 'checkbox_grid') return 0
  if (item.response_type === 'matching') return 1
  if (item.response_type === 'multiline_text' || item.response_type === 'composite'
      || item.decision_source === 'deepseek' || (item.llm_notes ?? []).length > 0) return 3
  return 2 // réponses manuscrites courtes, tableaux, trous et tracés
}

function llmNotesForCell(item: Item, cellIndex: number) {
  const cell = item.cells[cellIndex]
  return (item.llm_notes ?? []).filter((n) => n.cell_index != null
    ? n.cell_index === cellIndex : !!cell && n.champ === cell.label)
}

function cellNeedsTeacher(item: Item, cellIndex: number): boolean {
  if (!item.flagged || item.decision_source === 'teacher') return false
  // Les revues LLM sont déclenchées champ par champ. Une réponse à plusieurs
  // cases peut donc être signalée à cause d'UNE case à 85 %, sans que sa case
  // voisine à 98 % doive être montrée au professeur.
  if (item.reason_code.startsWith('llm_')) {
    const notes = llmNotesForCell(item, cellIndex)
    return notes.some((n) => n.requires_review === true)
  }
  return true
}

function blockNeedsTeacher(item: Item): boolean {
  if (!item.flagged || item.decision_source === 'teacher') return false
  if (!item.reason_code.startsWith('llm_')) return true
  return (item.llm_notes ?? []).some((n) => n.requires_review === true)
}

function buildUnits(items: Item[], scope: Scope): Unit[] {
  const us: Unit[] = []
  for (const it of items) {
    if (it.grade_mode === 'cells') {
      it.cells.forEach((c, ci) => {
        const cellAttention = cellNeedsTeacher(it, ci)
        // case VIDE (aucune encre → jamais envoyée à Mathpix) : compte faux et
        // reste cachée dans « Toutes les réponses ». Mais si la RÉPONSE est
        // signalée, même le blanc est à confirmer : une trace pâle sous le seuil
        // CV peut précisément être la cause de la revue.
        if (!cellAttention && !c.ocr_text.trim() && c.teacher_credit == null) return
        if (scope === 'flagged' && !cellAttention) return
        us.push({
          key: `${it.response_id}:${ci}`, respId: it.response_id, mode: 'cells',
          cellIndex: ci, expectedKey: normKey(c.expected_display), attention: cellAttention,
          responseRank: responseRank(it),
        })
      })
    } else {
      const attention = blockNeedsTeacher(it)
      if (scope === 'flagged' && !attention) continue
      us.push({
        key: `${it.response_id}:-`, respId: it.response_id, mode: it.grade_mode,
        cellIndex: null, expectedKey: normKey(it.expected_display), attention,
        responseRank: responseRank(it),
      })
    }
  }
  // La clé attendue reste le deuxième critère : toutes les copies portant la
  // même réponse restent consécutives à travers les élèves.
  us.sort((a, b) => a.responseRank - b.responseRank
    || a.expectedKey.localeCompare(b.expectedKey)
    || a.key.localeCompare(b.key))
  return us
}

function buildOcrUnits(items: Item[], threshold: number): OcrUnit[] {
  const out: OcrUnit[] = []
  for (const item of items) {
    if (item.response_type === 'table_fill' || item.response_type === 'multi_blank') {
      const confs = item.cell_confidences ?? []
      item.cells.forEach((_, i) => {
        if (!confs.length || (confs[i] ?? item.ocr_confidence ?? 0) < threshold)
          out.push({ key: `${item.response_id}:${i}`, respId: item.response_id, cellIndex: i })
      })
    } else {
      out.push({ key: `${item.response_id}:-`, respId: item.response_id, cellIndex: null })
    }
  }
  const byResponse = new Map(items.map((item) => [item.response_id, item]))
  out.sort((a, b) => {
    const ia = byResponse.get(a.respId)!, ib = byResponse.get(b.respId)!
    const family = (it: Item) => it.response_type.startsWith('qcm')
      || it.response_type === 'checkbox_grid' || it.response_type === 'matching' ? 0
        : it.response_type === 'multiline_text' || it.response_type === 'composite' ? 2 : 1
    return family(ia) - family(ib)
      || ia.group_key.localeCompare(ib.group_key)
      || (a.cellIndex ?? -1) - (b.cellIndex ?? -1)
      || ia.student_order - ib.student_order
  })
  return out
}

// image du crop scanné de la zone de réponse : chargée via fetch + token puis
// blob (une balise <img> n'envoie pas nos en-têtes d'auth), comme PdfFrame.
function ScanImage({ responseId, cellIndex, expectedOverlay = false, large = false }: {
  responseId: string; cellIndex?: number | null; expectedOverlay?: boolean; large?: boolean
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let revoke: string | null = null
    setUrl(null); setFailed(false)
    // en mode « cases », ne montrer QUE la case corrigée (pas tout le tableau).
    const params = new URLSearchParams()
    if (cellIndex != null) params.set('cell', String(cellIndex))
    if (expectedOverlay) params.set('expected_overlay', 'true')
    const q = params.size ? `?${params.toString()}` : ''
    fetch(`/api/scans/responses/${responseId}/scan${q}`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`${r.status}`))))
      .then((b) => { revoke = URL.createObjectURL(b); setUrl(revoke) })
      .catch(() => setFailed(true))
    return () => { if (revoke) URL.revokeObjectURL(revoke) }
  }, [responseId, cellIndex, expectedOverlay])
  if (failed) return (
    <Text size="xs" c="dimmed" p="sm">
      Zone non scannée (vide, ou lot sans scan) — rien à visualiser ici.
    </Text>
  )
  if (!url) return <Text size="xs" c="dimmed" p="sm">Chargement du scan…</Text>
  return (
    <img src={url} alt="Scan de la réponse de l'élève"
      style={{ maxWidth: '100%', maxHeight: 260, objectFit: 'contain',
        ...(large ? { maxHeight: 430 } : {}),
        border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4, background: '#fff' }} />
  )
}

function OcrScan({ item, cellIndex, selectedChoices, selectedPairs, matchStart,
  onChoice, onMatchPoint }: {
  item: Item; cellIndex: number | null; selectedChoices: number[]; selectedPairs: number[][]
  matchStart: number | null; onChoice: (box: { index: number; row?: number | null
    col?: number | null }) => void; onMatchPoint: (side: 'left' | 'right', index: number) => void
}) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let revoke: string | null = null
    const q = cellIndex != null ? `?cell=${cellIndex}` : ''
    fetch(`/api/scans/responses/${item.response_id}/scan${q}`,
      { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => r.ok ? r.blob() : Promise.reject(new Error(`${r.status}`)))
      .then((b) => { revoke = URL.createObjectURL(b); setUrl(revoke) })
      .catch(() => setUrl(null))
    return () => { if (revoke) URL.revokeObjectURL(revoke) }
  }, [item.response_id, cellIndex])
  const controls = item.ocr_controls
  const isGrid = item.response_type === 'checkbox_grid'
  const chosen = (box: { index: number; row?: number | null; col?: number | null }) =>
    isGrid && box.row != null && box.col != null
      ? selectedChoices[box.row] === box.col : selectedChoices.includes(box.index)
  const left = controls?.left ?? [], right = controls?.right ?? []
  const point = (side: 'left' | 'right', index: number) =>
    (side === 'left' ? left : right).find((p) => p.index === index)
  return (
    <Box style={{ display: 'inline-block', position: 'relative', maxWidth: '100%' }}>
      {url ? <img src={url} alt="Scan de la réponse de l'élève"
        style={{ display: 'block', maxWidth: '100%', maxHeight: 430, objectFit: 'contain',
          border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4 }} />
        : <Text size="xs" c="dimmed" p="sm">Chargement du scan…</Text>}
      {url && controls?.kind === 'boxes' && (controls.boxes ?? []).map((box) => (
        <button key={`${box.row ?? '-'}:${box.col ?? box.index}`}
          type="button" aria-label={`Reprendre la coche ${box.index + 1}`}
          onClick={() => onChoice(box)} style={{ position: 'absolute', cursor: 'pointer',
            left: `${box.x * 100}%`, top: `${box.y * 100}%`,
            width: `${Math.max(box.w, .018) * 100}%`, height: `${Math.max(box.h, .025) * 100}%`,
            minWidth: 12, minHeight: 12, padding: 0,
            border: '2px solid #1971c2', background: chosen(box) ? '#1971c2' : '#fff' }} />
      ))}
      {url && controls?.kind === 'matching' && (
        <>
          <svg aria-hidden style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
            pointerEvents: 'none' }}>
            {selectedPairs.map(([li, ri], i) => {
              const a = point('left', li), b = point('right', ri)
              return a && b ? <line key={i} x1={`${a.x * 100}%`} y1={`${a.y * 100}%`}
                x2={`${b.x * 100}%`} y2={`${b.y * 100}%`} stroke="#1971c2" strokeWidth="4" /> : null
            })}
          </svg>
          {[...left.map((p) => ({ ...p, side: 'left' as const })),
            ...right.map((p) => ({ ...p, side: 'right' as const }))].map((p) => (
              <button type="button" key={`${p.side}:${p.index}`}
                aria-label={`Point ${p.side === 'left' ? 'gauche' : 'droit'} ${p.index + 1}`}
                onClick={() => onMatchPoint(p.side, p.index)} style={{ position: 'absolute',
                  left: `${p.x * 100}%`, top: `${p.y * 100}%`, transform: 'translate(-50%, -50%)',
                  width: 18, height: 18, borderRadius: '50%', cursor: 'pointer', padding: 0,
                  border: '3px solid #1971c2',
                  background: p.side === 'left' && matchStart === p.index ? '#1971c2' : '#fff' }} />
            ))}
        </>
      )}
    </Box>
  )
}

// pastille d'état de la note courante d'une réponse dans la file de correction
function ItemStatus({ it }: { it: Item }) {
  if (it.cancelled) return <Badge size="sm" variant="light" color="gray">question annulée</Badge>
  if (it.decision_source === 'teacher')
    return <Badge size="sm" variant="light" color="indigo">corrigé — {fmtPts(it.current_points)}/{fmtPts(it.bareme_points)}</Badge>
  if (it.flagged)
    return <Badge size="sm" variant="light" color="orange">à vérifier{it.category ? ` — ${CATEGORY_LABELS[it.category] ?? it.category}` : ''}</Badge>
  // note posée par le correcteur LLM (raisonnement rédigé, réponse écrite
  // longue) : distincte du déterministe, pour que le professeur sache où
  // regarder en priorité s'il veut relire.
  if (it.decision_source === 'deepseek')
    return <Badge size="sm" variant="light" color="cyan">IA — {fmtPts(it.current_points)}/{fmtPts(it.bareme_points)}</Badge>
  if (it.full_credit) return <Badge size="sm" variant="light" color="green">auto ✓ {fmtPts(it.bareme_points)}/{fmtPts(it.bareme_points)}</Badge>
  return <Badge size="sm" variant="light" color="yellow">auto — {fmtPts(it.current_points)}/{fmtPts(it.bareme_points)}</Badge>
}

export default function Corrections() {
  const [batches, setBatches] = useState<Batch[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [reviewBatch, setReviewBatch] = useState<Batch | null>(null)
  const [ocrBatch, setOcrBatch] = useState<Batch | null>(null)
  const [ocrItems, setOcrItems] = useState<Item[]>([])
  const [ocrUnits, setOcrUnits] = useState<OcrUnit[]>([])
  const [ocrIdx, setOcrIdx] = useState(0)
  const [ocrLatex, setOcrLatex] = useState('')
  const [ocrChoices, setOcrChoices] = useState<number[]>([])
  const [ocrPairs, setOcrPairs] = useState<number[][]>([])
  const [matchStart, setMatchStart] = useState<number | null>(null)
  const [savingOcr, setSavingOcr] = useState(false)
  const ocrLatexRef = useRef<HTMLDivElement>(null)
  const [scope, setScope] = useState<Scope>('flagged')
  const [validateBatch, setValidateBatch] = useState<Batch | null>(null)
  const [summary, setSummary] = useState<BatchSummary | null>(null)
  const [mathpixOk, setMathpixOk] = useState(true)
  const [llmOk, setLlmOk] = useState(true)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  // file APLATIE en unités (une case / un QCM / une réponse rédigée), regroupées
  // par réponse attendue ; + verdicts Juste(true)/Faux(false)/à trancher(null)
  // par case et par réponse : CRÉDIT 1 (juste) / 0,5 (demi-point) / 0 (faux) /
  // null (à trancher) — set_cells exige des verdicts complets à l'envoi.
  const [units, setUnits] = useState<Unit[]>([])
  const [verdicts, setVerdicts] = useState<Record<string, (number | null)[]>>({})
  const [scoreInput, setScoreInput] = useState<number | ''>('')
  const [loaded, setLoaded] = useState(false)
  const [resetTarget, setResetTarget] = useState<Batch | null>(null)
  const [resetting, setResetting] = useState(false)
  const [sandboxUploading, setSandboxUploading] = useState(false)
  const [sandboxResults, setSandboxResults] = useState<SandboxResult[]>([])
  const [shortcuts, setShortcuts] = useState<Shortcuts>(DEFAULT_SHORTCUTS)
  const { cycle, matches } = useAppState()

  // Le dépôt peut contenir des centaines de photos : seul un bilan agrégé est
  // affiché, afin que le bac à sable ne rallonge jamais toute la page.
  const sandboxSummary = useMemo(() => ({
    images: sandboxResults.filter((r) => r.file_kind === 'image'
      && r.status === 'processed' && r.pages_added > 0).length,
    pdfs: sandboxResults.filter((r) => r.file_kind === 'pdf'
      && r.status === 'processed' && r.pages_added > 0).length,
    duplicates: sandboxResults.reduce((n, r) => n + r.duplicates_rejected, 0),
    unidentifiedPages: sandboxResults.reduce((n, r) => n + r.blocked_pages, 0),
    unrecognizedFiles: sandboxResults.filter((r) =>
      r.status === 'unrecognized' || r.status === 'error').length,
  }), [sandboxResults])

  // raccourcis de correction paramétrés (Réglages → Pédagogie), repli défauts
  useEffect(() => {
    api.get<Record<string, Partial<Shortcuts>>>('/api/settings/system')
      .then((s) => setShortcuts({ ...DEFAULT_SHORTCUTS, ...(s.correction_shortcuts ?? {}) }))
      .catch(() => {})
    // sans clé Mathpix, la correction est indisponible : on prévient et on bloque
    api.get<{ mathpix_configured: boolean; correction_llm_configured: boolean }>('/api/scans/config')
      .then((c) => { setMathpixOk(c.mathpix_configured); setLlmOk(c.correction_llm_configured) })
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
        message: `${pages} page(s) conservée(s) dans l’ordre${dups ? `, ${dups} doublon(s) détecté(s)` : ''}`,
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
    // Une réponse SIGNALÉE doit être validée explicitement : ses crédits auto
    // restent affichés comme information (`auto_credit`) mais ne pré-cochent pas
    // les choix du professeur. Sinon toutes les cases semblaient déjà résolues
    // et la modale annonçait « rien à vérifier » dès son ouverture.
    const vmap: Record<string, (number | null)[]> = {}
    for (const it of rs) {
      if (it.grade_mode !== 'cells') continue
      vmap[it.response_id] = it.cells.map((c, ci) => (
        c.teacher_credit != null ? c.teacher_credit
          : cellNeedsTeacher(it, ci) ? null : c.auto_credit
      ))
    }
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

  const loadOcrItems = useCallback(async (b: Batch, desired = 0) => {
    const rs = await api.get<Item[]>(`/api/scans/batches/${b.id}/ocr-items`)
    const nextUnits = buildOcrUnits(rs, b.ocr_threshold || 0.9)
    setOcrItems(rs); setOcrUnits(nextUnits)
    setOcrIdx(Math.max(0, Math.min(desired, nextUnits.length - 1)))
    return nextUnits.length
  }, [])

  async function openOcr(b: Batch) {
    setOcrBatch(b)
    try {
      await loadOcrItems(b)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      setOcrBatch(null)
    }
  }

  function closeOcr() {
    setOcrBatch(null); setOcrItems([]); setOcrUnits([]); setOcrIdx(0); refresh()
  }

  const currentOcrUnit = ocrUnits[ocrIdx]
  const currentOcrItem = currentOcrUnit
    ? ocrItems.find((x) => x.response_id === currentOcrUnit.respId) ?? null : null
  const currentOcrCell = currentOcrItem && currentOcrUnit?.cellIndex != null
    ? currentOcrItem.cells[currentOcrUnit.cellIndex] ?? null : null

  useEffect(() => {
    if (!currentOcrItem) return
    const text = currentOcrCell?.ocr_text ?? currentOcrItem.ocr_text ?? ''
    const latex = currentOcrCell?.ocr_latex || currentOcrItem.ocr_latex || text
    setOcrLatex(latex)
    setOcrChoices([...(currentOcrItem.selected_choices ?? [])])
    setOcrPairs((currentOcrItem.selected_pairs ?? []).map((p) => [...p]))
    setMatchStart(null)
    window.setTimeout(() => ocrLatexRef.current?.focus(), 0)
  }, [currentOcrItem?.response_id, currentOcrUnit?.cellIndex])

  async function finishOcr(b: Batch) {
    await api.post(`/api/scans/batches/${b.id}/ocr/complete`)
    notifications.show({ color: 'blue', message: 'Lecture validée — correction automatique démarrée' })
    closeOcr()
  }

  async function saveCurrentOcr(direction: -1 | 1 = 1) {
    const b = ocrBatch, unit = currentOcrUnit, item = currentOcrItem
    if (!b || !unit || !item || savingOcr) return
    setSavingOcr(true)
    try {
      const editedLatex = ocrLatexRef.current?.innerText ?? ocrLatex
      const body = item.response_type.startsWith('qcm') || item.response_type === 'checkbox_grid'
        ? { selected_choices: ocrChoices }
        : item.response_type === 'matching'
          ? { selected_pairs: ocrPairs }
          : { text: editedLatex, latex: editedLatex, cell_index: unit.cellIndex }
      const r = await api.post<{ remaining: number }>(`/api/scans/responses/${unit.respId}/ocr`, body)
      if (r.remaining === 0) {
        await finishOcr(b)
        return
      }
      // L'unité validée disparaît de la file. À droite, sa remplaçante prend le
      // même index ; à gauche, on revient sur l'index précédent.
      await loadOcrItems(b, direction > 0 ? ocrIdx : ocrIdx - 1)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSavingOcr(false)
    }
  }

  function toggleOcrChoice(box: { index: number; row?: number | null; col?: number | null }) {
    if (currentOcrItem?.response_type === 'checkbox_grid' && box.row != null && box.col != null) {
      setOcrChoices((prev) => {
        const next = [...prev]
        while (next.length <= box.row!) next.push(-1)
        next[box.row!] = next[box.row!] === box.col ? -1 : box.col!
        return next
      })
    } else {
      setOcrChoices((prev) => prev.includes(box.index)
        ? prev.filter((x) => x !== box.index) : [...prev, box.index].sort((a, c) => a - c))
    }
  }

  function toggleMatchPoint(side: 'left' | 'right', index: number) {
    if (side === 'left') { setMatchStart((v) => v === index ? null : index); return }
    if (matchStart == null) return
    setOcrPairs((prev) => {
      const exists = prev.some(([l, r]) => l === matchStart && r === index)
      return exists ? prev.filter(([l, r]) => l !== matchStart || r !== index)
        : [...prev.filter(([l, r]) => l !== matchStart && r !== index), [matchStart, index]]
    })
    setMatchStart(null)
  }

  useEffect(() => {
    if (!ocrBatch || !currentOcrItem) return
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'TEXTAREA'
          || (e.target as HTMLElement).isContentEditable) return
      if (e.key === 'Enter' || e.key === 'ArrowRight') {
        e.preventDefault(); saveCurrentOcr(1)
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault(); saveCurrentOcr(-1)
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

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
            current_points: Math.round(r * x.bareme_points * 1000) / 1000 }
    )))
    setScoreInput('')
    advance()
  }
  const gradeRatio = (ratio: number) => gradeBlock('set_ratio', { ratio })

  // enregistre une réponse à cases dès que TOUTES ses cases sont tranchées
  // (set_cells exige des verdicts complets) : le backend recalcule le barème
  // (points = nombre de cases justes) et rend l'overlay cohérent avec la note.
  async function submitCellsFor(rid: string, arr: (number | null)[]) {
    const credits = arr.map((v) => Math.max(0, Math.min(1, v ?? 0)))
    try {
      await api.post(`/api/scans/responses/${rid}/resolve`,
        { action: 'set_cells', cell_verdicts: credits })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      return
    }
    // le score d'une réponse à cases est la SOMME des crédits (un demi-point
    // compte pour 0,5), rapportée au barème de l'exercice — même règle qu'au
    // serveur (scans.resolve, action set_cells).
    const earned = credits.reduce((a, b) => a + b, 0)
    setItems((prev) => prev.map((x) => x.response_id !== rid ? x : ({
      ...x, decision_source: 'teacher', cancelled: false,
      full_credit: earned === credits.length,
      current_points: credits.length
        ? Math.round((earned / credits.length) * x.bareme_points * 1000) / 1000 : 0,
      cells: x.cells.map((c, ci) => ({ ...c, teacher_credit: credits[ci] })),
    })))
  }

  // pose le verdict d'UNE case (unité courante) et avance ; dès que la réponse
  // parente n'a plus aucune case en attente, elle est enregistrée automatiquement.
  function markCellUnit(val: number) {
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
        if (k === shortcuts.full) markCellUnit(1)
        else if (k === shortcuts.two_thirds) markCellUnit(0.5)   // demi-point
        else if (k === shortcuts.zero) markCellUnit(0)
        return
      }
      if (u.mode === 'binary') {
        if (k === shortcuts.full) gradeRatio(1)
        else if (k === shortcuts.zero) gradeRatio(0)
        return
      }
      // Un matching se note par nombre entier de liaisons justes. Les
      // raccourcis 1/3 et 2/3 d'une réponse rédigée seraient faux pour 2, 4,
      // 5 ou 6 liens ; seuls tout juste / tout faux restent non ambigus.
      const current = items.find((it) => it.response_id === u.respId)
      if (current?.response_type === 'matching') {
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

  async function setDistributed(b: Batch, value: boolean) {
    await api.patch(`/api/scans/batches/${b.id}`, { overlay_distributed: value })
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
  // Un verdict LLM peut porter sur plusieurs cellules d'une même réponse. Dans
  // l'assistant atomique, ne montrer que celui de l'unité courante.
  const currentLlmNotes = !curItem ? [] : cur?.mode !== 'cells'
    ? (curItem.llm_notes ?? [])
    : (curItem.llm_notes ?? []).filter((n) => n.cell_index != null
      ? n.cell_index === cur?.cellIndex
      : !!curCell && n.champ === curCell.label)
  const compactDeterministic = !!curItem && (
    curItem.response_type.startsWith('qcm') || curItem.response_type === 'matching')
  const cellVal = cur && cur.mode === 'cells' && cur.cellIndex != null
    ? verdicts[cur.respId]?.[cur.cellIndex] ?? null : null
  // Un bouton foncé représente exclusivement la note EFFECTIVEMENT portée par
  // la réponse courante. Auparavant « Juste » (et le maximum en matching) était
  // toujours `filled`, ce qui créait une sélection fantôme sans rapport avec le
  // score. current_points est arrondi au millième par l'API, d'où la tolérance.
  const scoreMatchesRatio = (ratio: number) => !!curItem && !curItem.cancelled
    && curItem.bareme_points > 0
    && Math.abs(curItem.current_points - curItem.bareme_points * ratio) <= 0.0011
  // position dans le groupe des réponses attendues IDENTIQUES (cases enchaînées)
  const sameGroup = cur ? units.filter((u) => u.mode === cur.mode && u.expectedKey === cur.expectedKey) : []
  const samePos = cur ? sameGroup.findIndex((u) => u.key === cur.key) + 1 : 0

  // La navigation est par unité/cellule, mais le badge métier compte des
  // RÉPONSES individuelles, comme pending_reviews côté backend. Une réponse
  // tableau de 5 cellules ne doit ni apparaître comme 5 réponses, ni disparaître.
  const attentionUnits = units.filter((u) => u.attention)
  const remaining = new Set(items
    .filter((it) => it.flagged && it.decision_source !== 'teacher')
    .map((it) => it.response_id)).size
  const ocrSameGroup = currentOcrItem ? ocrUnits.filter((u) => {
    const it = ocrItems.find((x) => x.response_id === u.respId)
    return it?.group_key === currentOcrItem.group_key && u.cellIndex === currentOcrUnit?.cellIndex
  }) : []
  const ocrSamePos = currentOcrUnit
    ? ocrSameGroup.findIndex((u) => u.key === currentOcrUnit.key) + 1 : 0

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

      {mathpixOk && !llmOk && (
        <Alert color="yellow" variant="light" icon={<AlertTriangle size={18} />}
          title="Correcteur des réponses rédigées non configuré">
          Sans clé DeepSeek, les raisonnements rédigés et les réponses écrites
          longues ne sont pas notés automatiquement : ils arrivent dans votre
          file de correction manuelle (jamais de note simulée). Le reste du
          sujet — QCM, grilles, tableaux, réponses courtes — est corrigé
          normalement. Clé à ajouter dans <b>Paramètres → API</b>.
        </Alert>
      )}

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
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <Group gap="xs" wrap="nowrap" align="flex-start"
            style={{ flex: '1 1 420px', minWidth: 240 }}>
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
                  loading={sandboxUploading} disabled={!mathpixOk}
                  style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
                  Déposer en vrac
                </Button>
              )}
            </FileButton>
          </Tooltip>
        </Group>
        {sandboxResults.length > 0 && (
          <Group gap="xs" mt="sm" wrap="wrap">
            {sandboxSummary.images > 0 && (
              <Badge size="sm" variant="light" color="green">
                {sandboxSummary.images} image{sandboxSummary.images > 1 ? 's' : ''}{' '}
                identifiée{sandboxSummary.images > 1 ? 's' : ''}
              </Badge>
            )}
            {sandboxSummary.pdfs > 0 && (
              <Badge size="sm" variant="light" color="green">
                {sandboxSummary.pdfs} PDF identifié{sandboxSummary.pdfs > 1 ? 's' : ''}
              </Badge>
            )}
            {sandboxSummary.duplicates > 0 && (
              <Badge size="sm" variant="light" color="gray">
                {sandboxSummary.duplicates} doublon{sandboxSummary.duplicates > 1 ? 's' : ''}{' '}
                rejeté{sandboxSummary.duplicates > 1 ? 's' : ''}
              </Badge>
            )}
            {sandboxSummary.unidentifiedPages > 0 && (
              <Badge size="sm" variant="light" color="orange">
                {sandboxSummary.unidentifiedPages} page{sandboxSummary.unidentifiedPages > 1 ? 's' : ''}{' '}
                non identifiée{sandboxSummary.unidentifiedPages > 1 ? 's' : ''}
              </Badge>
            )}
            {sandboxSummary.unrecognizedFiles > 0 && (
              <Badge size="sm" variant="light" color="red">
                {sandboxSummary.unrecognizedFiles} fichier{sandboxSummary.unrecognizedFiles > 1 ? 's' : ''}{' '}
                non reconnu{sandboxSummary.unrecognizedFiles > 1 ? 's' : ''}
              </Badge>
            )}
          </Group>
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
              const done = stage === 'done' && b.overlay_distributed
              const badge = done ? { label: 'terminé', color: 'gray' } : STAGE_BADGE[stage]
              return (
                <Card key={b.id} withBorder padding="md" style={done ? {
                  opacity: 0.55, background: 'var(--mantine-color-gray-1)',
                } : undefined}>
                  <Group justify="space-between" wrap="nowrap" align="flex-start">
                    <Stack gap={6} style={{ minWidth: 0, flex: 1 }}>
                      <Group gap="xs" wrap="nowrap">
                        <Badge variant="light" size="sm"
                          color={b.assessment_type === 'control' ? 'red' : 'blue'}>
                          {b.assessment_type === 'control' ? 'Contrôle' : 'Entraînement'}
                        </Badge>
                        {b.note_base && (
                          <Tooltip label={`${b.assessment_type === 'control' ? 'Noté' : 'Scoré'} sur ${b.note_base} points`}>
                            <Badge size="sm" variant="outline"
                              color={b.assessment_type === 'control' ? 'red' : 'gray'}>
                              /{b.note_base}
                            </Badge>
                          </Tooltip>
                        )}
                        <Text fw={600} lineClamp={1}>{b.assessment_title}</Text>
                        <Badge size="sm" variant="dot" color={badge.color}>
                          {badge.label}{stage === 'review' && b.pending_reviews ? ` (${b.pending_reviews})` : ''}
                          {stage === 'ocr_review' && b.pending_ocr ? ` (${b.pending_ocr})` : ''}
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
                          <Checkbox size="xs" label="Distribué aux élèves"
                            disabled={!overlayReady || !b.overlay_printed}
                            checked={b.overlay_distributed}
                            onChange={(e) => setDistributed(b, e.target.checked)} />
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

                      {stage === 'ocr_review' && (
                        <>
                          <Tooltip label={`Reprendre seulement les lectures sous ${(b.ocr_threshold * 100).toFixed(0)} %, avant toute correction`}>
                            <Button size="xs" color="blue" leftSection={<ScanLine size={14} />}
                              onClick={() => openOcr(b)}>
                              OCRiser ({b.pending_ocr})
                            </Button>
                          </Tooltip>
                          <Tooltip label="Effacer cette lecture et re-scanner depuis zéro">
                            <ActionIcon variant="subtle" color="red" size="lg"
                              onClick={() => setResetTarget(b)}><Trash2 size={16} /></ActionIcon>
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
                          {b.pending_llm > 0 && (
                            <Tooltip label="Retenter uniquement les verdicts DeepSeek indisponibles, sans refaire l’OCR">
                              <Button size="xs" variant="light" color="blue"
                                leftSection={<RefreshCw size={14} />} onClick={() => retry(b)}>
                                Relancer l’IA ({b.pending_llm})
                              </Button>
                            </Tooltip>
                          )}
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
                          {overlayReady && (
                            <PrintButton assessmentId={b.assessment_id}
                              file="correction_overlay.pdf" label="Imprimer l'overlay" />
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
              {summary.note_base ? ` · score ramené sur ${summary.note_base}` : ''}.
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

      <Modal opened={!!ocrBatch} onClose={closeOcr} size="xl"
        title={<Group gap="xs"><Text fw={650} c="blue.7">OCRiser</Text>
          <Text fw={500}>— {ocrBatch?.assessment_title}</Text></Group>}>
        <Stack>
          <Group justify="space-between" wrap="nowrap">
            <Group gap="xs">
              <Badge color="blue" variant="filled">{currentOcrItem?.group_label ?? 'Lecture'}</Badge>
              {currentOcrItem?.ocr_confidence != null && (
                <Badge color="blue" variant="light">
                  confiance {(currentOcrItem.ocr_confidence * 100).toFixed(0)} %
                </Badge>
              )}
              {ocrSameGroup.length > 1 && (
                <Badge color="cyan" variant="light">
                  {ocrSamePos}/{ocrSameGroup.length} même case de réponse
                </Badge>
              )}
            </Group>
            <Group gap={6} wrap="nowrap">
              <ActionIcon variant="light" disabled={ocrIdx <= 0 || savingOcr}
                onClick={() => saveCurrentOcr(-1)}><ChevronLeft size={16} /></ActionIcon>
              <Text size="xs" c="dimmed">{ocrUnits.length ? `${ocrIdx + 1} / ${ocrUnits.length}` : '—'}</Text>
              <ActionIcon variant="light" disabled={!ocrUnits.length || savingOcr}
                onClick={() => saveCurrentOcr(1)}><ChevronRight size={16} /></ActionIcon>
            </Group>
          </Group>

          {currentOcrItem && currentOcrUnit ? (
            <>
              <Group justify="space-between">
                <Text size="sm" fw={600}>{currentOcrItem.student}</Text>
                <Text size="xs" c="blue.7" fw={600}>
                  Reprise de la réponse élève — aucune note n'est attribuée ici
                </Text>
              </Group>
              <Card withBorder padding="sm">
                <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb="xs">
                  Scan de l'élève{currentOcrCell?.label ? ` — ${currentOcrCell.label}` : ''}
                </Text>
                <OcrScan item={currentOcrItem} cellIndex={currentOcrUnit.cellIndex}
                  selectedChoices={ocrChoices} selectedPairs={ocrPairs} matchStart={matchStart}
                  onChoice={toggleOcrChoice} onMatchPoint={toggleMatchPoint} />
                {(currentOcrItem.response_type.startsWith('qcm')
                  || currentOcrItem.response_type === 'checkbox_grid') && (
                  <Text size="xs" c="blue.7" mt="xs">
                    Cliquez les carrés bleus directement sur le scan : plein = coché, vide = non coché.
                  </Text>
                )}
                {currentOcrItem.response_type === 'matching' && (
                  <Text size="xs" c="blue.7" mt="xs">
                    Cliquez un point bleu à gauche puis son correspondant à droite. Recliquez la liaison pour la retirer.
                  </Text>
                )}
              </Card>

              {!currentOcrItem.response_type.startsWith('qcm')
                && currentOcrItem.response_type !== 'checkbox_grid'
                && currentOcrItem.response_type !== 'matching' && (
                  <Card withBorder padding="sm" style={{ borderColor: 'var(--mantine-color-blue-3)' }}>
                    <Text size="xs" c="blue.7" fw={600} tt="uppercase" mb={6}>
                      Aperçu LaTeX — modifiable
                    </Text>
                    <Text size="xs" c="dimmed" mb={6}>
                      Entrée valide · ←/→ valide et navigue · Maj+Entrée ajoute une ligne au raisonnement
                    </Text>
                    <Box ref={ocrLatexRef} contentEditable suppressContentEditableWarning role="textbox"
                      tabIndex={0}
                      aria-label="Modifier le LaTeX" key={currentOcrUnit.key}
                      onInput={(e) => {
                        const value = e.currentTarget.innerText ?? ''
                        setOcrLatex(value)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'ArrowRight') { e.preventDefault(); saveCurrentOcr(1) }
                        else if (e.key === 'ArrowLeft') { e.preventDefault(); saveCurrentOcr(-1) }
                        else if (e.key === 'Enter'
                          && !(e.shiftKey && (currentOcrItem.response_type === 'multiline_text'
                            || currentOcrItem.response_type === 'composite'))) {
                          e.preventDefault(); saveCurrentOcr(1)
                        }
                      }} style={{ minHeight: 34, padding: 7, color: 'var(--mantine-color-blue-8)',
                        border: '1px solid var(--mantine-color-blue-3)', borderRadius: 4,
                        fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>{ocrLatex}</Box>
                    <Divider my="sm" />
                    <Box fz="1.55rem" c="blue.8" style={{ minHeight: 38 }}>
                      <MathAnswer text={ocrLatex} fallback="Aucune réponse détectée" />
                    </Box>
                  </Card>
              )}

              <Group justify="flex-end">
                <Button color="blue" loading={savingOcr} onClick={() => saveCurrentOcr(1)}>
                  Valider la lecture et continuer <Kbd ml={8}>Entrée</Kbd>
                </Button>
              </Group>
            </>
          ) : (
            <Text c="dimmed" py="lg">Aucune lecture sous le seuil de confiance.</Text>
          )}
        </Stack>
      </Modal>

      <Modal opened={!!reviewBatch} onClose={closeCorrection} size="xl"
        title={<Text fw={650}>Correction — {reviewBatch?.assessment_title}</Text>}>
        <Stack>
          <Group justify="space-between" wrap="wrap">
            <SegmentedControl size="xs" value={scope}
              onChange={(v) => changeScope(v as Scope)}
              data={[
                { label: `À vérifier${remaining ? ` (${remaining})` : ''}`, value: 'flagged' },
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

              {compactDeterministic ? (
                /* QCM / matching : une seule vue suffit. Le rouge attendu est
                   dessiné directement sur le crop, par-dessus la réponse élève. */
                <Card withBorder padding="sm">
                  <Group justify="space-between" gap="xs" mb="xs">
                    <Text size="xs" c="dimmed" fw={600} tt="uppercase">
                      Réponse de l'élève + correction attendue
                    </Text>
                    {curItem.ocr_confidence != null && (
                      <Badge size="xs" variant="light"
                        color={curItem.ocr_confidence >= 0.9 ? 'green' : 'orange'}>
                        confiance {(curItem.ocr_confidence * 100).toFixed(0)} %
                      </Badge>
                    )}
                  </Group>
                  <ScanImage responseId={cur.respId} expectedOverlay large />
                  <Group gap="lg" mt="xs">
                    <Group gap={6} wrap="nowrap">
                      <Box style={{ width: 13, height: 13, flexShrink: 0,
                        background: 'var(--mantine-color-red-7)' }} />
                      <Text size="xs" c="dimmed">
                        {curItem.response_type.startsWith('qcm')
                          ? 'plein = choix attendu · vide = choix non attendu'
                          : 'rouge = liaisons attendues'}
                      </Text>
                    </Group>
                    <Text size="xs" c="dimmed">noir / bleu = réponse de l'élève</Text>
                  </Group>
                  {curItem.reason_code && (
                    <Text size="xs" c="dimmed" mt={6}>Motif : {curItem.reason_code}</Text>
                  )}
                </Card>
              ) : (
              /* À gauche : le scan de l'élève et l'avis LLM de CETTE unité.
                 La transcription OCR reste volontairement masquée ici pour ne
                 pas être confondue avec l'attendu, qui est seul à droite. */
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
                <Card withBorder padding="sm">
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>
                    Réponse de l'élève{cur.mode === 'cells' && curCell?.label ? ` — ${curCell.label}` : ''}
                  </Text>
                  <ScanImage responseId={cur.respId}
                    cellIndex={cur.mode === 'cells' ? cur.cellIndex : null} />
                  {cur.mode !== 'cells' && curItem.reason_code && (
                    <Text size="xs" c="dimmed" mt={6}>Motif : {curItem.reason_code}</Text>
                  )}
                  {curItem.flagged && curItem.reason_code === 'llm_low_confidence' && (
                    <Text size="xs" c="orange.8" mt={6} fw={600}>
                      Seuil LLM {(curItem.llm_threshold * 100).toFixed(0)} %
                      {curItem.llm_min_confidence != null
                        ? ` · confiance minimale de la réponse ${(curItem.llm_min_confidence * 100).toFixed(0)} %`
                        : ''}
                      {' · seules les cases sous le seuil sont à vérifier'}
                    </Text>
                  )}
                  {curItem.flagged && !curItem.reason_code.startsWith('llm_')
                    && (curItem.llm_notes ?? []).length > 0 && (
                    <Text size="xs" c="orange.8" mt={6} fw={600}>
                      Cette revue n'est pas déclenchée par la confiance LLM,
                      mais par le contrôle « {curItem.reason_code} ».
                    </Text>
                  )}
                  {currentLlmNotes.length > 0 && (
                    <Stack gap={2} mt="sm">
                      <Text size="xs" c="dimmed" fw={600} tt="uppercase">Avis IA</Text>
                      {currentLlmNotes.map((n, i) => {
                        const color = llmVerdictColor(n.verdict)
                        return (
                          <Text key={i} size="xs" c={`${color}.7`} fw={650}>
                            {n.verdict} ({fmtPts(n.points)}/{fmtPts(n.bareme)})
                            {n.verdict !== 'indisponible' && n.confidence != null
                              ? ` · confiance ${(n.confidence * 100).toFixed(0)} %` : ''}
                            {n.requires_review && n.confidence != null
                              ? ` · sous le seuil ${(curItem.llm_threshold * 100).toFixed(0)} %` : ''}
                            {n.motif ? ` · ${n.motif}` : ''}
                          </Text>
                        )
                      })}
                    </Stack>
                  )}
                </Card>
                <Card withBorder padding="sm">
                  <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>
                    Réponse attendue{curCell?.label ? ` — ${curCell.label}` : ''}
                  </Text>
                  <Box fz="1.7rem" fw={700} style={{ lineHeight: 1.3 }}>
                    <MathAnswer text={cur.mode === 'cells' ? curCell?.expected_display
                      : curItem.expected_display} />
                  </Box>
                </Card>
              </SimpleGrid>
              )}

              {/* actions — ordre gauche→droite : Faux … Juste (§ demande) */}
              {cur.mode === 'cells' ? (
                <Group>
                  <Button color="red" variant={cellVal === 0 ? 'filled' : 'light'}
                    onClick={() => markCellUnit(0)}>
                    Faux <Kbd ml={6}>{shortcuts.zero.toUpperCase()}</Kbd>
                  </Button>
                  {/* demi-point : arrondi correct, erreur très légère — la case
                      vaut alors la moitié de ses points (cf. grading.numeric_credit,
                      qui le propose déjà tout seul sur un arrondi juste). */}
                  <Button color="orange" variant={cellVal === 0.5 ? 'filled' : 'light'}
                    onClick={() => markCellUnit(0.5)}>
                    ½ point <Kbd ml={6}>{shortcuts.two_thirds.toUpperCase()}</Kbd>
                  </Button>
                  <Button color="green" variant={cellVal === 1 ? 'filled' : 'light'}
                    onClick={() => markCellUnit(1)}>
                    Juste <Kbd ml={6}>{shortcuts.full.toUpperCase()}</Kbd>
                  </Button>
                </Group>
              ) : cur.mode === 'binary' ? (
                <Group>
                  <Button color="red" variant={scoreMatchesRatio(0) ? 'filled' : 'light'}
                    aria-pressed={scoreMatchesRatio(0)} onClick={() => gradeRatio(0)}>
                    Faux — 0 point <Kbd ml={6}>{shortcuts.zero.toUpperCase()}</Kbd>
                  </Button>
                  <Button color="green" variant={scoreMatchesRatio(1) ? 'filled' : 'light'}
                    aria-pressed={scoreMatchesRatio(1)} onClick={() => gradeRatio(1)}>
                    Juste — {fmtPts(curItem.bareme_points)} <Kbd ml={6}>{shortcuts.full.toUpperCase()}</Kbd>
                  </Button>
                  <Button variant={curItem.cancelled ? 'filled' : 'subtle'} color="gray"
                    aria-pressed={curItem.cancelled} onClick={() => gradeBlock('cancel_item')}>
                    Annuler la question
                  </Button>
                </Group>
              ) : curItem.response_type === 'matching' ? (
                <>
                  {(() => {
                    const expectedPairs = ((curItem.expected as { pairs?: number[][] })
                      ?.pairs) || []
                    const total = Math.max(1, expectedPairs.length)
                    return (
                      <Group gap="xs">
                        {Array.from({ length: total + 1 }, (_, correct) => (
                          <Button key={correct}
                            color={correct === 0 ? 'red' : correct === total ? 'green' : 'teal'}
                            variant={scoreMatchesRatio(correct / total) ? 'filled' : 'light'}
                            aria-pressed={scoreMatchesRatio(correct / total)}
                            onClick={() => gradeRatio(correct / total)}>
                            {correct === 0 ? '0 liaison juste — 0'
                              : `${correct}/${total} liaison${correct > 1 ? 's' : ''} — `
                                + fmtPts(curItem.bareme_points * correct / total)}
                          </Button>
                        ))}
                      </Group>
                    )
                  })()}
                  <Text size="xs" c="dimmed">
                    Chaque liaison vaut la même part du barème :{' '}
                    {fmtPts(curItem.bareme_points / Math.max(1,
                      ((curItem.expected as { pairs?: number[][] })?.pairs || []).length))} point(s).
                  </Text>
                  <Button w="fit-content" variant={curItem.cancelled ? 'filled' : 'subtle'}
                    color="gray" aria-pressed={curItem.cancelled}
                    onClick={() => gradeBlock('cancel_item')}>
                    Annuler la question
                  </Button>
                </>
              ) : (
                <>
                  <Group>
                    <Button color="red" variant={scoreMatchesRatio(0) ? 'filled' : 'light'}
                      aria-pressed={scoreMatchesRatio(0)} onClick={() => gradeRatio(0)}>
                      Faux — 0 <Kbd ml={6}>{shortcuts.zero.toUpperCase()}</Kbd>
                    </Button>
                    <Button color="orange" variant={scoreMatchesRatio(1 / 3) ? 'filled' : 'light'}
                      aria-pressed={scoreMatchesRatio(1 / 3)} onClick={() => gradeRatio(1 / 3)}>
                      1⁄3 — {fmtPts(curItem.bareme_points / 3)} <Kbd ml={6}>{shortcuts.one_third.toUpperCase()}</Kbd>
                    </Button>
                    <Button color="teal" variant={scoreMatchesRatio(2 / 3) ? 'filled' : 'light'}
                      aria-pressed={scoreMatchesRatio(2 / 3)} onClick={() => gradeRatio(2 / 3)}>
                      2⁄3 — {fmtPts(curItem.bareme_points * 2 / 3)} <Kbd ml={6}>{shortcuts.two_thirds.toUpperCase()}</Kbd>
                    </Button>
                    <Button color="green" variant={scoreMatchesRatio(1) ? 'filled' : 'light'}
                      aria-pressed={scoreMatchesRatio(1)} onClick={() => gradeRatio(1)}>
                      Juste — {fmtPts(curItem.bareme_points)} <Kbd ml={6}>{shortcuts.full.toUpperCase()}</Kbd>
                    </Button>
                  </Group>
                  <Group>
                    <NumberInput placeholder="points" w={120} min={0} max={curItem.bareme_points} step={0.125}
                      decimalScale={3} value={scoreInput}
                      onChange={(v) => setScoreInput(v === '' ? '' : Number(v))} />
                    <Button variant="light" disabled={scoreInput === '' || !curItem.bareme_points}
                      onClick={() => gradeRatio(Number(scoreInput) / curItem.bareme_points)}>
                      Attribuer ces points
                    </Button>
                    <Button variant={curItem.cancelled ? 'filled' : 'subtle'} color="gray"
                      aria-pressed={curItem.cancelled} onClick={() => gradeBlock('cancel_item')}>
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
