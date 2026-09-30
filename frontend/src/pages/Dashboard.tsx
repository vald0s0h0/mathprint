// Dashboard : point d'entrée du professeur. Trois questions, dans l'ordre :
// 1. que puis-je lancer tout de suite ? (actions rapides) ;
// 2. qu'est-ce qui attend une action de ma part ? (à faire, trié par urgence) ;
// 3. où en sont mes classes ? (maîtrise, moyennes des évaluations corrigées,
//    compétences à renforcer).
// Écran en lecture seule sur les routes existantes : aucune logique métier ici,
// l'API reste l'autorité (dépôt de scans et impression réutilisent les mêmes
// appels que Corrections et Sujets).
import {
  Alert, Badge, Box, Button, Card, FileButton, Grid, Group, Modal, Paper, Progress,
  SegmentedControl, SimpleGrid, Skeleton, Stack, Text, ThemeIcon, Timeline, Title,
  Tooltip, UnstyledButton,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, CheckCircle2, CircleDollarSign,
  ClipboardList, Eye, FileText, Hourglass, Library, Loader2, Minus, Plus, Printer,
  ScanLine, Send, Sparkles, Upload, Wand2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import PdfPreviewModal from '../components/PdfPreview'
import PrintButton from '../components/PrintButton'
import { useAppState } from '../state/AppState'
import { masteryColor } from '../utils/mastery'

// ------------------------------------------------------------------ données

type DashClass = {
  id: string; name: string; grade_level: string; students: number
  avg_mastery: number; due_competencies: number
}
type Dash = {
  pending_reviews: number
  classes: DashClass[]
  assessments_draft: number
  system: { version: string }
}
type Costs = Record<string, { day_eur: number; month_eur: number; calls_month: number }>
type Assessment = {
  id: string; title: string; type: string; status: string
  class_name: string; class_id: string; grade_level: string
  duplex: boolean; overlay_distributed: boolean
  error_message: string | null; created_at: string
}
type Batch = {
  id: string; assessment_id: string; status: string
  assessment_title: string; class_name: string; grade_level: string
  overlay_printed: boolean; overlay_distributed: boolean
  error: string | null; pending_reviews: number; pending_ocr: number
  created_at: string
}
type GradeValue = { note: number | null; note_base: number; absent: boolean }
type Gradebook = {
  assessments: { id: string; title: string }[]
  values: Record<string, Record<string, GradeValue>>
}
type MatrixCompetency = {
  id: string; code: string; short_id?: string; label: string
  mastery_by_class: Record<string, number | null>
}
type Matrix = {
  domains: { chapters: { name: string; competencies: MatrixCompetency[] }[] }[]
}
type SandboxResult = {
  status: string; pages_added: number; duplicates_rejected: number; blocked_pages: number
}

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  draft: { label: 'brouillon', color: 'gray' },
  queued: { label: 'en file', color: 'yellow' },
  generating: { label: 'génération…', color: 'orange' },
  ready: { label: 'prêt', color: 'blue' },
  error: { label: 'échec', color: 'red' },
  printed: { label: 'imprimé', color: 'cyan' },
  scanning: { label: 'scan en cours', color: 'orange' },
  finalized: { label: 'corrigé', color: 'green' },
}

// Étape métier d'un lot de scans — même lecture que l'écran Corrections.
type Stage = 'awaiting' | 'processing' | 'error' | 'ocr_review' | 'review' | 'done'
function stageOf(b: Batch): Stage {
  if (b.status === 'awaiting_scan') return 'awaiting'
  if (b.error) return 'error'
  if (b.status === 'finalized' || b.status === 'overlay_ready') return 'done'
  if (b.status === 'ocr_review_pending' || b.pending_ocr > 0) return 'ocr_review'
  if (b.status === 'graded' || b.status === 'review_pending') {
    return b.pending_reviews > 0 ? 'review' : 'processing'
  }
  return 'processing'
}
const STAGE_LABEL: Record<Stage, { label: string; color: string }> = {
  awaiting: { label: 'en attente de scan', color: 'gray' },
  processing: { label: 'correction en cours', color: 'blue' },
  error: { label: 'bloqué', color: 'red' },
  ocr_review: { label: 'lecture à vérifier', color: 'blue' },
  review: { label: 'à corriger', color: 'orange' },
  done: { label: 'corrigé', color: 'green' },
}

// ------------------------------------------------------------------ outils

// dates stockées en UTC sans fuseau (DateTime naïf) : on les lit comme UTC
function parseDate(s: string): Date {
  const iso = s.includes('T') ? s : s.replace(' ', 'T')
  return new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`)
}

function timeAgo(s: string): string {
  const d = parseDate(s)
  const sec = (Date.now() - d.getTime()) / 1000
  if (Number.isNaN(sec)) return ''
  if (sec < 60) return "à l'instant"
  if (sec < 3600) return `il y a ${Math.floor(sec / 60)} min`
  if (sec < 86400) return `il y a ${Math.floor(sec / 3600)} h`
  const days = Math.floor(sec / 86400)
  if (days === 1) return 'hier'
  if (days < 7) return `il y a ${days} j`
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
}

const fmt1 = (v: number) => v.toLocaleString('fr-FR', { maximumFractionDigits: 1 })
const pct = (v: number) => `${Math.round(v * 100)} %`
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n > 1 ? many : one}`
const sum = (xs: number[]) => xs.reduce((n, x) => n + x, 0)

// Moyenne de la classe (ramenée sur 20) pour chaque évaluation corrigée, dans
// l'ordre chronologique du carnet de notes. Absents et copies sans note exclus.
type Point = { id: string; title: string; value: number }
function classSeries(book: Gradebook | null | undefined): Point[] {
  if (!book) return []
  return book.assessments.flatMap((a) => {
    const notes = Object.values(book.values)
      .map((row) => row[a.id])
      .filter((g): g is GradeValue => !!g && !g.absent && g.note != null && g.note_base > 0)
      .map((g) => (g.note as number) / g.note_base * 20)
    return notes.length ? [{ id: a.id, title: a.title, value: sum(notes) / notes.length }] : []
  })
}

// Prénom affiché dans l'accueil : premier mot du nom d'affichage, à défaut la
// partie « prénom » d'une adresse prenom.nom@… ; sinon aucun prénom.
function firstName(me: { display_name?: string; email?: string } | null): string {
  const display = (me?.display_name || '').trim().split(/\s+/)[0]
  if (display) return display
  const local = (me?.email || '').split('@')[0]
  const parts = local.split(/[._-]/).filter(Boolean)
  if (parts.length < 2 || !/^[a-zà-ÿ]+$/i.test(parts[0])) return ''
  return parts[0].charAt(0).toUpperCase() + parts[0].slice(1).toLowerCase()
}

// Phrases d'accueil selon le moment de la journée et de la semaine, tirées au
// hasard à chaque visite. `{n}` = prénom ; sans prénom, « , {n} » / « {n} »
// disparaît proprement. Tournures neutres (pas d'accord en genre).
function greetingPool(d: Date): string[] {
  const h = d.getHours()
  const day = d.getDay() // 0 = dimanche
  const weekend = day === 0 || day === 6
  let pool: string[]
  if (h >= 5 && h < 8) {
    pool = ['Déjà au travail, {n} ?', 'Debout de bonne heure, {n}', 'Un café et c’est parti, {n}',
      'Bonjour {n}, la journée commence tôt']
  } else if (h >= 8 && h < 12) {
    pool = ['Bonjour {n}', 'Belle matinée, {n}', 'Bonjour {n}, que prépare-t-on aujourd’hui ?',
      'Bon retour, {n}']
  } else if (h >= 12 && h < 14) {
    pool = ['Bon appétit, {n}', 'Bonjour {n}, une pause bien méritée ?', 'Bonjour {n}']
  } else if (h >= 14 && h < 18) {
    pool = ['Bon après-midi, {n}', 'Rebonjour {n}', 'Bonjour {n}', 'On reprend, {n} ?']
  } else if (h >= 18 && h < 22) {
    pool = ['Bonsoir {n}', 'Bonne soirée, {n}', 'Bonsoir {n}, on termine la journée ?']
  } else {
    pool = ['Encore là, {n} ?', 'Il se fait tard, {n}', 'Bonsoir {n}, pensez à vous reposer',
      'Une dernière chose avant de dormir, {n} ?']
  }
  if (day === 1 && h >= 5 && h < 12) pool.push('Bonne semaine, {n}', 'Bon lundi, {n}')
  if (day === 5 && h >= 12) pool.push('Bientôt le week-end, {n}', 'Dernière ligne droite, {n}')
  if (weekend) pool.push('Bon week-end, {n}', 'Même le week-end, {n} ?')
  return pool
}

function greeting(template: string, name: string): string {
  return name ? template.replace('{n}', name) : template.replace(/,? ?\{n\}/, '').replace(/\s+([?!])/, ' $1')
}

// ------------------------------------------------------------------ styles

const CSS = `
.mpd-tile{position:relative;display:flex;flex-direction:column;gap:12px;width:100%;height:100%;
  padding:16px;border-radius:var(--mantine-radius-md);border:1px solid var(--mantine-color-default-border);
  background:var(--mantine-color-default);text-align:left;
  transition:transform 140ms ease,box-shadow 140ms ease,border-color 140ms ease}
.mpd-tile:hover:not(:disabled){transform:translateY(-2px);box-shadow:var(--mantine-shadow-md);border-color:var(--tile-strong)}
.mpd-tile:focus-visible{outline:2px solid var(--mantine-color-indigo-5);outline-offset:2px}
.mpd-tile:disabled{opacity:.55;cursor:not-allowed}
.mpd-tileIcon{display:grid;place-items:center;width:40px;height:40px;border-radius:10px;
  background:var(--tile-soft);color:var(--tile-strong)}
.mpd-tileHint{color:var(--mantine-color-dimmed)}
.mpd-tileBadge{position:absolute;top:12px;right:12px}
.mpd-primary{border-color:transparent;color:var(--mantine-color-white);
  background:linear-gradient(135deg,var(--mantine-color-indigo-6),var(--mantine-color-violet-6))}
.mpd-primary:hover:not(:disabled){border-color:transparent}
.mpd-primary .mpd-tileIcon{background:rgba(255,255,255,.18);color:var(--mantine-color-white)}
.mpd-primary .mpd-tileHint{color:rgba(255,255,255,.82)}
.mpd-todo{position:relative;display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;
  padding:10px 12px 10px 16px;border-radius:10px;transition:background 120ms ease}
.mpd-todo::before{content:'';position:absolute;left:4px;top:12px;bottom:12px;width:3px;
  border-radius:3px;background:var(--todo-color)}
.mpd-todo:hover{background:var(--mantine-color-default-hover)}
.mpd-todoBody{flex:1 1 220px;min-width:0}
.mpd-todoActions{display:flex;gap:6px;margin-left:auto}
@keyframes mpd-spin{to{transform:rotate(360deg)}}
.mpd-spin{animation:mpd-spin 1.2s linear infinite}
`

// ------------------------------------------------------------------ composants

function ActionTile({ icon, title, hint, color, badge, primary, disabled, loading, onClick }: {
  icon: ReactNode; title: string; hint: string; color: string
  badge?: number; primary?: boolean; disabled?: boolean; loading?: boolean
  onClick?: () => void
}) {
  return (
    <UnstyledButton onClick={onClick} disabled={disabled || loading}
      className={`mpd-tile${primary ? ' mpd-primary' : ''}`}
      style={{
        '--tile-soft': `var(--mantine-color-${color}-light)`,
        '--tile-strong': `var(--mantine-color-${color}-light-color)`,
      } as CSSProperties}>
      <div className="mpd-tileIcon">
        {loading ? <Loader2 size={20} className="mpd-spin" /> : icon}
      </div>
      <div>
        <Text fw={650} size="sm">{title}</Text>
        <Text size="xs" className="mpd-tileHint" lineClamp={2}>{hint}</Text>
      </div>
      {!!badge && (
        <Badge className="mpd-tileBadge" size="sm" circle={badge < 10}
          color={primary ? 'white' : color} variant={primary ? 'white' : 'filled'}>
          {badge}
        </Badge>
      )}
    </UnstyledButton>
  )
}

type Todo = {
  key: string; tone: string; icon: ReactNode; title: string; detail: string
  weight: number; action?: ReactNode; progress?: number
}

function TodoRow({ t }: { t: Todo }) {
  return (
    <div className="mpd-todo"
      style={{ '--todo-color': `var(--mantine-color-${t.tone}-6)` } as CSSProperties}>
      <ThemeIcon variant="light" color={t.tone} size={36} radius="md">{t.icon}</ThemeIcon>
      <div className="mpd-todoBody">
        <Text size="sm" fw={600} lineClamp={1}>{t.title}</Text>
        <Text size="xs" c="dimmed" lineClamp={1}>{t.detail}</Text>
        {t.progress != null && (
          <Progress value={t.progress} size={4} mt={6} color={t.tone} animated />
        )}
      </div>
      {t.action && <div className="mpd-todoActions">{t.action}</div>}
    </div>
  )
}

// Courbe des moyennes d'une classe : échelle recadrée sur les données mais
// jamais sur moins de 8 points (une variation d'un demi-point reste plate),
// bornée à 0-20, repère pointillé à 10 quand il est dans le cadre, dernier
// point accentué, info-bulle au survol de chaque évaluation.
function Sparkline({ points, width = 150, height = 44 }: {
  points: Point[]; width?: number; height?: number
}) {
  const [hover, setHover] = useState<number | null>(null)
  const pad = 6
  const x = (i: number) => points.length === 1
    ? width / 2 : pad + i * (width - 2 * pad) / (points.length - 1)
  const values = points.map((p) => p.value)
  const mid = (Math.min(...values) + Math.max(...values)) / 2
  const span = Math.max(8, Math.max(...values) - Math.min(...values) + 2)
  const lo = Math.min(Math.max(0, mid - span / 2), 20 - span)
  const hi = lo + span
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (height - 2 * pad)
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ')
  const last = points.length - 1
  const active = hover ?? last
  const slot = points.length > 1 ? (width - 2 * pad) / (points.length - 1) : width
  return (
    <Box pos="relative" w={width} h={height} onMouseLeave={() => setHover(null)}
      style={{ flexShrink: 0 }}>
      <svg width={width} height={height} role="img" style={{ display: 'block', overflow: 'visible' }}
        aria-label={`Moyennes successives : ${points.map((p) => fmt1(p.value)).join(', ')} sur 20`}>
        {lo < 10 && hi > 10 && (
          <line x1={pad} x2={width - pad} y1={y(10)} y2={y(10)}
            stroke="var(--mantine-color-default-border)" strokeDasharray="3 3" />
        )}
        {points.length > 1 && (
          <path d={path} fill="none" stroke="var(--mantine-color-indigo-3)" strokeWidth={2}
            strokeLinejoin="round" strokeLinecap="round" />
        )}
        {hover !== null && (
          <line x1={x(hover)} x2={x(hover)} y1={0} y2={height}
            stroke="var(--mantine-color-default-border)" />
        )}
        <circle cx={x(active)} cy={y(points[active].value)} r={4.5}
          fill="var(--mantine-color-indigo-6)" stroke="var(--mantine-color-default)" strokeWidth={2} />
        {points.map((p, i) => (
          <rect key={p.id} x={x(i) - slot / 2} y={0} width={slot} height={height}
            fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && (
        <Paper shadow="sm" withBorder px={8} py={4} style={{
          position: 'absolute', bottom: height + 6, left: x(hover), transform: 'translateX(-50%)',
          pointerEvents: 'none', whiteSpace: 'nowrap', zIndex: 5,
        }}>
          <Text size="xs" fw={600} maw={220} truncate>{points[hover].title}</Text>
          <Text size="xs" c="dimmed">moyenne {fmt1(points[hover].value)} / 20</Text>
        </Paper>
      )}
    </Box>
  )
}

function Delta({ value }: { value: number | null }) {
  if (value == null) return null
  const rounded = Math.round(value * 10) / 10
  const color = rounded > 0 ? 'green' : rounded < 0 ? 'red' : 'gray'
  const Icon = rounded > 0 ? ArrowUpRight : rounded < 0 ? ArrowDownRight : Minus
  return (
    <Tooltip label="Écart avec l'évaluation précédente">
      <Group gap={2} wrap="nowrap" c={color}>
        <Icon size={14} />
        <Text size="xs" fw={600} c={color}>
          {rounded > 0 ? '+' : ''}{fmt1(rounded)}
        </Text>
      </Group>
    </Tooltip>
  )
}

function SectionTitle({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <Group justify="space-between" mb="sm" wrap="nowrap">
      <Text fw={650}>{title}</Text>
      {action}
    </Group>
  )
}

function LinkButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button size="compact-xs" variant="subtle" rightSection={<ArrowRight size={12} />}
      onClick={onClick}>
      {label}
    </Button>
  )
}

function MiniStat({ label, value, hint, extra }: {
  label: string; value: ReactNode; hint?: string; extra?: ReactNode
}) {
  return (
    <Paper withBorder radius="md" p="sm">
      <Text size="xs" c="dimmed" lineClamp={1}>{label}</Text>
      <Group gap={6} align="baseline" wrap="nowrap">
        <Text fz={22} fw={650} lh={1.2}>{value}</Text>
        {extra}
      </Group>
      {hint && <Text size="xs" c="dimmed" lineClamp={1}>{hint}</Text>}
    </Paper>
  )
}

// ------------------------------------------------------------------ écran

export default function Dashboard() {
  const navigate = useNavigate()
  const { cycle, matches, activeJobs } = useAppState()

  const [dash, setDash] = useState<Dash | null>(null)
  const [dashFailed, setDashFailed] = useState(false)
  const [assessments, setAssessments] = useState<Assessment[]>([])
  const [batches, setBatches] = useState<Batch[]>([])
  const [costs, setCosts] = useState<Costs | null>(null)
  const [scansReady, setScansReady] = useState(true)
  const [books, setBooks] = useState<Record<string, Gradebook | null>>({})
  const [matrices, setMatrices] = useState<Record<string, Matrix | null>>({})

  const [printOpen, setPrintOpen] = useState(false)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [showAllTodos, setShowAllTodos] = useState(false)
  const [compView, setCompView] = useState<'weak' | 'strong'>('weak')
  const [name, setName] = useState('')
  // tirage fixé pour la visite : le poll ne fait pas changer la phrase
  const [greetSeed] = useState(() => Math.random())

  // état « vivant » (à faire, activité) rafraîchi en continu ; un poll qui
  // échoue ne vide jamais ce qui est déjà affiché
  const loadLive = useCallback(() => {
    api.get<Dash>('/api/dashboard')
      .then((d) => { setDash(d); setDashFailed(false) })
      .catch(() => setDashFailed(true))
    api.get<Assessment[]>('/api/assessments').then(setAssessments).catch(() => {})
    api.get<Batch[]>('/api/scans/batches').then(setBatches).catch(() => {})
  }, [])

  useEffect(() => {
    loadLive()
    const t = setInterval(loadLive, 10000)
    return () => clearInterval(t)
  }, [loadLive])

  useEffect(() => {
    api.get<Costs>('/api/costs').then(setCosts).catch(() => {})
    api.get<{ display_name: string; email: string }>('/api/auth/me')
      .then((me) => setName(firstName(me))).catch(() => {})
    api.get<{ mathpix_configured: boolean }>('/api/scans/config')
      .then((c) => setScansReady(c.mathpix_configured)).catch(() => {})
  }, [])

  // carnets de notes et maîtrise par compétence : rechargés seulement quand
  // les classes changent ou qu'une nouvelle correction est consolidée
  const finalizedCount = assessments.filter((a) => a.status === 'finalized').length
  const classKey = dash ? dash.classes.map((c) => c.id).join(',') : ''
  const gradeKey = dash ? [...new Set(dash.classes.map((c) => c.grade_level))].sort().join(',') : ''

  useEffect(() => {
    if (!classKey) return
    let alive = true
    for (const id of classKey.split(',')) {
      api.get<Gradebook>(`/api/grades/classes/${id}?kind=all`)
        .then((b) => { if (alive) setBooks((m) => ({ ...m, [id]: b })) })
        .catch(() => { if (alive) setBooks((m) => ({ ...m, [id]: null })) })
    }
    return () => { alive = false }
  }, [classKey, finalizedCount])

  useEffect(() => {
    if (!gradeKey) return
    let alive = true
    for (const g of gradeKey.split(',')) {
      api.get<Matrix>(`/api/assessments/competency-matrix?grade_level=${encodeURIComponent(g)}`)
        .then((m) => { if (alive) setMatrices((x) => ({ ...x, [g]: m })) })
        .catch(() => { if (alive) setMatrices((x) => ({ ...x, [g]: null })) })
    }
    return () => { alive = false }
  }, [gradeKey, finalizedCount])

  // ---------------------------------------------------------- dérivés

  const classes = useMemo(
    () => (dash?.classes ?? []).filter((c) => matches(c.grade_level))
      .sort((a, b) => a.grade_level.localeCompare(b.grade_level) || a.name.localeCompare(b.name)),
    [dash, matches],
  )
  const visibleAssessments = useMemo(
    () => assessments.filter((a) => matches(a.grade_level)), [assessments, matches])
  const visibleBatches = useMemo(
    () => batches.filter((b) => matches(b.grade_level)), [batches, matches])

  const readySubjects = visibleAssessments.filter((a) => a.status === 'ready')
  const printableSubjects = visibleAssessments.filter(
    (a) => ['ready', 'printed', 'scanning'].includes(a.status) && !a.overlay_distributed)
  const printableOverlays = visibleBatches.filter(
    (b) => b.status === 'overlay_ready' && !b.overlay_distributed)
  const overlaysToPrint = printableOverlays.filter((b) => !b.overlay_printed)
  const reviewCount = sum(visibleBatches.filter((b) => stageOf(b) === 'review')
    .map((b) => b.pending_reviews))
  const printBadge = readySubjects.length + overlaysToPrint.length

  const series = useMemo(() => {
    const out: Record<string, Point[]> = {}
    for (const c of classes) out[c.id] = classSeries(books[c.id])
    return out
  }, [classes, books])

  const students = sum(classes.map((c) => c.students))
  const mastery = students
    ? sum(classes.map((c) => c.avg_mastery * c.students)) / students : 0
  const dueTotal = sum(classes.map((c) => c.due_competencies))
  const latest = classes.map((c) => series[c.id]).filter((s) => s.length > 0)
  const latestAvg = latest.length ? sum(latest.map((s) => s[s.length - 1].value)) / latest.length : null
  const withPrevious = latest.filter((s) => s.length > 1)
  const latestDelta = withPrevious.length
    ? sum(withPrevious.map((s) => s[s.length - 1].value - s[s.length - 2].value)) / withPrevious.length
    : null

  // compétences classées par maîtrise moyenne des classes visibles (sans donnée = ignorée)
  const ranked = useMemo(() => {
    const rows: { id: string; code: string; label: string; chapter: string; grade: string; value: number }[] = []
    for (const [grade, m] of Object.entries(matrices)) {
      if (!m || !matches(grade)) continue
      const ids = classes.filter((c) => c.grade_level === grade).map((c) => c.id)
      for (const d of m.domains) for (const ch of d.chapters) for (const comp of ch.competencies) {
        const vals = ids.map((id) => comp.mastery_by_class[id])
          .filter((v): v is number => typeof v === 'number')
        if (!vals.length) continue
        rows.push({
          id: comp.id, code: comp.short_id || comp.code, label: comp.label,
          chapter: ch.name, grade, value: sum(vals) / vals.length,
        })
      }
    }
    return rows.sort((a, b) => a.value - b.value)
  }, [matrices, classes, matches])
  const compRows = compView === 'weak' ? ranked.slice(0, 6) : [...ranked].reverse().slice(0, 6)

  const monthCost = costs ? sum(Object.values(costs).map((c) => c.month_eur)) : 0

  // ---------------------------------------------------------- actions

  async function uploadScans(files: File[]) {
    if (!files.length) return
    setUploading(true)
    try {
      const fd = new FormData()
      for (const f of files) fd.append('files', f)
      const r = await api.post<{ results: SandboxResult[] }>('/api/scans/sandbox', fd)
      const pages = sum(r.results.map((x) => x.pages_added))
      const dups = sum(r.results.map((x) => x.duplicates_rejected + (x.status === 'duplicate_file' ? 1 : 0)))
      const blocked = sum(r.results.map((x) => x.blocked_pages))
      notifications.show({
        color: pages ? 'green' : 'orange',
        title: 'Scans déposés',
        message: `${plural(pages, 'page identifiée', 'pages identifiées')}`
          + (dups ? `, ${plural(dups, 'doublon ignoré', 'doublons ignorés')}` : '')
          + (blocked ? `, ${plural(blocked, 'page non identifiée', 'pages non identifiées')}` : '')
          + ' — suivi dans Corrections.',
      })
      loadLive()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setUploading(false)
    }
  }

  // ---------------------------------------------------------- à faire

  const todos = useMemo(() => {
    const out: Todo[] = []
    const go = (to: string, label: string, color?: string) => (
      <Button size="xs" variant="light" color={color} rightSection={<ArrowRight size={13} />}
        onClick={() => navigate(to)}>{label}</Button>
    )
    for (const b of visibleBatches) {
      const stage = stageOf(b)
      const where = `${b.assessment_title} · ${b.class_name}`
      if (stage === 'error') {
        out.push({ key: `err-${b.id}`, tone: 'red', weight: 0, icon: <AlertTriangle size={18} />,
          title: 'Correction bloquée', detail: b.error ? `${where} — ${b.error}` : where,
          action: go('/corrections', 'Débloquer', 'red') })
      } else if (stage === 'review') {
        out.push({ key: `rev-${b.id}`, tone: 'orange', weight: 1, icon: <ScanLine size={18} />,
          title: `${plural(b.pending_reviews, 'réponse')} à corriger`, detail: where,
          action: go('/corrections', 'Corriger', 'orange') })
      } else if (stage === 'ocr_review') {
        out.push({ key: `ocr-${b.id}`, tone: 'blue', weight: 2, icon: <Eye size={18} />,
          title: 'Lecture des copies à vérifier', detail: where,
          action: go('/corrections', 'Vérifier') })
      } else if (b.status === 'overlay_ready' && !b.overlay_printed) {
        out.push({ key: `ovl-${b.id}`, tone: 'green', weight: 3, icon: <CheckCircle2 size={18} />,
          title: 'Copies corrigées prêtes à imprimer', detail: where,
          action: <PrintButton assessmentId={b.assessment_id} file="correction_overlay.pdf"
            label="Imprimer l'overlay" /> })
      } else if (b.status === 'overlay_ready' && !b.overlay_distributed) {
        out.push({ key: `dist-${b.id}`, tone: 'teal', weight: 6, icon: <Send size={18} />,
          title: 'Copies corrigées à rendre', detail: where,
          action: go('/corrections', 'Marquer distribué', 'teal') })
      }
    }
    for (const a of visibleAssessments) {
      const where = `${a.class_name} · ${a.type === 'control' ? 'contrôle' : 'entraînement'}`
      if (a.status === 'error') {
        out.push({ key: `gen-${a.id}`, tone: 'red', weight: 0, icon: <AlertTriangle size={18} />,
          title: 'Génération échouée',
          detail: a.error_message ? `${a.title} — ${a.error_message}` : `${a.title} · ${where}`,
          action: go('/sujets', 'Réessayer', 'red') })
      } else if (a.status === 'ready') {
        out.push({ key: `rdy-${a.id}`, tone: 'indigo', weight: 4, icon: <Printer size={18} />,
          title: 'Sujet prêt à imprimer', detail: `${a.title} · ${where}`,
          action: <>
            <Button size="xs" variant="subtle" leftSection={<Eye size={14} />}
              onClick={() => setPreviewId(a.id)}>Aperçu</Button>
            <PrintButton assessmentId={a.id} file="subject_batch.pdf" label="Imprimer"
              assessmentDuplex={a.duplex} />
          </> })
      }
    }
    // sujets imprimés dont aucune copie n'est encore revenue au scanner
    for (const b of visibleBatches) {
      if (stageOf(b) !== 'awaiting') continue
      const a = visibleAssessments.find((x) => x.id === b.assessment_id)
      if (a?.status !== 'printed') continue
      out.push({ key: `scan-${b.id}`, tone: 'gray', weight: 5, icon: <Hourglass size={18} />,
        title: 'En attente des copies scannées', detail: `${b.assessment_title} · ${b.class_name}`,
        action: scansReady ? (
          <FileButton onChange={uploadScans} multiple
            accept="application/pdf,image/jpeg,image/png,image/heic,image/heif">
            {(props) => (
              <Button {...props} size="xs" variant="light" color="gray" loading={uploading}
                leftSection={<Upload size={14} />}>Déposer</Button>
            )}
          </FileButton>
        ) : undefined })
    }
    for (const j of activeJobs) {
      out.push({ key: `job-${j.assessment_id}`, tone: 'blue', weight: 7,
        icon: <Loader2 size={18} className="mpd-spin" />,
        title: 'Génération en cours', detail: `${j.title} · ${j.class_name}`, progress: j.progress })
    }
    return out.sort((x, y) => x.weight - y.weight)
  }, [visibleBatches, visibleAssessments, activeJobs, scansReady, uploading, navigate])

  // ---------------------------------------------------------- activité récente

  const activity = useMemo(() => {
    const rows = [
      ...visibleAssessments.map((a) => ({
        key: `a-${a.id}`, when: a.created_at, icon: <FileText size={12} />,
        title: a.title, detail: `Sujet créé · ${a.class_name}`,
        badge: a.overlay_distributed ? { label: 'terminé', color: 'gray' }
          : STATUS_LABEL[a.status] ?? { label: a.status, color: 'gray' },
      })),
      ...visibleBatches.filter((b) => b.status !== 'awaiting_scan').map((b) => ({
        key: `b-${b.id}`, when: b.created_at, icon: <ScanLine size={12} />,
        title: b.assessment_title, detail: `Copies déposées · ${b.class_name}`,
        badge: b.overlay_distributed ? { label: 'terminé', color: 'gray' } : STAGE_LABEL[stageOf(b)],
      })),
    ]
    return rows.sort((x, y) => parseDate(y.when).getTime() - parseDate(x.when).getTime()).slice(0, 7)
  }, [visibleAssessments, visibleBatches])

  // ---------------------------------------------------------- rendu

  if (!dash) {
    if (dashFailed) {
      return (
        <Alert color="red" icon={<AlertTriangle size={16} />} title="Tableau de bord indisponible">
          Impossible de joindre le serveur. Nouvel essai automatique dans quelques secondes.
        </Alert>
      )
    }
    return (
      <Stack gap="lg">
        <Skeleton h={52} w={320} />
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }}>
          {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} h={118} />)}
        </SimpleGrid>
        <Grid>
          <Grid.Col span={{ base: 12, lg: 7 }}><Skeleton h={280} /></Grid.Col>
          <Grid.Col span={{ base: 12, lg: 5 }}><Skeleton h={280} /></Grid.Col>
        </Grid>
      </Stack>
    )
  }

  const pool = greetingPool(new Date())
  const hello = greeting(pool[Math.floor(greetSeed * pool.length)], name)
  const today = new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })
  const summary = [
    reviewCount > 0 && plural(reviewCount, 'réponse à corriger', 'réponses à corriger'),
    readySubjects.length > 0 && plural(readySubjects.length, 'sujet prêt à imprimer', 'sujets prêts à imprimer'),
    overlaysToPrint.length > 0 && plural(overlaysToPrint.length, 'correction à imprimer', 'corrections à imprimer'),
  ].filter(Boolean) as string[]
  const visibleTodos = showAllTodos ? todos : todos.slice(0, 5)
  const cycleLabel = cycle === 'all' ? 'tous cycles' : `cycle ${cycle}`

  return (
    <Stack gap="lg">
      <style>{CSS}</style>

      {/* -------- en-tête -------- */}
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={600} style={{ letterSpacing: 0.6 }}>
            {today} · {cycleLabel}
          </Text>
          <Title order={2}>{hello}</Title>
          <Text size="sm" c="dimmed">
            {summary.length
              ? `${summary.join(', ')}.`
              : 'Rien d’urgent : aucune correction ni impression en attente.'}
          </Text>
        </div>
        {costs && (
          <Tooltip multiline w={240} label={
            <Stack gap={2}>
              {Object.entries(costs).filter(([, c]) => c.calls_month > 0).map(([p, c]) => (
                <Group key={p} justify="space-between" gap="md">
                  <Text size="xs">{p}</Text>
                  <Text size="xs">{c.month_eur.toFixed(2)} € · {c.calls_month} appels</Text>
                </Group>
              ))}
              {!Object.values(costs).some((c) => c.calls_month > 0) && (
                <Text size="xs">Aucun appel sur 30 jours</Text>
              )}
            </Stack>
          }>
            <Badge variant="light" color="teal" size="lg" radius="md"
              leftSection={<CircleDollarSign size={14} />} style={{ textTransform: 'none' }}>
              {monthCost.toFixed(2)} € d’API sur 30 jours
            </Badge>
          </Tooltip>
        )}
      </Group>

      {/* -------- actions rapides -------- */}
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm">
        <ActionTile primary icon={<Plus size={20} />} color="indigo" title="Créer un sujet"
          hint="Entraînement ou contrôle, pour une classe"
          onClick={() => navigate('/sujets?nouveau=1')} />
        <ActionTile icon={<Library size={20} />} color="violet" title="Banque d'exercices"
          hint="Parcourir les exercices par compétence" onClick={() => navigate('/banque')} />
        <ActionTile icon={<Printer size={20} />} color="blue" title="Imprimer"
          hint="Sujets prêts et copies corrigées" badge={printBadge}
          onClick={() => setPrintOpen(true)} />
        <Tooltip label="Configurez d'abord la clé Mathpix (Paramètres → API)" disabled={scansReady}>
          <div style={{ height: '100%' }}>
            <FileButton onChange={uploadScans} multiple disabled={!scansReady}
              accept="application/pdf,image/jpeg,image/png,image/heic,image/heif">
              {(props) => (
                <ActionTile {...props} icon={<Upload size={20} />} color="cyan"
                  title="Déposer des scans" hint="PDF et photos, même mélangés"
                  disabled={!scansReady} loading={uploading} />
              )}
            </FileButton>
          </div>
        </Tooltip>
        <ActionTile icon={<ScanLine size={20} />} color="orange" title="Corriger"
          hint="Réponses signalées par la correction" badge={reviewCount}
          onClick={() => navigate('/corrections')} />
        <ActionTile icon={<ClipboardList size={20} />} color="teal" title="Carnet de notes"
          hint="Notes et évolution par élève" onClick={() => navigate('/notes')} />
      </SimpleGrid>

      {/* -------- à faire + activité -------- */}
      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Card withBorder padding="lg" h="100%">
            <SectionTitle title="À faire" action={todos.length > 0 && (
              <Badge variant="light" color={todos.some((t) => t.weight <= 1) ? 'orange' : 'gray'}>
                {todos.length}
              </Badge>
            )} />
            {todos.length === 0 ? (
              <Stack align="center" gap={6} py="xl">
                <ThemeIcon variant="light" color="green" size={48} radius="xl">
                  <CheckCircle2 size={26} />
                </ThemeIcon>
                <Text fw={600}>Tout est à jour</Text>
                <Text size="sm" c="dimmed" ta="center" maw={340}>
                  Aucune correction, impression ou génération en attente
                  {cycle !== 'all' && ` en ${cycle}`}.
                </Text>
                <Button mt="xs" variant="light" leftSection={<Plus size={16} />}
                  onClick={() => navigate('/sujets?nouveau=1')}>
                  Préparer le prochain sujet
                </Button>
              </Stack>
            ) : (
              <Stack gap={2}>
                {visibleTodos.map((t) => <TodoRow key={t.key} t={t} />)}
                {todos.length > 5 && (
                  <Button variant="subtle" size="xs" mt={4} onClick={() => setShowAllTodos((v) => !v)}>
                    {showAllTodos ? 'Réduire'
                      : todos.length === 6 ? 'Voir 1 autre' : `Voir les ${todos.length - 5} autres`}
                  </Button>
                )}
              </Stack>
            )}
          </Card>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Card withBorder padding="lg" h="100%">
            <SectionTitle title="Activité récente"
              action={<LinkButton label="Sujets" onClick={() => navigate('/sujets')} />} />
            {activity.length === 0 ? (
              <Text size="sm" c="dimmed">Aucun sujet ni lot de copies pour le moment.</Text>
            ) : (
              <Timeline bulletSize={22} lineWidth={2} active={-1}>
                {activity.map((r) => (
                  <Timeline.Item key={r.key} bullet={r.icon} title={
                    <Group justify="space-between" wrap="nowrap" gap="xs">
                      <Text size="sm" fw={600} lineClamp={1}>{r.title}</Text>
                      <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                        {timeAgo(r.when)}
                      </Text>
                    </Group>
                  }>
                    <Group gap={6} wrap="nowrap">
                      <Text size="xs" c="dimmed" lineClamp={1}>{r.detail}</Text>
                      <Badge size="xs" variant="dot" color={r.badge.color} style={{ flexShrink: 0 }}>
                        {r.badge.label}
                      </Badge>
                    </Group>
                  </Timeline.Item>
                ))}
              </Timeline>
            )}
          </Card>
        </Grid.Col>
      </Grid>

      {/* -------- aperçu des progrès -------- */}
      <Group justify="space-between" mt="xs">
        <div>
          <Title order={3}>Aperçu des progrès</Title>
          <Text size="sm" c="dimmed">
            Maîtrise des compétences et moyennes des évaluations corrigées
          </Text>
        </div>
        <LinkButton label="Élèves" onClick={() => navigate('/eleves')} />
      </Group>

      {classes.length === 0 ? (
        <Card withBorder padding="xl">
          <Stack align="center" gap="xs">
            <Sparkles size={32} strokeWidth={1.5} opacity={0.5} />
            <Text fw={600}>Aucune classe {cycle !== 'all' && `en ${cycle}`}</Text>
            <Text size="sm" c="dimmed" ta="center">
              Créez une classe et importez vos élèves pour suivre leurs progrès ici.
            </Text>
            <Button mt="xs" variant="light" onClick={() => navigate('/eleves')}>
              Créer une classe
            </Button>
          </Stack>
        </Card>
      ) : (
        <Grid gutter="lg">
          <Grid.Col span={{ base: 12, md: 4 }}>
            <Card withBorder padding="lg" h="100%">
              <Text size="sm" c="dimmed">Maîtrise moyenne</Text>
              <Text fz={52} fw={700} lh={1.05} mt={4}>{pct(mastery)}</Text>
              <Progress value={mastery * 100} size="md" radius="xl" mt="sm"
                color={masteryColor(mastery)} aria-label="Maîtrise moyenne" />
              <Text size="xs" c="dimmed" mt={6}>
                {plural(students, 'élève suivi', 'élèves suivis')} · {plural(classes.length, 'classe')}
              </Text>
              <SimpleGrid cols={{ base: 2, md: 1, lg: 2 }} spacing="sm" mt="lg">
                <MiniStat label="Dernières évaluations"
                  value={latestAvg == null ? '—' : <>{fmt1(latestAvg)}<Text span size="sm" c="dimmed"> /20</Text></>}
                  extra={<Delta value={latestDelta} />}
                  hint={latestAvg == null ? 'aucune copie corrigée' : 'moyenne des classes'} />
                <MiniStat label="Compétences à revoir" value={dueTotal}
                  hint="sous le seuil d’oubli" />
                <MiniStat label="Sujets corrigés"
                  value={visibleAssessments.filter((a) => a.status === 'finalized').length}
                  hint={`sur ${visibleAssessments.length} créés`} />
                <MiniStat label="Brouillons" value={dash.assessments_draft}
                  hint="sujets non générés" />
              </SimpleGrid>
            </Card>
          </Grid.Col>

          <Grid.Col span={{ base: 12, md: 8 }}>
            <Card withBorder padding="lg" h="100%">
              <SectionTitle title="Classes"
                action={<LinkButton label="Carnet de notes" onClick={() => navigate('/notes')} />} />
              <Stack gap={0}>
                {classes.map((c, i) => {
                  const pts = series[c.id] ?? []
                  const last = pts.length ? pts[pts.length - 1].value : null
                  const delta = pts.length > 1 ? pts[pts.length - 1].value - pts[pts.length - 2].value : null
                  return (
                    <Box key={c.id} py="sm" style={i ? {
                      borderTop: '1px solid var(--mantine-color-default-border)',
                    } : undefined}>
                      <Grid align="center" gutter="md">
                        <Grid.Col span={{ base: 12, sm: 4 }}>
                          <Group gap={6} wrap="nowrap">
                            <Text fw={650} truncate>{c.name}</Text>
                            <Badge size="xs" variant="light">{c.grade_level}</Badge>
                          </Group>
                          <Group gap={8} mt={2} wrap="nowrap">
                            <Text size="xs" c="dimmed">{plural(c.students, 'élève')}</Text>
                            {c.due_competencies > 0 && (
                              <Tooltip label="Compétences dont le souvenir passe sous le seuil d'oubli">
                                <Badge size="xs" color="orange" variant="light"
                                  leftSection={<AlertTriangle size={10} />}>
                                  {c.due_competencies} à revoir
                                </Badge>
                              </Tooltip>
                            )}
                          </Group>
                        </Grid.Col>
                        <Grid.Col span={{ base: 12, sm: 3 }}>
                          <Text size="xs" c="dimmed">Maîtrise</Text>
                          <Group gap={8} wrap="nowrap">
                            <Progress value={c.avg_mastery * 100} size="sm" radius="xl"
                              color={masteryColor(c.avg_mastery)} style={{ flex: 1 }}
                              aria-label={`Maîtrise ${c.name}`} />
                            <Text size="xs" fw={600} style={{ fontVariantNumeric: 'tabular-nums' }}>
                              {pct(c.avg_mastery)}
                            </Text>
                          </Group>
                        </Grid.Col>
                        <Grid.Col span={{ base: 12, sm: 5 }}>
                          {pts.length === 0 ? (
                            <Text size="xs" c="dimmed">
                              {books[c.id] === undefined ? 'Chargement des notes…'
                                : 'Pas encore d’évaluation corrigée'}
                            </Text>
                          ) : (
                            <Group gap="md" wrap="nowrap" justify="flex-end">
                              <Sparkline points={pts} />
                              <div style={{ minWidth: 64, textAlign: 'right' }}>
                                <Text fw={650} lh={1.2}>
                                  {fmt1(last as number)}<Text span size="xs" c="dimmed"> /20</Text>
                                </Text>
                                <Group justify="flex-end"><Delta value={delta} /></Group>
                              </div>
                            </Group>
                          )}
                        </Grid.Col>
                      </Grid>
                    </Box>
                  )
                })}
              </Stack>
            </Card>
          </Grid.Col>

          <Grid.Col span={12}>
            <Card withBorder padding="lg">
              <Group justify="space-between" mb="sm" wrap="wrap" gap="xs">
                <Text fw={650}>Compétences</Text>
                <Group gap="xs">
                  <SegmentedControl size="xs" value={compView}
                    onChange={(v) => setCompView(v as 'weak' | 'strong')}
                    data={[{ value: 'weak', label: 'À renforcer' }, { value: 'strong', label: 'Les plus solides' }]} />
                  <LinkButton label="Toutes" onClick={() => navigate('/competences')} />
                </Group>
              </Group>
              {ranked.length === 0 ? (
                <Text size="sm" c="dimmed">
                  La maîtrise par compétence apparaîtra après la première correction.
                </Text>
              ) : (
                <SimpleGrid cols={{ base: 1, md: 2 }} spacing="xs" verticalSpacing="xs">
                  {compRows.map((r) => (
                    <Paper key={r.id} withBorder radius="md" px="sm" py={8}>
                      <Group gap="sm" wrap="nowrap">
                        <Badge variant="light" color="gray" radius="sm" visibleFrom="sm"
                          style={{ flexShrink: 0 }}>
                          {r.code}
                        </Badge>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Tooltip label={r.label} multiline maw={360} openDelay={400}>
                            <Text size="sm" fw={500} truncate>{r.label}</Text>
                          </Tooltip>
                          <Text size="xs" c="dimmed" truncate>
                            {cycle === 'all' ? `${r.grade} · ` : ''}{r.chapter}
                          </Text>
                        </div>
                        <Group gap={8} wrap="nowrap" w={{ base: 88, sm: 120 }} style={{ flexShrink: 0 }}>
                          <Progress value={r.value * 100} size="sm" radius="xl"
                            color={masteryColor(r.value)} style={{ flex: 1 }}
                            aria-label={`Maîtrise ${r.code}`} />
                          <Text size="xs" fw={600} w={34} ta="right"
                            style={{ fontVariantNumeric: 'tabular-nums' }}>
                            {pct(r.value)}
                          </Text>
                        </Group>
                      </Group>
                    </Paper>
                  ))}
                </SimpleGrid>
              )}
              {compView === 'weak' && ranked.length > 0 && (
                <Group justify="flex-end" mt="sm">
                  <Button size="xs" variant="light" leftSection={<Wand2 size={14} />}
                    onClick={() => navigate('/sujets?nouveau=1')}>
                    Créer un sujet de remédiation
                  </Button>
                </Group>
              )}
            </Card>
          </Grid.Col>
        </Grid>
      )}

      {/* -------- modales -------- */}
      <Modal opened={printOpen} onClose={() => setPrintOpen(false)} size="lg"
        title={<Group gap={8}><Printer size={18} /><Text fw={650}>Imprimer</Text></Group>}>
        <Stack gap="lg">
          <div>
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" mb={6} style={{ letterSpacing: 0.5 }}>
              Sujets
            </Text>
            {printableSubjects.length === 0 ? (
              <Text size="sm" c="dimmed">Aucun sujet prêt {cycle !== 'all' && `en ${cycle}`}.</Text>
            ) : (
              <Stack gap={6}>
                {printableSubjects.map((a) => (
                  <Paper key={a.id} withBorder radius="md" p="xs">
                    <Group justify="space-between" wrap="nowrap" gap="xs">
                      <div style={{ minWidth: 0 }}>
                        <Text size="sm" fw={600} truncate>{a.title}</Text>
                        <Group gap={6}>
                          <Text size="xs" c="dimmed">{a.class_name}</Text>
                          <Badge size="xs" variant="dot"
                            color={(STATUS_LABEL[a.status] ?? { color: 'gray' }).color}>
                            {(STATUS_LABEL[a.status] ?? { label: a.status }).label}
                          </Badge>
                        </Group>
                      </div>
                      <Group gap={6} wrap="nowrap">
                        <Button size="xs" variant="subtle" leftSection={<Eye size={14} />}
                          onClick={() => setPreviewId(a.id)}>Aperçu</Button>
                        <PrintButton assessmentId={a.id} file="subject_batch.pdf"
                          label="Imprimer" assessmentDuplex={a.duplex} />
                      </Group>
                    </Group>
                  </Paper>
                ))}
              </Stack>
            )}
          </div>
          <div>
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" mb={6} style={{ letterSpacing: 0.5 }}>
              Copies corrigées (overlay)
            </Text>
            {printableOverlays.length === 0 ? (
              <Text size="sm" c="dimmed">Aucune correction prête à imprimer.</Text>
            ) : (
              <Stack gap={6}>
                {printableOverlays.map((b) => (
                  <Paper key={b.id} withBorder radius="md" p="xs">
                    <Group justify="space-between" wrap="nowrap" gap="xs">
                      <div style={{ minWidth: 0 }}>
                        <Text size="sm" fw={600} truncate>{b.assessment_title}</Text>
                        <Group gap={6}>
                          <Text size="xs" c="dimmed">{b.class_name}</Text>
                          {b.overlay_printed && <Badge size="xs" variant="dot" color="cyan">imprimé</Badge>}
                        </Group>
                      </div>
                      <PrintButton assessmentId={b.assessment_id} file="correction_overlay.pdf"
                        label="Imprimer l'overlay" />
                    </Group>
                  </Paper>
                ))}
              </Stack>
            )}
          </div>
          <Group justify="space-between">
            <Button variant="subtle" size="xs" leftSection={<FileText size={14} />}
              onClick={() => { setPrintOpen(false); navigate('/sujets') }}>
              Tous les sujets
            </Button>
            <Button variant="subtle" size="xs" rightSection={<ArrowRight size={14} />}
              onClick={() => { setPrintOpen(false); navigate('/corrections') }}>
              Corrections
            </Button>
          </Group>
        </Stack>
      </Modal>

      <PdfPreviewModal assessmentId={previewId} opened={!!previewId}
        onClose={() => setPreviewId(null)} />
    </Stack>
  )
}
