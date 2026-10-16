// Modale « Affiner la correction » (§9.5) : le lot est corrigé, l'overlay
// attend d'être imprimé. Le professeur parcourt les copies scannées AVEC leur
// overlay de correction, élève par élève (une page, ou recto-verso côte à
// côte), et corrige une note en réécrivant simplement la réponse de l'élève :
// le serveur la renote et reconsolide la copie (backend services.refine).
// « OK » régénère l'overlay du lot — les autres copies sont rendues à
// l'identique, à la même place dans la pile — puis ferme.
//
// Fluidité : la liste des copies est légère ; chaque copie (pages, réponses
// cliquables) et ses aperçus JPEG se chargent à la demande, et les copies
// voisines sont préchargées — images déjà décodées — pendant que le
// professeur lit la copie courante.
import {
  ActionIcon, Alert, Badge, Box, Button, Center, Checkbox, Divider, Group, Kbd, Loader,
  Modal, NumberInput, ScrollArea, SegmentedControl, Select, Stack, Table, Text,
  TextInput, Tooltip, UnstyledButton,
  type ComboboxItem, type OptionsFilter,
} from '@mantine/core'
import { useElementSize } from '@mantine/hooks'
import { notifications } from '@mantine/notifications'
import {
  ArrowLeft, Check, ChevronLeft, ChevronRight, Columns2, RectangleVertical,
  RefreshCw, Search, X,
} from 'lucide-react'
import {
  useCallback, useEffect, useMemo, useReducer, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import { api, getToken } from '../api'
import MathText, { MathAnswer } from './MathText'

type CopyEntry = { copy_id: string; student: string; note: string | null; points: string | null }
type Rect = { x: number; y: number; w: number; h: number }
type Zone = {
  response_id: string; sequence: number; response_type: string
  points: number; bareme: number; full: boolean; rect: Rect
}
type CopyPage = {
  page_id: string; index: number; page_no: number; version: string
  has_scan: boolean; zones: Zone[]
}
type CopyView = {
  copy_id: string; student: string; note: string | null; points: string | null
  pages: CopyPage[]
}
type RefineStatus = { stale: boolean; rebuilding: boolean; error: string | null; overlay_printed: boolean }
type Overview = RefineStatus & {
  batch_id: string; assessment_id: string; title: string; copies: CopyEntry[]
}
type EditKind = 'qcm' | 'grid' | 'matching' | 'cells' | 'text' | 'points'
type Answer = { selected_choices?: number[]; selected_pairs?: number[][]; cells?: string[]; text?: string }
type Detail = {
  response_id: string; group_label: string; sequence: number; response_type: string
  edit_kind: EditKind; answer: Answer
  expected: { correct?: number[]; pairs?: number[][] } & Record<string, unknown>
  expected_display: string; correction: string
  current_points: number; bareme_points: number; decision_source: string; cancelled: boolean
  reason_code: string
  ocr_text: string; ocr_latex: string
  choices: string[]
  grid: { rows: string[]; correct: (number | null)[]; cols: string[] } | null
  matching: { left: string[]; right: string[]; pairs: number[][] } | null
  cells: { index: number; label: string; expected_display: string
           auto_credit: number | null; teacher_credit: number | null }[]
}
type ApplyResult = RefineStatus & { copy: CopyView | null; uncertain: boolean; reason: string }

// A4 portrait : toutes les pages scannées sont recalées sur ce format.
const PAGE_RATIO = 210 / 297
const PAGE_GAP = 16
const LAYOUT_KEY = 'mathprint:refine-layout'

const fmtPts = (v: number) => (Math.round(v * 1000) / 1000).toString().replace('.', ',')
const sleep = (ms: number) => new Promise((ok) => setTimeout(ok, ms))
const previewKey = (p: CopyPage) => `${p.page_id}:${p.version}`
const previewSrc = (copyId: string, p: CopyPage) =>
  `/api/scans/refine/copies/${copyId}/pages/${p.page_id}/preview?v=${p.version}`

const TYPE_LABELS: Record<string, string> = {
  qcm_single: 'QCM', qcm_multiple: 'QCM', checkbox_grid: 'Grille', matching: 'Liaisons',
  table_fill: 'Tableau', multi_blank: 'Cases', short_text: 'Réponse courte',
  multiline_text: 'Rédaction', composite: 'Rédaction', manual_drawing: 'Tracé',
}

// « Ex. 5a / 5b / 5c » : les sous-questions d'un exercice partagent son
// numéro ; elles sont lettrées dans l'ordre de la copie, comme à l'impression.
function zoneLabels(pages: CopyPage[]): Map<string, string> {
  const bySeq = new Map<number, { z: Zone; page: number }[]>()
  for (const p of pages) {
    for (const z of p.zones) {
      const group = bySeq.get(z.sequence) ?? []
      group.push({ z, page: p.index })
      bySeq.set(z.sequence, group)
    }
  }
  const out = new Map<string, string>()
  for (const [seq, group] of bySeq) {
    group.sort((a, b) => a.page - b.page || a.z.rect.y - b.z.rect.y || a.z.rect.x - b.z.rect.x)
    group.forEach(({ z }, i) => out.set(z.response_id,
      group.length > 1 ? `Ex. ${seq}${String.fromCharCode(97 + i)}` : `Ex. ${seq}`))
  }
  return out
}

function scoreColor(points: number, bareme: number): string {
  if (bareme <= 0) return 'gray'
  if (points >= bareme - 1e-6) return 'green'
  return points > 0 ? 'orange' : 'red'
}

// recherche d'élève insensible à la casse ET aux accents (« elo » → Élodie),
// chaque mot tapé devant figurer dans le nom
const fold = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
const studentFilter: OptionsFilter = ({ options, search }) => {
  const words = fold(search).split(/\s+/).filter(Boolean)
  return options.filter((o) => !('group' in o)
    && words.every((w) => fold((o as ComboboxItem).label).includes(w)))
}

// --------------------------------------------------------------- aperçus

type PreviewJob = {
  key: string; src: string; resolve: (url: string) => void; reject: (e: Error) => void
}

/** Aperçus JPEG authentifiés (fetch + jeton → URL d'objet), décodés AVANT
 *  affichage, mis en cache (LRU) et chargés par une petite file à priorité :
 *  la copie affichée passe devant les préchargements, deux rendus à la fois
 *  au plus pour ne pas saturer le NAS. */
class PreviewLoader {
  private urls = new Map<string, string>()
  private pending = new Map<string, Promise<string>>()
  private queue: PreviewJob[] = []
  private active = 0
  private protectedKeys = new Set<string>()
  private disposed = false

  constructor(private onReady: () => void, private max = 40, private concurrency = 2) {}

  has(key: string): boolean { return this.urls.has(key) }

  get(key: string): string | undefined {
    const url = this.urls.get(key)
    if (url) { this.urls.delete(key); this.urls.set(key, url) }   // LRU : récent
    return url
  }

  load(key: string, src: string, urgent = false): Promise<string> {
    const ready = this.get(key)
    if (ready) return Promise.resolve(ready)
    const inflight = this.pending.get(key)
    if (inflight) {
      if (urgent) {   // déjà en file : remonte en tête
        const i = this.queue.findIndex((j) => j.key === key)
        if (i > 0) this.queue.unshift(...this.queue.splice(i, 1))
      }
      return inflight
    }
    const promise = new Promise<string>((resolve, reject) => {
      const job = { key, src, resolve, reject }
      if (urgent) this.queue.unshift(job)
      else this.queue.push(job)
    })
    this.pending.set(key, promise)
    this.pump()
    return promise
  }

  /** Abandonne les préchargements en file devenus inutiles (navigation). */
  prune(wanted: Set<string>) {
    const dropped = this.queue.filter((j) => !wanted.has(j.key))
    this.queue = this.queue.filter((j) => wanted.has(j.key))
    for (const j of dropped) {
      this.pending.delete(j.key)
      j.reject(new Error('abandonné'))
    }
  }

  /** Pages de la copie affichée : jamais évincées du cache. */
  protect(keys: Set<string>) { this.protectedKeys = keys }

  dispose() {
    this.disposed = true
    for (const j of this.queue) j.reject(new Error('fermé'))
    this.queue = []
    for (const url of this.urls.values()) URL.revokeObjectURL(url)
    this.urls.clear()
    this.pending.clear()
  }

  private pump() {
    while (this.active < this.concurrency && this.queue.length) {
      const job = this.queue.shift()!
      this.active++
      this.fetchJob(job).finally(() => { this.active--; this.pump() })
    }
  }

  private async fetchJob(job: PreviewJob) {
    try {
      const r = await fetch(job.src, { headers: { Authorization: `Bearer ${getToken()}` } })
      if (!r.ok) throw new Error(`Aperçu indisponible (${r.status})`)
      const url = URL.createObjectURL(await r.blob())
      try {
        const img = new Image()
        img.src = url
        await img.decode()     // affichage instantané : décodé hors écran
      } catch { /* l'<img> retentera lui-même */ }
      if (this.disposed) { URL.revokeObjectURL(url); return }
      this.urls.set(job.key, url)
      this.evict()
      job.resolve(url)
      this.onReady()
    } catch (e) {
      job.reject(e as Error)
    } finally {
      this.pending.delete(job.key)
    }
  }

  private evict() {
    for (const [key, url] of this.urls) {
      if (this.urls.size <= this.max) break
      if (this.protectedKeys.has(key)) continue
      this.urls.delete(key)
      URL.revokeObjectURL(url)
    }
  }
}

// --------------------------------------------------------------- page

function PageView({ page, src, stale, width, height, selectedId, changed, labels, onPick }: {
  page: CopyPage; src?: string; stale: boolean; width: number; height: number
  selectedId: string | null; changed: Set<string>; labels: Map<string, string>
  onPick: (responseId: string) => void
}) {
  return (
    <Box style={{ position: 'relative', width, height, flexShrink: 0, background: '#fff',
      boxShadow: '0 1px 4px rgba(0,0,0,.18), 0 8px 24px rgba(0,0,0,.10)', borderRadius: 2 }}>
      {src ? (
        <img src={src} alt={`Page ${page.index + 1} corrigée`} draggable={false}
          style={{ width: '100%', height: '100%', display: 'block', userSelect: 'none' }} />
      ) : (
        <Center h="100%"><Loader size="sm" color="gray" /></Center>
      )}
      {src && stale && (
        <Loader size="xs" color="indigo" style={{ position: 'absolute', top: 10, right: 10 }} />
      )}
      {!page.has_scan && src && (
        <Badge size="xs" color="gray" variant="light"
          style={{ position: 'absolute', bottom: 8, left: 8 }}>scan indisponible</Badge>
      )}
      {page.zones.map((z) => {
        const selected = z.response_id === selectedId
        const edited = changed.has(z.response_id)
        const label = labels.get(z.response_id) ?? `Ex. ${z.sequence}`
        return (
          <Tooltip key={z.response_id} openDelay={350} withArrow
            label={`${label} — ${fmtPts(z.points)}/${fmtPts(z.bareme)} · cliquer pour modifier`}>
            <button type="button" className="mp-refine-zone"
              aria-label={label.replace('Ex.', 'Exercice')}
              onClick={() => onPick(z.response_id)}
              data-selected={selected || undefined} data-edited={edited || undefined}
              style={{ position: 'absolute', left: `${z.rect.x * 100}%`, top: `${z.rect.y * 100}%`,
                width: `${z.rect.w * 100}%`, height: `${z.rect.h * 100}%` }} />
          </Tooltip>
        )
      })}
    </Box>
  )
}

// survol / sélection des zones cliquables (pas de :hover en style inline)
const ZONE_CSS = `
.mp-refine-zone { padding: 0; margin: 0; cursor: pointer; border-radius: 6px;
  background: transparent; border: 2px solid transparent; transition: background .12s, border-color .12s; }
.mp-refine-zone:hover { border-color: var(--mantine-color-indigo-4); background: rgba(76, 110, 245, .06); }
.mp-refine-zone[data-edited] { border: 2px dashed var(--mantine-color-orange-5); }
.mp-refine-zone[data-selected] { border: 3px solid var(--mantine-color-indigo-6); background: rgba(76, 110, 245, .08); }
.mp-refine-zone:focus-visible { outline: 2px solid var(--mantine-color-indigo-6); outline-offset: 2px; }
`

// --------------------------------------------------------------- éditeur

function sameAnswer(a: Answer, b: Answer) {
  return JSON.stringify(a) === JSON.stringify(b)
}

function CreditMark({ credit }: { credit: number | null }) {
  if (credit == null) return null
  if (credit >= 1) return <Check size={15} color="var(--mantine-color-green-7)" />
  if (credit > 0) return <Text size="xs" fw={700} c="orange">½</Text>
  return <X size={15} color="var(--mantine-color-red-7)" />
}

function AnswerEditor({ detail, answer, onChange, onSubmit }: {
  detail: Detail; answer: Answer; onChange: (a: Answer) => void; onSubmit: () => void
}) {
  // note fixée à la main (points, question annulée) : les verdicts par case
  // ne disent plus rien de la note, on ne les montre pas
  const manualScore = detail.reason_code.startsWith('teacher_set')
    || detail.reason_code === 'teacher_cancel_item'
  const submitOnEnter = (e: ReactKeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); onSubmit() }
  }

  if (detail.edit_kind === 'qcm') {
    const chosen = new Set(answer.selected_choices ?? [])
    const correct = new Set(detail.expected.correct ?? [])
    return (
      <Stack gap={8}>
        {detail.choices.map((choice, i) => (
          <Group key={i} gap="xs" wrap="nowrap" align="flex-start">
            <Checkbox checked={chosen.has(i)} mt={2}
              onChange={() => {
                const next = new Set(chosen)
                if (next.has(i)) next.delete(i)
                else next.add(i)
                onChange({ selected_choices: [...next].sort((a, b) => a - b) })
              }}
              label={<MathText text={choice} />} styles={{ body: { alignItems: 'flex-start' } }} />
            {correct.has(i) && <Badge size="xs" color="green" variant="light" style={{ flexShrink: 0 }}>attendu</Badge>}
          </Group>
        ))}
      </Stack>
    )
  }

  if (detail.edit_kind === 'grid' && detail.grid) {
    const grid = detail.grid
    const sel = answer.selected_choices ?? []
    const setRow = (ri: number, col: number) => {
      const next = [...sel]
      while (next.length < grid.rows.length) next.push(-1)
      next[ri] = next[ri] === col ? -1 : col
      onChange({ selected_choices: next })
    }
    return (
      <Table withRowBorders verticalSpacing={4} horizontalSpacing={6} fz="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th />
            {grid.cols.map((c, ci) => <Table.Th key={ci} ta="center"><MathText text={c} /></Table.Th>)}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {grid.rows.map((label, ri) => (
            <Table.Tr key={ri}>
              <Table.Td><MathText text={label} /></Table.Td>
              {grid.cols.map((_, ci) => (
                <Table.Td key={ci} ta="center" style={grid.correct[ri] === ci
                  ? { background: 'var(--mantine-color-green-light)' } : undefined}>
                  <Checkbox checked={sel[ri] === ci} onChange={() => setRow(ri, ci)}
                    aria-label={`Ligne ${ri + 1}, colonne ${ci + 1}`}
                    styles={{ body: { justifyContent: 'center' } }} />
                </Table.Td>
              ))}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    )
  }

  if (detail.edit_kind === 'matching' && detail.matching) {
    const m = detail.matching
    const pairs = answer.selected_pairs ?? []
    const rightOf = (li: number) => pairs.find((p) => p[0] === li)?.[1] ?? null
    const expectedRight = (li: number) => m.pairs.find((p) => p[0] === li)?.[1] ?? null
    // une colonne de droite ne se relie qu'une fois : la choisir pour une autre
    // ligne la retire de la précédente
    const link = (li: number, ri: number) => {
      const current = rightOf(li)
      const rest = pairs.filter((p) => p[0] !== li && p[1] !== ri)
      onChange({ selected_pairs: current === ri ? rest
        : [...rest, [li, ri]].sort((a, b) => a[0] - b[0]) })
    }
    return (
      <Stack gap={10}>
        {m.left.map((label, li) => (
          <div key={li}>
            <Text size="sm" fw={600} mb={4}><MathText text={label} /></Text>
            <Group gap={6}>
              {m.right.map((r, ri) => (
                <Button key={ri} size="compact-sm" radius="xl"
                  variant={rightOf(li) === ri ? 'filled' : 'default'}
                  color={rightOf(li) === ri ? 'indigo' : undefined}
                  style={expectedRight(li) === ri
                    ? { boxShadow: '0 0 0 2px var(--mantine-color-green-5)' } : undefined}
                  onClick={() => link(li, ri)}>
                  <MathAnswer text={r} />
                </Button>
              ))}
            </Group>
          </div>
        ))}
        <Text size="xs" c="dimmed">Contour vert = liaison attendue.</Text>
      </Stack>
    )
  }

  if (detail.edit_kind === 'cells') {
    const cells = answer.cells ?? []
    return (
      <Stack gap={8}>
        {detail.cells.map((c, i) => (
          <div key={i}>
            <Group justify="space-between" gap={6} mb={2} wrap="nowrap">
              <Text size="xs" fw={600} lineClamp={1}><MathText text={c.label} /></Text>
              <Group gap={4} wrap="nowrap">
                <Text size="xs" c="dimmed">attendu</Text>
                <Text size="xs" fw={600}><MathAnswer text={c.expected_display} /></Text>
                {!manualScore && <CreditMark credit={c.teacher_credit ?? c.auto_credit} />}
              </Group>
            </Group>
            <TextInput size="sm" value={cells[i] ?? ''} onKeyDown={submitOnEnter}
              placeholder="case vide"
              rightSection={cells[i]?.trim() ? (
                <Box fz="xs" pr={6} style={{ whiteSpace: 'nowrap' }}><MathAnswer text={cells[i]} /></Box>
              ) : null}
              rightSectionWidth="auto"
              onChange={(e) => {
                const next = [...cells]
                next[i] = e.currentTarget.value
                onChange({ cells: next })
              }} />
          </div>
        ))}
      </Stack>
    )
  }

  if (detail.edit_kind === 'text') {
    return (
      <Stack gap={6}>
        <Group gap={6}>
          <Text size="xs" c="dimmed">Réponse attendue</Text>
          <Text size="sm" fw={700}><MathAnswer text={detail.expected_display} /></Text>
        </Group>
        <TextInput value={answer.text ?? ''} onKeyDown={submitOnEnter} placeholder="réponse vide"
          onChange={(e) => onChange({ text: e.currentTarget.value })} />
        {answer.text?.trim() && (
          <Box fz="lg"><MathAnswer text={answer.text} /></Box>
        )}
      </Stack>
    )
  }

  // raisonnement rédigé, tracé : seule la note se modifie
  return (
    <Stack gap={6}>
      <Text size="xs" c="dimmed" fw={600} tt="uppercase">Lecture de la copie</Text>
      <Box fz="sm"><MathAnswer text={detail.ocr_latex || detail.ocr_text}
        fallback="Aucune lecture (tracé ou réponse illisible)" /></Box>
      {(detail.expected_display || detail.correction) && (
        <>
          <Text size="xs" c="dimmed" fw={600} tt="uppercase" mt={4}>Corrigé</Text>
          <ScrollArea.Autosize mah={160}>
            <Box fz="sm"><MathText text={detail.expected_display || detail.correction} /></Box>
          </ScrollArea.Autosize>
        </>
      )}
    </Stack>
  )
}

function ResponsePanel({ responseId, label, onBack, onApplied }: {
  responseId: string; label?: string; onBack: () => void
  onApplied: (r: ApplyResult, responseId: string) => void
}) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [answer, setAnswer] = useState<Answer>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<{ uncertain: boolean; reason: string } | null>(null)
  const [points, setPoints] = useState<number | ''>('')
  const [showPoints, setShowPoints] = useState(false)

  const load = useCallback(async () => {
    const d = await api.get<Detail>(`/api/scans/refine/responses/${responseId}`)
    setDetail(d)
    setAnswer(d.answer)
    setPoints(d.current_points)
    return d
  }, [responseId])

  useEffect(() => {
    let alive = true
    setDetail(null); setError(null); setFeedback(null); setShowPoints(false)
    load().catch((e) => { if (alive) setError((e as Error).message) })
    return () => { alive = false }
  }, [load])

  const dirty = !!detail && !sameAnswer(answer, detail.answer)

  async function submit(kind: 'answer' | 'points', value?: number) {
    if (!detail || busy) return
    if (kind === 'answer' && !dirty) return
    setBusy(true); setError(null)
    try {
      const r = kind === 'answer'
        ? await api.post<ApplyResult>(`/api/scans/refine/responses/${responseId}/answer`, answer)
        : await api.post<ApplyResult>(`/api/scans/refine/responses/${responseId}/points`, { points: value })
      onApplied(r, responseId)
      setFeedback({ uncertain: r.uncertain, reason: r.reason })
      if (r.uncertain) setShowPoints(true)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // Ctrl/Cmd + Entrée applique depuis n'importe où dans le panneau
  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit('answer') }
  }

  if (error && !detail) return (
    <Stack>
      <Button variant="subtle" size="xs" leftSection={<ArrowLeft size={14} />} onClick={onBack} w="fit-content">
        Tous les exercices
      </Button>
      <Alert color="red" variant="light">{error}</Alert>
    </Stack>
  )
  if (!detail) return <Center py="xl"><Loader size="sm" /></Center>

  const bareme = detail.bareme_points
  const color = scoreColor(detail.current_points, bareme)
  const pointsOnly = detail.edit_kind === 'points'
  return (
    <Stack gap="sm" onKeyDown={onKeyDown}>
      <Group justify="space-between" wrap="nowrap">
        <Button variant="subtle" size="xs" px={4} leftSection={<ArrowLeft size={14} />} onClick={onBack}>
          Tous les exercices
        </Button>
        <Kbd size="xs">Échap</Kbd>
      </Group>
      <Group justify="space-between" wrap="nowrap">
        <Group gap={6} wrap="nowrap">
          <Badge variant="filled" color="indigo">{label ?? detail.group_label}</Badge>
          <Text size="xs" c="dimmed">{TYPE_LABELS[detail.response_type] ?? detail.response_type}</Text>
        </Group>
        <Badge size="lg" variant="light" color={detail.cancelled ? 'gray' : color}>
          {detail.cancelled ? 'annulée' : `${fmtPts(detail.current_points)} / ${fmtPts(bareme)}`}
        </Badge>
      </Group>

      {!pointsOnly && (
        <Text size="xs" c="dimmed">
          Corrigez la réponse telle que l'élève l'a écrite : la note est recalculée
          automatiquement.
        </Text>
      )}
      <AnswerEditor detail={detail} answer={answer} onChange={setAnswer}
        onSubmit={() => submit('answer')} />

      {!pointsOnly && (
        <Group justify="space-between">
          <Button variant="subtle" color="gray" size="xs" disabled={!dirty || busy}
            onClick={() => setAnswer(detail.answer)}>
            Annuler
          </Button>
          <Button size="sm" disabled={!dirty} loading={busy} leftSection={<Check size={15} />}
            onClick={() => submit('answer')}>
            Appliquer <Kbd ml={8} size="xs">Entrée</Kbd>
          </Button>
        </Group>
      )}

      {feedback && !busy && (
        feedback.uncertain ? (
          <Alert color="orange" variant="light" p="xs">
            <Text size="xs">
              Le moteur ne reconnaît pas cette réponse comme juste ({feedback.reason}) :
              elle compte faux. Si elle est correcte, attribuez les points ci-dessous.
            </Text>
          </Alert>
        ) : (
          <Text size="xs" c="var(--mp-text-green)" fw={600}>
            ✓ Note recalculée : {fmtPts(detail.current_points)} / {fmtPts(bareme)}
          </Text>
        )
      )}
      {error && <Alert color="red" variant="light" p="xs"><Text size="xs">{error}</Text></Alert>}

      <Divider />
      {pointsOnly || showPoints ? (
        <Stack gap={6}>
          <Text size="xs" c="dimmed" fw={600} tt="uppercase">Points attribués</Text>
          <Group gap={6}>
            {[0, 0.5, 1].map((r) => (
              <Button key={r} size="compact-sm" variant="light" disabled={busy}
                color={r === 0 ? 'red' : r === 1 ? 'green' : 'orange'}
                onClick={() => submit('points', r * bareme)}>
                {r === 0 ? '0' : r === 1 ? `Tout (${fmtPts(bareme)})` : fmtPts(bareme / 2)}
              </Button>
            ))}
          </Group>
          <Group gap={6} wrap="nowrap">
            <NumberInput size="sm" w={110} min={0} max={bareme} step={0.25} decimalScale={3}
              decimalSeparator="," value={points} disabled={busy}
              onChange={(v) => setPoints(v === '' ? '' : Number(v))}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && points !== '') { e.preventDefault(); submit('points', Number(points)) }
              }} />
            <Text size="sm" c="dimmed">/ {fmtPts(bareme)}</Text>
            <Button size="sm" variant="light" disabled={points === '' || busy}
              onClick={() => submit('points', Number(points))}>
              Attribuer
            </Button>
          </Group>
        </Stack>
      ) : (
        <Button variant="subtle" color="gray" size="xs" w="fit-content" onClick={() => setShowPoints(true)}>
          Ajuster les points à la main…
        </Button>
      )}
    </Stack>
  )
}

// --------------------------------------------------------------- modale

export default function RefineCorrectionModal({ batchId, opened, onClose }: {
  batchId: string | null; opened: boolean; onClose: () => void
}) {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  const [pageIdx, setPageIdx] = useState(0)
  const [layout, setLayout] = useState<'single' | 'spread'>(() => {
    try { return localStorage.getItem(LAYOUT_KEY) === 'single' ? 'single' : 'spread' } catch { return 'spread' }
  })
  const [selected, setSelected] = useState<string | null>(null)
  const [status, setStatus] = useState<RefineStatus>({ stale: false, rebuilding: false, error: null, overlay_printed: false })
  const [changed, setChanged] = useState<Set<string>>(new Set())
  const [regenerating, setRegenerating] = useState(false)
  const [closing, setClosing] = useState(false)
  const [confirmClose, setConfirmClose] = useState(false)
  const [viewErrors, setViewErrors] = useState<Record<string, string>>({})
  const [, rerender] = useReducer((x: number) => x + 1, 0)

  const views = useRef(new Map<string, CopyView>())
  const viewRequests = useRef(new Map<string, Promise<CopyView>>())
  // page_id -> dernière image prête (affichée pendant le rendu de la suivante)
  const lastShown = useRef(new Map<string, { key: string; url: string }>())
  const loader = useRef<PreviewLoader | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const { ref: viewerRef, width: viewerW, height: viewerH } = useElementSize()

  // (ré)ouverture : état neuf, cache d'aperçus neuf
  useEffect(() => {
    if (!opened || !batchId) return
    const l = new PreviewLoader(rerender)
    loader.current = l
    views.current = new Map()
    viewRequests.current = new Map()
    lastShown.current = new Map()
    setOverview(null); setLoadError(null); setIdx(0); setPageIdx(0); setSelected(null)
    setChanged(new Set()); setViewErrors({}); setConfirmClose(false)
    api.get<Overview>(`/api/scans/batches/${batchId}/refine`)
      .then((o) => {
        setOverview(o)
        setStatus({ stale: o.stale, rebuilding: o.rebuilding, error: o.error, overlay_printed: o.overlay_printed })
      })
      .catch((e) => setLoadError((e as Error).message))
    return () => { l.dispose(); if (loader.current === l) loader.current = null }
  }, [opened, batchId])

  const ensureView = useCallback((copyId: string): Promise<CopyView> => {
    const have = views.current.get(copyId)
    if (have) return Promise.resolve(have)
    let req = viewRequests.current.get(copyId)
    if (!req) {
      req = api.get<CopyView>(`/api/scans/refine/copies/${copyId}`)
        .then((v) => {
          views.current.set(copyId, v)
          setViewErrors((m) => { const { [copyId]: _, ...rest } = m; return rest })
          rerender()
          return v
        })
        .catch((e) => {
          setViewErrors((m) => ({ ...m, [copyId]: (e as Error).message }))
          throw e
        })
        .finally(() => viewRequests.current.delete(copyId))
      viewRequests.current.set(copyId, req)
    }
    return req
  }, [])

  const copies = overview?.copies ?? []
  const entry = copies[idx] ?? null
  const view = entry ? views.current.get(entry.copy_id) ?? null : null
  const pages = view?.pages ?? []
  const spread = layout === 'spread' && pages.length > 1
  const step = spread ? 2 : 1
  const firstShown = spread ? pageIdx - (pageIdx % 2) : pageIdx
  const shownPages = pages.slice(firstShown, firstShown + step)

  // Copie courante d'abord (images en tête de file), puis les voisines dans
  // l'ordre où le professeur va probablement aller : suivante, précédente…
  useEffect(() => {
    if (!opened || !copies.length) return
    let cancelled = false
    const order = [idx, idx + 1, idx - 1, idx + 2, idx + 3].filter((i) => i >= 0 && i < copies.length)
    const wanted = new Set<string>()
    ;(async () => {
      for (const [rank, i] of order.entries()) {
        try {
          const v = await ensureView(copies[i].copy_id)
          if (cancelled) return
          const keys = new Set<string>()
          for (const p of v.pages) {
            wanted.add(previewKey(p)); keys.add(previewKey(p))
            loader.current?.load(previewKey(p), previewSrc(v.copy_id, p), rank === 0).catch(() => {})
          }
          if (rank === 0) {
            for (const p of v.pages) {
              const shown = lastShown.current.get(p.page_id)
              if (shown) keys.add(shown.key)
            }
            loader.current?.protect(keys)
          }
        } catch { /* erreur affichée pour la copie courante */ }
      }
      if (!cancelled) loader.current?.prune(wanted)
    })()
    return () => { cancelled = true }
    // `view` : nouvelles versions de page après une modification
  }, [opened, copies, idx, ensureView, view])

  // image affichée : la version courante dès qu'elle est prête, sinon la
  // précédente de la même page (après une modification) avec un indicateur
  function imageFor(p: CopyPage): { src?: string; stale: boolean } {
    const key = previewKey(p)
    const ready = loader.current?.get(key)
    if (ready) {
      lastShown.current.set(p.page_id, { key, url: ready })
      return { src: ready, stale: false }
    }
    const shown = lastShown.current.get(p.page_id)
    return { src: shown && loader.current?.has(shown.key) ? shown.url : undefined, stale: true }
  }

  const goTo = useCallback((i: number) => {
    if (!copies.length) return
    const next = Math.max(0, Math.min(copies.length - 1, i))
    setIdx(next); setPageIdx(0); setSelected(null)
  }, [copies.length])

  function pickZone(responseId: string) {
    const p = pages.findIndex((pg) => pg.zones.some((z) => z.response_id === responseId))
    if (p >= 0 && (p < firstShown || p >= firstShown + step)) setPageIdx(p)
    setSelected(responseId)
  }

  function onApplied(r: ApplyResult, responseId: string) {
    setStatus({ stale: r.stale, rebuilding: r.rebuilding, error: r.error, overlay_printed: r.overlay_printed })
    setChanged((s) => new Set(s).add(responseId))
    if (r.copy) {
      const fresh = r.copy
      views.current.set(fresh.copy_id, fresh)
      setOverview((o) => o && {
        ...o, copies: o.copies.map((c) => c.copy_id === fresh.copy_id
          ? { ...c, note: fresh.note, points: fresh.points } : c),
      })
      rerender()
    }
  }

  // Régénère l'overlay du lot (tâche de fond côté serveur) et rend la main dès
  // que l'overlay IMPRIMABLE est à jour — l'aperçu PDF « copie + overlay »
  // finit de se rendre ensuite, sans faire attendre le professeur. Une
  // modification arrivée pendant le rendu relance un tour.
  const regenerate = useCallback(async (): Promise<boolean> => {
    if (!batchId) return false
    setRegenerating(true)
    try {
      for (let round = 0; round < 4; round++) {
        let st = await api.post<RefineStatus>(`/api/scans/batches/${batchId}/refine/regenerate`)
        setStatus(st)
        const deadline = Date.now() + 5 * 60_000
        while (st.stale && st.rebuilding && Date.now() < deadline) {
          await sleep(600)
          st = await api.get<RefineStatus>(`/api/scans/batches/${batchId}/refine/status`)
          setStatus(st)
        }
        if (!st.stale) {
          notifications.show({ color: 'green', message: 'Overlay régénéré — prêt à imprimer' })
          return true
        }
        if (st.rebuilding) throw new Error('La régénération continue en arrière-plan — réessayez dans un instant.')
        if (st.error) throw new Error(st.error)
        // rendu terminé mais une correction est arrivée entre-temps : nouveau tour
      }
      throw new Error("L'overlay n'a pas pu être mis à jour — réessayez.")
    } catch (e) {
      notifications.show({ color: 'red', autoClose: 8000, message: (e as Error).message })
      return false
    } finally {
      setRegenerating(false)
    }
  }, [batchId])

  async function ok() {
    if (closing) return
    if (status.stale || status.rebuilding) {
      setClosing(true)
      const done = await regenerate()
      setClosing(false)
      if (!done) return
    }
    onClose()
  }

  function requestClose() {
    if (closing) return
    if (status.stale) setConfirmClose(true)
    else onClose()
  }

  // clavier : ←/→ élève, ↑/↓ page, / ou Ctrl+K recherche, Échap désélectionne
  useEffect(() => {
    if (!opened) return
    const h = (e: KeyboardEvent) => {
      if (confirmClose) return
      const t = e.target as HTMLElement
      const typing = t.tagName === 'TEXTAREA' || t.isContentEditable || (t.tagName === 'INPUT'
        && !['checkbox', 'radio', 'button'].includes((t as HTMLInputElement).type))
      if (e.key === 'Escape') {
        e.preventDefault()
        if (typing) t.blur()
        else if (selected) setSelected(null)
        else requestClose()
        return
      }
      if (typing) return
      if (e.key === '/' || (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey))) {
        e.preventDefault(); searchRef.current?.focus(); return
      }
      if (e.key === 'ArrowRight') { e.preventDefault(); goTo(idx + 1) }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); goTo(idx - 1) }
      else if (e.key === 'ArrowDown' || e.key === 'PageDown') {
        e.preventDefault()
        if (firstShown + step < pages.length) setPageIdx(firstShown + step)
      } else if (e.key === 'ArrowUp' || e.key === 'PageUp') {
        e.preventDefault()
        if (firstShown > 0) setPageIdx(Math.max(0, firstShown - step))
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

  function changeLayout(v: string) {
    const next = v === 'single' ? 'single' : 'spread'
    setLayout(next)
    try { localStorage.setItem(LAYOUT_KEY, next) } catch { /* stockage indisponible */ }
  }

  // pages ajustées à la zone d'affichage (pleine hauteur, ou deux côte à côte)
  const n = Math.max(1, shownPages.length)
  const availW = Math.max(0, viewerW - 32 - (n - 1) * PAGE_GAP)
  const availH = Math.max(0, viewerH - 32)
  const pageH = Math.floor(Math.min(availH, (availW / n) / PAGE_RATIO))
  const pageW = Math.floor(pageH * PAGE_RATIO)

  const searchData = useMemo(() => copies.map((c) => ({
    value: c.copy_id, label: c.note ? `${c.student} — ${c.note}` : c.student,
  })), [copies])
  const labels = useMemo(() => zoneLabels(pages), [pages])
  const allZones = useMemo(() => pages.flatMap((p) => p.zones)
    .sort((a, b) => a.sequence - b.sequence
      || (labels.get(a.response_id) ?? '').localeCompare(labels.get(b.response_id) ?? '')),
  [pages, labels])
  const currentError = entry ? viewErrors[entry.copy_id] : undefined
  const busy = closing || regenerating

  return (
    <Modal opened={opened} onClose={requestClose} fullScreen withCloseButton={false}
      closeOnEscape={false} padding={0} transitionProps={{ duration: 0 }}
      styles={{ body: { height: '100dvh', padding: 0 } }}>
      <style>{ZONE_CSS}</style>
      <Box style={{ height: '100dvh', display: 'flex', flexDirection: 'column' }}>
        {/* barre du haut : sujet, recherche, navigation, overlay */}
        <Group justify="space-between" wrap="nowrap" gap="sm" px="md" py={8}
          style={{ borderBottom: '1px solid var(--mp-border-subtle)', flexShrink: 0 }}>
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
            <Text fw={700} style={{ whiteSpace: 'nowrap' }}>Affiner la correction</Text>
            <Text c="dimmed" lineClamp={1} size="sm">{overview?.title}</Text>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Select ref={searchRef} w={260} size="sm" searchable placeholder="Rechercher un élève…"
              leftSection={<Search size={14} />} rightSection={<Kbd size="xs">/</Kbd>}
              data={searchData} filter={studentFilter} value={entry?.copy_id ?? null}
              nothingFoundMessage="Aucun élève" maxDropdownHeight={420} allowDeselect={false}
              comboboxProps={{ zIndex: 400 }}
              // taper remplace le nom affiché ; Entrée prend le premier élève trouvé
              // (Mantine perd parfois l'option surlignée quand le filtrage suit
              // de près l'ouverture du menu : on ne dépend pas de lui). Une
              // option choisie aux flèches reste prioritaire : Mantine la valide
              // juste après ce gestionnaire.
              onFocus={(e) => e.currentTarget.select()} selectFirstOptionOnChange
              onKeyDown={(e) => {
                if (e.key !== 'Enter') return
                const words = fold(e.currentTarget.value).split(/\s+/).filter(Boolean)
                const i = searchData.findIndex((o) => words.every((w) => fold(o.label).includes(w)))
                if (i < 0) return
                e.preventDefault()
                if (i !== idx) goTo(i)
                e.currentTarget.blur()
              }}
              onChange={(v) => {
                const i = copies.findIndex((c) => c.copy_id === v)
                if (i >= 0) goTo(i)
                searchRef.current?.blur()
              }} />
            <ActionIcon variant="default" size="lg" disabled={idx <= 0} onClick={() => goTo(idx - 1)}
              aria-label="Copie précédente"><ChevronLeft size={18} /></ActionIcon>
            <Text size="sm" c="dimmed" style={{ minWidth: 56, textAlign: 'center' }}>
              {copies.length ? `${idx + 1} / ${copies.length}` : '—'}
            </Text>
            <ActionIcon variant="default" size="lg" disabled={idx >= copies.length - 1}
              onClick={() => goTo(idx + 1)} aria-label="Copie suivante"><ChevronRight size={18} /></ActionIcon>
            <SegmentedControl size="xs" value={layout} onChange={changeLayout} data={[
              { value: 'single', label: <Tooltip label="Une page"><RectangleVertical size={15} /></Tooltip> },
              { value: 'spread', label: <Tooltip label="Recto-verso côte à côte"><Columns2 size={15} /></Tooltip> },
            ]} />
          </Group>
          <Group gap="xs" wrap="nowrap">
            {status.stale ? (
              <Badge color="orange" variant="light">overlay à régénérer</Badge>
            ) : (
              <Badge color="green" variant="light">overlay à jour</Badge>
            )}
            <Button size="sm" variant="light" leftSection={<RefreshCw size={14} />}
              loading={regenerating && !closing} disabled={!status.stale || busy}
              onClick={() => { regenerate() }}>
              Régénérer l'overlay
            </Button>
            <Button size="sm" loading={closing} onClick={ok}>OK</Button>
            <ActionIcon variant="subtle" color="gray" size="lg" onClick={requestClose}
              aria-label="Fermer" disabled={closing}><X size={18} /></ActionIcon>
          </Group>
        </Group>

        <Box style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          {/* zone d'affichage des pages */}
          <Box ref={viewerRef} data-autofocus tabIndex={-1} style={{ flex: 1, minWidth: 0,
            position: 'relative', outline: 'none',
            background: 'var(--mp-surface-sunken)', display: 'flex', alignItems: 'center',
            justifyContent: 'center', gap: PAGE_GAP, padding: 16, overflow: 'hidden' }}>
            {loadError ? (
              <Alert color="red" variant="light" maw={480}>{loadError}</Alert>
            ) : !overview ? (
              <Loader />
            ) : !copies.length ? (
              <Text c="dimmed">Aucune copie scannée à affiner dans ce lot.</Text>
            ) : currentError && !view ? (
              <Stack align="center">
                <Alert color="red" variant="light" maw={480}>{currentError}</Alert>
                <Button size="xs" variant="light" onClick={() => entry && ensureView(entry.copy_id).catch(() => {})}>
                  Réessayer
                </Button>
              </Stack>
            ) : !view ? (
              <Loader />
            ) : !pages.length ? (
              <Text c="dimmed">Aucune page corrigée pour cette copie.</Text>
            ) : pageH > 0 && shownPages.map((p) => {
              const img = imageFor(p)
              return (
                <PageView key={p.page_id} page={p} src={img.src} stale={img.stale}
                  width={pageW} height={pageH} selectedId={selected} changed={changed}
                  labels={labels} onPick={pickZone} />
              )
            })}
            {pages.length > step && (
              <Group gap={4} style={{ position: 'absolute', bottom: 10, left: '50%',
                transform: 'translateX(-50%)' }}>
                {Array.from({ length: Math.ceil(pages.length / step) }, (_, k) => (
                  <Button key={k} size="compact-xs" radius="xl"
                    variant={firstShown === k * step ? 'filled' : 'default'}
                    onClick={() => setPageIdx(k * step)}>
                    {step === 2 ? `Pages ${k * 2 + 1}-${Math.min(pages.length, k * 2 + 2)}` : `Page ${k + 1}`}
                  </Button>
                ))}
              </Group>
            )}
          </Box>

          {/* panneau : la copie, ses exercices, l'éditeur de la réponse choisie */}
          <Box style={{ width: 400, flexShrink: 0, borderLeft: '1px solid var(--mp-border-subtle)',
            display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <Box px="md" pt="md" pb="sm" style={{ flexShrink: 0 }}>
              <Group justify="space-between" wrap="nowrap">
                <Text fw={700} size="lg" lineClamp={1}>{entry?.student ?? ''}</Text>
                {entry?.note && <Badge size="lg" variant="filled" color="red">{entry.note}</Badge>}
              </Group>
              {entry?.points && <Text size="xs" c="dimmed">{entry.points} points</Text>}
            </Box>
            <Divider />
            <ScrollArea style={{ flex: 1 }} px="md" py="sm">
              {selected ? (
                <ResponsePanel key={selected} responseId={selected} label={labels.get(selected)}
                  onBack={() => setSelected(null)} onApplied={onApplied} />
              ) : (
                <Stack gap={4}>
                  <Text size="xs" c="dimmed" mb={4}>
                    Cliquez une réponse sur la copie (ou ci-dessous) pour la corriger.
                  </Text>
                  {allZones.map((z) => (
                    <UnstyledButton key={z.response_id} onClick={() => pickZone(z.response_id)}
                      px={8} py={6} style={{ borderRadius: 6, border: '1px solid var(--mp-border-subtle)' }}>
                      <Group justify="space-between" wrap="nowrap">
                        <Group gap={8} wrap="nowrap">
                          <Badge size="sm" variant="light" color="indigo">
                            {labels.get(z.response_id) ?? `Ex. ${z.sequence}`}
                          </Badge>
                          <Text size="sm">{TYPE_LABELS[z.response_type] ?? z.response_type}</Text>
                          {changed.has(z.response_id) && (
                            <Badge size="xs" variant="dot" color="orange">modifié</Badge>
                          )}
                        </Group>
                        <Text size="sm" fw={600} c={scoreColor(z.points, z.bareme)}>
                          {fmtPts(z.points)} / {fmtPts(z.bareme)}
                        </Text>
                      </Group>
                    </UnstyledButton>
                  ))}
                </Stack>
              )}
            </ScrollArea>
            <Divider />
            <Text size="xs" c="dimmed" px="md" py={8} style={{ flexShrink: 0 }}>
              <Kbd size="xs">←</Kbd> <Kbd size="xs">→</Kbd> élève · <Kbd size="xs">↑</Kbd> <Kbd size="xs">↓</Kbd> page
              · <Kbd size="xs">/</Kbd> recherche
            </Text>
          </Box>
        </Box>
      </Box>

      <Modal opened={confirmClose} onClose={() => setConfirmClose(false)} zIndex={400} centered
        title={<Text fw={650}>Overlay pas encore régénéré</Text>}>
        <Stack>
          <Text size="sm">
            Les corrections modifiées ne figurent pas encore sur l'overlay à imprimer.
            Tant qu'il n'est pas régénéré, son impression est bloquée.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => { setConfirmClose(false); onClose() }}>
              Fermer sans régénérer
            </Button>
            <Button onClick={() => { setConfirmClose(false); ok() }}>Régénérer et fermer</Button>
          </Group>
        </Stack>
      </Modal>
    </Modal>
  )
}
