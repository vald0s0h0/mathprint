// Écran Correction (§9.5) : lots de scans en cartes groupées par classe.
// Chaque carte affiche l'ÉTAPE courante de la pipeline et le BOUTON D'ACTION
// logique qui indique clairement au professeur la prochaine chose à faire
// (déposer / corriger / valider / imprimer), plus un bouton de déblocage
// (relancer) quand la correction est bloquée. Le dépôt d'un scan ne demande pas
// de choisir l'évaluation : le code signé de chaque page identifie le sujet.
import {
  ActionIcon, Alert, Badge, Box, Button, Card, Checkbox, Divider, FileButton, Group, Kbd,
  Loader, Menu, Modal, NumberInput, SegmentedControl, SimpleGrid, Stack, Table, Text,
  Title, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, ChevronLeft, ChevronRight, Eye, Inbox, PencilLine, RefreshCw, ScanLine,
  Trash2, Upload,
} from 'lucide-react'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, getToken } from '../api'
import ActionsMenu from '../components/ActionsMenu'
import MathText, { MathAnswer } from '../components/MathText'
import { type Segment, stepWidths } from '../components/SegmentBar'
import { type LateJob, lateJobNames } from '../components/LateJobs'
import ScanLotRow, { type Lot, type LotHandlers, type LotStage } from '../components/ScanLotRow'
import PrintButton from '../components/PrintButton'
import OcrAssistant, { type OcrBatchRef } from '../components/OcrAssistant'
import RefineCorrectionModal from '../components/RefineCorrectionModal'
import { useAppState } from '../state/AppState'

type Batch = {
  id: string; assessment_id: string; status: string; page_count: number
  assessment_title: string; assessment_type: string
  // base de scoring ; les entraînements ne l'impriment pas
  note_base: number | null
  class_name: string; class_id: string | null; grade_level: string
  overlay_printed: boolean; overlay_distributed: boolean
  // corrections affinées pas encore reportées sur l'overlay imprimable
  // (« Affiner la correction », backend services.refine), et sa régénération
  overlay_stale: boolean; overlay_rebuilding: boolean
  error: string | null; pending_reviews: number; pending_ocr: number; pending_llm: number
  // lecture locale sur le Mac (mode cascade) : découpes lues / total, worker joignable ?
  ocr_local?: { decided: number; total: number; worker_online: boolean; waiting_hours?: number; timeout_hours?: number; overdue?: boolean } | null
  // élèves jamais revenus à la finalisation (jamais pénalisés, cf. services
  // .pipeline.finalize_batch) — le prof doit savoir combien manquent sur ce sujet
  absent_count: number
  ocr_threshold: number
  segments: Segment[]; created_at: string
  // Enchaînements déposés après la validation : un job par dépôt (services.late_copies)
  late_jobs?: LateJob[]; first_wave_status?: string
  // file de correction restreinte à un lot tardif (posé à l'ouverture des modales)
  scopeWave?: number
  // élèves du lot de scan principal
  students?: string[]
  // sujet imprimé recto-verso
  duplex?: boolean
}
// une case à corriger d'un tableau / de cases à trous (table_fill, multi_blank) :
// sa réponse attendue lisible, ce que l'OCR a lu, le crédit calculé par le moteur
// (auto_credit) et celui éventuellement déjà posé par le professeur (teacher_credit).
// candidat de lecture proposé par les moteurs locaux (OCR multicouche)
type OcrCandidate = { surface: string; engines: string[]; voices: number }
type Cell = {
  index: number; label: string; expected_display: string
  ocr_candidates?: OcrCandidate[]
  // crédit 1 (juste) / 0.5 (demi-point) / 0 (faux) ; null = non tranché
  ocr_text: string; ocr_latex: string
  auto_credit: number | null; teacher_credit: number | null
}
// mode de correction manuelle piloté par le backend (cf. scans._grade_mode)
type GradeMode = 'cells' | 'binary' | 'partial'
// une réponse d'élève à (re)corriger : signalée par le moteur (flagged) ou
// simplement relue par le professeur. Clé de résolution = response_id.
type Item = {
  // lecture multicouche : élément tiré pour le contrôle (« Contrôle »), candidats des moteurs
  ocr_audit?: boolean; ocr_candidates?: OcrCandidate[]
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
  // géométrie à tracer : lecture CV en mode ombre, proposée mais jamais appliquée
  construction?: {
    status: string | null; fraction: number | null; confidence: number | null
    reasons: string[]; error_id: string | null; error_feedback: string | null
    elements: { name: string; state: string; length_mm?: number | null }[]
  } | null
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
// raccourcis de correction manuelle (paramétrables, cf. Réglages → Pédagogie)
type Shortcuts = { full: string; two_thirds: string; one_third: string; zero: string }
const DEFAULT_SHORTCUTS: Shortcuts = { full: 'f', two_thirds: 'd', one_third: 's', zero: 'q' }
type SandboxResult = {
  filename: string; file_kind: 'image' | 'pdf' | 'unknown'; status: string; pages_added: number
  duplicates_rejected: number; blocked_pages: number; batches_created: string[]
  late_pages?: number; conflicts?: number
}
type Scope = 'flagged' | 'all'

const CATEGORY_LABELS: Record<string, string> = {
  rature: 'Rature', double_coche: 'Double coche', ocr_ambigu: 'OCR ambigu',
  scan_faible: 'Scan faible', bareme: 'Barème',
  trace_dessin: 'Tracé / dessin', points_a_relier: 'Points à relier',
}

// Le lot principal ne montre plus les copies tardives (wave=0) dès qu'il y en a ;
// un lot tardif ne montre que les siennes (wave=N).
function waveQuery(b: Batch): string {
  const wave = b.scopeWave ?? (b.late_jobs?.length ? 0 : undefined)
  return wave === undefined ? '' : `&wave=${wave}`
}

// Étape « métier » d'un lot, dérivée de son statut technique — c'est elle qui
// pilote le libellé de la carte et l'action proposée au professeur.
type Stage = 'awaiting' | 'processing' | 'error' | 'ocr_review' | 'review' | 'done'
function stageOf(b: Batch): Stage {
  if (b.status === 'awaiting_scan') return 'awaiting'
  if (b.error) return 'error'
  if (b.status === 'finalized' || b.status === 'overlay_ready') return 'done'
  if (b.status === 'ocr_review_pending' || b.pending_ocr > 0) return 'ocr_review'
  if (b.status === 'graded' || b.status === 'review_pending') {
    // dès la dernière revue tranchée, le serveur enchaîne seul sur la
    // finalisation et les copies corrigées (halte « Valider » supprimée,
    // 02/09) — un lot qui reste ici sans revue en attente est en train d'y
    // passer (ou un lot ancien pas encore rafraîchi) : traité comme en cours.
    return b.pending_reviews > 0 ? 'review' : 'processing'
  }
  return 'processing'  // uploaded → split → … → ocr_complete
}
const STAGE_BADGE: Record<Stage, { label: string; color: string }> = {
  awaiting: { label: 'en attente de scan', color: 'gray' },
  processing: { label: 'correction en cours', color: 'blue' },
  error: { label: 'bloqué', color: 'red' },
  ocr_review: { label: 'lecture à vérifier', color: 'blue' },
  review: { label: 'à corriger', color: 'orange' },
  done: { label: 'prêt à imprimer', color: 'green' },
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

// Lecture CV d'un tracé. Sa note s'applique au-dessus du seuil réglé ; en dessous,
// ou quand la copie est tirée pour le contrôle par échantillon, le professeur
// tranche — et ses décisions servent au calibrage du seuil.
const ELEMENT_STATE: Record<string, string> = {
  present: 'tracé', absent: 'absent', ambiguous: 'incertain', hidden: 'caché',
  wrong_length: 'longueur fausse',
}
const CONSTRUCTION_REASON: Record<string, string> = {
  construction_cv: 'note appliquée automatiquement',
  construction_blank: 'copie blanche, note appliquée automatiquement',
  construction_low_confidence: 'confiance sous le seuil : à corriger',
  construction_audit: 'contrôle par échantillon : votre note sert au calibrage',
  construction_unreadable: 'tracé illisible : à corriger',
}
function ConstructionReading({ reading, reason }: {
  reading: NonNullable<Item['construction']>; reason: string
}) {
  const summary = reading.status === 'blank' ? 'aucun tracé'
    : reading.status === 'read' && reading.fraction != null
      ? `${Math.round(reading.fraction * 100)} % du tracé attendu retrouvé`
      : 'lecture impossible'
  return (
    <Box mt={6}>
      <Text size="xs" c="dimmed">
        Lecture automatique : {summary}
        {reading.confidence != null ? ` · confiance ${Math.round(reading.confidence * 100)} %` : ''}
        {reading.reasons.length > 0 ? ` (${reading.reasons.join(', ')})` : ''}
      </Text>
      {CONSTRUCTION_REASON[reason] && (
        <Text size="xs" c="dimmed" fw={600}>{CONSTRUCTION_REASON[reason]}</Text>
      )}
      {reading.elements.length > 0 && (
        <Text size="xs" c="dimmed">
          {reading.elements.map((e) => `${e.name} ${ELEMENT_STATE[e.state] ?? e.state}`
            + (e.length_mm != null ? ` (${String(e.length_mm).replace('.', ',')} mm)` : '')).join(' · ')}
        </Text>
      )}
      {reading.error_feedback && (
        <Text size="xs" c="var(--mp-text-orange-strong)">
          Erreur reconnue : {reading.error_feedback}
        </Text>
      )}
    </Box>
  )
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
        border: '1px solid var(--mp-border-subtle)', borderRadius: 4, background: '#fff' }} />
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
  const [searchParams, setSearchParams] = useSearchParams()
  const assessmentFilter = searchParams.get('assessment')
  const [batches, setBatches] = useState<Batch[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [preview, setPreview] = useState<{ id: string; wave?: number } | null>(null)
  const [reviewBatch, setReviewBatch] = useState<Batch | null>(null)
  const [ocrBatch, setOcrBatch] = useState<OcrBatchRef | null>(null)
  const [scope, setScope] = useState<Scope>('flagged')
  const [mathpixOk, setMathpixOk] = useState(true)
  const [llmOk, setLlmOk] = useState(true)
  const [refine, setRefine] = useState<{ id: string; wave?: number } | null>(null)
  // revue copie par copie AVANT la correction, ouverte depuis la fin d'OCRiser
  const [preReview, setPreReview] = useState<{ id: string; wave?: number } | null>(null)
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
    latePages: sandboxResults.reduce((n, r) => n + (r.late_pages ?? 0), 0),
    conflicts: sandboxResults.reduce((n, r) => n + (r.conflicts ?? 0), 0),
    unrecognizedFiles: sandboxResults.filter((r) =>
      r.status === 'unrecognized' || r.status === 'error').length,
  }), [sandboxResults])

  // raccourcis de correction : les miens (roue crantée), sinon le défaut de
  // l'instance (Paramètres admin), sinon les défauts du code
  useEffect(() => {
    api.get<{ correction_shortcuts?: Partial<Shortcuts> }>('/api/auth/me/preferences')
      .then((p) => setShortcuts({ ...DEFAULT_SHORTCUTS, ...(p.correction_shortcuts ?? {}) }))
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
      // Dépôt ASYNCHRONE : la réponse arrive dès la réception du fichier
      // (job_id), l'identification d'une classe entière tourne en tâche de
      // fond — sinon le proxy du NAS coupait la requête au bout d'une minute
      // (504). On suit le job jusqu'à son terme (15 min max).
      type SandboxJob = { job_id: string; status: string; results: SandboxResult[]; error?: string | null }
      let job = await api.post<SandboxJob>('/api/scans/sandbox', fd)
      if (job.status === 'running') {
        notifications.show({ color: 'blue', message: 'Fichier reçu — identification des copies en cours…' })
      }
      const deadline = Date.now() + 15 * 60_000
      while (job.status === 'running' && Date.now() < deadline) {
        await new Promise((ok) => setTimeout(ok, 3000))
        job = await api.get<SandboxJob>(`/api/scans/sandbox/jobs/${job.job_id}`)
      }
      if (job.status !== 'done') {
        throw new Error(job.error || 'Le dépôt n’a pas abouti — le fichier reste dans les scans originaux (Paramètres → Données)')
      }
      const r = job
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
    const rs = await api.get<Item[]>(`/api/scans/batches/${b.id}/items?scope=${s}${waveQuery(b)}`)
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

  // Assistant OCRiser (components/OcrAssistant) : même fenêtre pour le professeur
  // et pour l'Atelier, en cascade comme en Mathpix seul.
  function openOcr(b: Batch) {
    setOcrBatch({ id: b.id, assessment_title: b.assessment_title,
      ocr_threshold: b.ocr_threshold || 0.9, query: waveQuery(b).slice(1) })
  }

  function closeOcr() {
    setOcrBatch(null); refresh()
  }

  function reviewAllFromOcr() {
    if (!ocrBatch) return
    const w = new URLSearchParams(ocrBatch.query).get('wave')
    setPreReview({ id: ocrBatch.id, wave: w == null ? undefined : Number(w) })
    setOcrBatch(null)
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

  // Lot en attente du Mac depuis trop longtemps : abandonne la lecture locale, relit avec Mathpix
  async function ocrFallback(b: Batch) {
    if (!window.confirm('Relire ce lot avec Mathpix (comme avant la lecture locale) ? La lecture locale en cours est abandonnée.')) return
    try {
      await api.post(`/api/scans/batches/${b.id}/ocr/fallback-mathpix`)
      notifications.show({ color: 'blue', message: 'Lot relu avec Mathpix' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  // Recalcule/régénère les copies corrigées après un ajustement manuel — la
  // finalisation elle-même est désormais automatique dès la dernière revue
  // tranchée (halte « Valider la correction » supprimée, 02/09) ; ce bouton
  // ne sert plus qu'au « Recalculer » de l'étape « prêt à imprimer ».
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

  // Reporte sur l'overlay imprimable des corrections affinées (régénération
  // interrompue, ou modale fermée sans régénérer). Tâche de fond : la carte
  // suit son avancement par le rafraîchissement périodique.
  async function regenerateOverlay(b: Batch) {
    try {
      await api.post(`/api/scans/batches/${b.id}/refine/regenerate`)
      notifications.show({ color: 'blue', message: 'Régénération de l’overlay lancée' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function setDistributed(b: Batch, value: boolean) {
    await api.patch(`/api/scans/batches/${b.id}`, { overlay_distributed: value })
    refresh()
  }

  // Lots de scan d'une correction : le principal puis ceux déposés après, tous
  // représentés pareil (pages, pipeline, prochaine action).
  function lotsOf(b: Batch): Lot[] {
    const fmt = (d: string | null) => d
      ? new Date(d).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : ''
    const stage = stageOf(b)
    const main: Lot = {
      wave: 0, stage: stage === 'done' ? 'ready' : stage, segments: b.segments,
      pages: b.page_count, pendingOcr: b.pending_ocr, pendingReviews: b.pending_reviews,
      conflicts: 0, overlayReady: b.status === 'overlay_ready',
      printed: b.overlay_printed, distributed: b.overlay_distributed,
      stale: b.overlay_stale, rebuilding: b.overlay_rebuilding,
      students: b.students ?? [],
      ocrLocal: b.ocr_local ?? null,
      info: [`${b.page_count} p.`, fmt(b.created_at)].filter(Boolean).join(' · '),
    }
    const late: Lot[] = (b.late_jobs ?? []).map((job) => ({
      wave: job.wave,
      stage: (['print', 'distribute', 'done'].includes(job.stage) ? 'ready' : job.stage) as LotStage,
      segments: job.segments, pages: job.page_count, pendingOcr: job.pending_ocr,
      pendingReviews: job.pending_reviews, conflicts: job.conflicts,
      overlayReady: ['print', 'distribute', 'done'].includes(job.stage),
      printed: job.overlay_printed, distributed: job.overlay_distributed,
      stale: b.overlay_stale, rebuilding: b.overlay_rebuilding,
      students: job.students,
      info: [`${job.page_count} p.`, fmt(job.created_at), lateJobNames(job)].filter(Boolean).join(' · '),
    }))
    return [main, ...late]
  }

  function lotHandlers(b: Batch, lot: Lot): LotHandlers {
    const isMain = lot.wave === 0
    const scoped = isMain ? b : { ...b, scopeWave: lot.wave }
    return {
      batchId: b.id, assessmentId: b.assessment_id,
      pendingLlm: isMain ? b.pending_llm : 0,
      onPreview: () => setPreview({ id: b.id, wave: (b.late_jobs ?? []).length ? lot.wave : undefined }),
      onOcr: () => openOcr(scoped),
      onCorrect: (s) => openCorrection(scoped, s),
      onRetry: () => retry(b),
      onOcrFallback: isMain ? () => ocrFallback(b) : undefined,
      onRetryLlm: isMain ? () => retry(b) : undefined,
      onRefine: () => setRefine({ id: b.id, wave: (b.late_jobs ?? []).length ? lot.wave : undefined }),
      onRegenerate: () => regenerateOverlay(b),
      onRecalc: isMain ? () => finalize(b) : undefined,
      onReset: isMain ? () => setResetTarget(b) : undefined,
      onDistributed: (v) => (isMain ? setDistributed(b, v) : setLateDistributed(b, lot.wave, v)),
      onChanged: refresh,
    }
  }

  async function setLateDistributed(b: Batch, wave: number, value: boolean) {
    await api.patch(`/api/scans/batches/${b.id}`, { overlay_distributed: value, wave })
    refresh()
  }

  const groups = useMemo(() => {
    const filtered = batches.filter((b) => assessmentFilter ? b.assessment_id === assessmentFilter : matches(b.grade_level))
    const by = new Map<string, { cls: string; grade: string; rows: Batch[] }>()
    for (const b of filtered) {
      const key = b.class_id || b.class_name
      if (!by.has(key)) by.set(key, { cls: b.class_name, grade: b.grade_level, rows: [] })
      by.get(key)!.rows.push(b)
    }
    return [...by.values()].sort((x, y) => x.cls.localeCompare(y.cls))
  }, [batches, matches, assessmentFilter])

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
    curItem.response_type.startsWith('qcm') || curItem.response_type === 'matching'
    || !!curItem.construction)
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

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Group><Title order={2}>Corrections</Title>{assessmentFilter && <Button variant="light" size="xs" onClick={() => setSearchParams({})}>Voir toutes les corrections</Button>}</Group>
          <Text size="sm" c="dimmed">
            Déposez le PDF scanné — le code de chaque page l'associe au bon sujet, puis
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
            {sandboxSummary.latePages > 0 && (
              <Badge size="sm" variant="light" color="teal">
                {sandboxSummary.latePages} copie{sandboxSummary.latePages > 1 ? 's' : ''}{' '}
                tardive{sandboxSummary.latePages > 1 ? 's' : ''}
              </Badge>
            )}
            {sandboxSummary.conflicts > 0 && (
              <Badge size="sm" variant="light" color="orange">
                {sandboxSummary.conflicts} double{sandboxSummary.conflicts > 1 ? 's' : ''}{' '}
                correction{sandboxSummary.conflicts > 1 ? 's' : ''} à résoudre
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
              sable ci-dessus — chaque page est associée au bon sujet par son code.
            </Text>
          </Stack>
        </Card>
      )}

      {groups.map((g) => (
        <div key={g.cls}>
          <Group gap={8} mb="xs">
            <Text fw={700}>{g.cls}</Text>
            <Badge size="sm" variant="light">{g.grade}</Badge>
            <Text size="xs" c="dimmed">{g.rows.length} correction(s)</Text>
          </Group>
          <Stack gap="xs">
            {g.rows.map((b) => {
              const lots = lotsOf(b)
              const allDone = lots.every((l) => l.stage === 'ready' && l.distributed)
              const hasLate = (b.late_jobs ?? []).length > 0
              return (
                <Card key={b.id} withBorder padding="md" style={allDone ? {
                  opacity: 0.55, background: 'var(--mp-surface-muted)',
                } : undefined}>
                  {/* En-tête : ce qui est propre à la correction entière */}
                  <Group gap="xs" wrap="nowrap" mb="sm">
                    <Text fw={600} lineClamp={1}>{b.assessment_title}</Text>
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
                    {b.duplex && (
                      <Tooltip label="Sujet imprimé en recto-verso">
                        <Badge size="sm" variant="outline" color="red" tt="none">Recto verso</Badge>
                      </Tooltip>
                    )}
                    {b.absent_count > 0 && (
                      <Tooltip label="Élève(s) sans copie, tous lots de scan confondus — jamais pénalisé(s)">
                        <Badge size="sm" variant="light" color="gray">
                          {b.absent_count} absent{b.absent_count > 1 ? 's' : ''}
                        </Badge>
                      </Tooltip>
                    )}
                  </Group>
                  {b.error && !hasLate && (
                    <Text size="xs" c="red" mb="xs">
                      Correction bloquée : {b.error} — relancez, ou re-déposez le scan.
                    </Text>
                  )}
                  {/* Un lot de scan par ligne : principal et suivants sont identiques */}
                  {/* Grille commune à tous les lots de la carte : colonnes alignées */}
                  <div style={{
                    display: 'grid', alignItems: 'center', columnGap: 0, rowGap: 12,
                    gridTemplateColumns: `max-content ${stepWidths().map((w) => `${w}px`).join(' ')} minmax(60px, 1fr) max-content`,
                    // pas d'ascenseur vertical : un bouton enfoncé (translateY) débordait
                    overflowX: 'auto', overflowY: 'hidden', padding: 3, margin: -3,
                  }}>
                    {lots.map((lot, i) => (
                      <Fragment key={lot.wave}>
                        {i > 0 && <Divider style={{ gridColumn: '1 / -1' }} />}
                        <ScanLotRow lot={lot} h={lotHandlers(b, lot)} />
                      </Fragment>
                    ))}
                  </div>
                </Card>
              )
            })}
          </Stack>
        </div>
      ))}

      {/* aperçu avant impression de l'overlay : copie scannée + overlay, lecture seule */}
      <RefineCorrectionModal mode="overlay" batchId={preview?.id ?? null} wave={preview?.wave}
        opened={!!preview} onClose={() => setPreview(null)} />

      <RefineCorrectionModal batchId={refine?.id ?? null} wave={refine?.wave} opened={!!refine}
        onClose={() => { setRefine(null); refresh() }} />

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

      <OcrAssistant batch={ocrBatch} onClose={closeOcr} onReviewAll={reviewAllFromOcr} />
      <RefineCorrectionModal mode="review" batchId={preReview?.id ?? null} wave={preReview?.wave}
        opened={!!preReview} onClose={() => { setPreReview(null); refresh() }} />

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
                    {curItem.ocr_confidence != null && !curItem.construction && (
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
                          : curItem.construction ? 'rouge = tracé attendu'
                          : 'rouge = liaisons attendues'}
                      </Text>
                    </Group>
                    <Text size="xs" c="dimmed">noir / bleu = réponse de l'élève</Text>
                  </Group>
                  {curItem.construction?.status && (
                    <ConstructionReading reading={curItem.construction} reason={curItem.reason_code} />
                  )}
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
                    Réponse de l'élève{cur.mode === 'cells' && curCell?.label ? <> — <Text span inherit tt="none"><MathText text={curCell.label} /></Text></> : null}
                  </Text>
                  <ScanImage responseId={cur.respId}
                    cellIndex={cur.mode === 'cells' ? cur.cellIndex : null} />
                  {cur.mode !== 'cells' && curItem.reason_code && (
                    <Text size="xs" c="dimmed" mt={6}>Motif : {curItem.reason_code}</Text>
                  )}
                  {curItem.flagged && curItem.reason_code === 'llm_low_confidence' && (
                    <Text size="xs" c="var(--mp-text-orange-strong)" mt={6} fw={600}>
                      Seuil LLM {(curItem.llm_threshold * 100).toFixed(0)} %
                      {curItem.llm_min_confidence != null
                        ? ` · confiance minimale de la réponse ${(curItem.llm_min_confidence * 100).toFixed(0)} %`
                        : ''}
                      {' · seules les cases sous le seuil sont à vérifier'}
                    </Text>
                  )}
                  {curItem.flagged && !curItem.reason_code.startsWith('llm_')
                    && (curItem.llm_notes ?? []).length > 0 && (
                    <Text size="xs" c="var(--mp-text-orange-strong)" mt={6} fw={600}>
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
                          <Text key={i} size="xs" c={`var(--mp-text-${color})`} fw={650}>
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
                    Réponse attendue{curCell?.label ? <> — <Text span inherit tt="none"><MathText text={curCell.label} /></Text></> : null}
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
              <Button size="xs" variant="default" onClick={closeCorrection}>Fermer</Button>
            </Group>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}
