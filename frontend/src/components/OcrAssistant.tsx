// Assistant OCRiser : reprise des lectures douteuses, AVANT toute note.
//
// À gauche le scan de l'élève, à droite les lectures des moteurs (Mathpix et
// moteurs locaux) déjà rendues en LaTeX : un clic sur la bonne lecture la
// retient et passe à la suivante. En bas à droite, la saisie libre quand aucun
// moteur n'a lu juste. Mêmes écrans en cascade, en ombre ou en Mathpix seul ;
// même assistant pour le professeur et pour l'Atelier (/atelier/corrections).
//
// Choisir une lecture lue par plusieurs moteurs n'est PAS un vote pour l'un
// d'eux : le serveur enregistre la surface retenue, qui crédite chaque moteur
// l'ayant lue (ocr_truth.record_truth).
//
// Revue rapide : les enregistrements partent en file (dans l'ordre, sans
// bloquer l'écran), les scans des cases suivantes sont préchargés, et ← / →
// parcourent toute la session, cases déjà lues comprises.
import {
  ActionIcon, Badge, Box, Button, Card, Group, Kbd, Modal, Progress, SimpleGrid, Stack, Text,
  Textarea, TextInput, Tooltip, UnstyledButton,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Check, ChevronLeft, ChevronRight, PencilLine } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { api, getToken } from '../api'
import MathText, { MathAnswer } from './MathText'

export type OcrOption = { surface: string; text: string; engines: string[] }
type OcrCell = { index: number; label: string; ocr_text?: string; ocr_options?: OcrOption[] }
type Box4 = { index: number; row?: number | null; col?: number | null; x: number; y: number; w: number; h: number }
export type OcrItem = {
  response_id: string; student: string; student_order: number
  group_key: string; group_label: string; response_type: string
  ocr_confidence: number | null; cell_confidences?: number[]
  ocr_audit?: boolean; ocr_options?: OcrOption[]
  ocr_text?: string | null
  ocr_pending?: boolean; ocr_touched?: (number | 'zone')[]
  selected_choices: number[]; selected_pairs: number[][]
  cells: OcrCell[]
  ocr_controls?: {
    kind: 'text' | 'boxes' | 'matching'
    boxes?: Box4[]
    left?: { index: number; x: number; y: number }[]
    right?: { index: number; x: number; y: number }[]
  }
}
export type OcrBatchRef = { id: string; assessment_title: string; ocr_threshold: number; query: string }

type Unit = { key: string; respId: string; cellIndex: number | null }
type Done = {
  text?: string; choices?: number[]; pairs?: number[][]
  status: 'saving' | 'saved' | 'error'
}

const ENGINE_LABELS: Record<string, string> = {
  mathpix: 'Mathpix', mock: 'Mathpix (démo)', unimumer: 'Uni-MuMER', lighton: 'LightOnOCR',
  apple_vision: 'Apple Vision', trocr: 'TrOCR',
}
const PREFETCH = 6

const isBoxes = (it: OcrItem) => it.response_type.startsWith('qcm') || it.response_type === 'checkbox_grid'
const isMatching = (it: OcrItem) => it.response_type === 'matching'
const isMultiline = (it: OcrItem) => it.response_type === 'multiline_text' || it.response_type === 'composite'

const isTable = (it: OcrItem) => it.response_type === 'table_fill' || it.response_type === 'multi_blank'

/** Cases à lire : celles encore douteuses, plus celles déjà lues par le professeur
 *  pendant cet OCRiser (pour y revenir, même après avoir fermé l'assistant). */
function buildUnits(items: OcrItem[], threshold: number): Unit[] {
  const out: Unit[] = []
  for (const item of items) {
    const pending = item.ocr_pending !== false
    const touched = item.ocr_touched ?? []
    if (isTable(item)) {
      const confs = item.cell_confidences ?? []
      item.cells.forEach((_, i) => {
        const doubtful = pending && (!confs.length || (confs[i] ?? item.ocr_confidence ?? 0) < threshold)
        if (doubtful || touched.includes(i))
          out.push({ key: `${item.response_id}:${i}`, respId: item.response_id, cellIndex: i })
      })
    } else if (pending || touched.length) {
      out.push({ key: `${item.response_id}:-`, respId: item.response_id, cellIndex: null })
    }
  }
  const by = new Map(items.map((it) => [it.response_id, it]))
  const family = (it: OcrItem) => isBoxes(it) || isMatching(it) ? 0 : isMultiline(it) ? 2 : 1
  // même case de réponse à travers les élèves : l'œil reste sur le même genre de réponse
  out.sort((a, b) => {
    const ia = by.get(a.respId)!, ib = by.get(b.respId)!
    return family(ia) - family(ib) || ia.group_key.localeCompare(ib.group_key)
      || (a.cellIndex ?? -1) - (b.cellIndex ?? -1) || ia.student_order - ib.student_order
  })
  return out
}

/** Lectures déjà enregistrées par le professeur (session précédente de l'assistant). */
function doneFromServer(items: OcrItem[], units: Unit[]): Record<string, Done> {
  const by = new Map(items.map((it) => [it.response_id, it]))
  const out: Record<string, Done> = {}
  for (const u of units) {
    const it = by.get(u.respId)!
    const touched = it.ocr_touched ?? []
    if (u.cellIndex != null) {
      if (touched.includes(u.cellIndex))
        out[u.key] = { text: it.cells[u.cellIndex]?.ocr_text ?? '', status: 'saved' }
    } else if (touched.length) {
      out[u.key] = isBoxes(it) ? { choices: [...(it.selected_choices ?? [])], status: 'saved' }
        : isMatching(it) ? { pairs: (it.selected_pairs ?? []).map((p) => [...p]), status: 'saved' }
          : { text: it.ocr_text ?? '', status: 'saved' }
    }
  }
  return out
}

// ------------------------------------------------------------ clavier de saisie
// Symboles insérés tels qu'on les écrit (≈, °, ℝ…), modèles LaTeX avec un curseur
// `|` posé au bon endroit ; Tab saute à la case vide `{}` suivante.
type Key = { label: string; insert: string; tip: string }
const KEY_ROWS: { title: string; keys: Key[] }[] = [
  { title: 'Modèles', keys: [
    { label: 'a/b', insert: '\\frac{|}{}', tip: 'Fraction : numérateur, Tab, dénominateur' },
    { label: 'xⁿ', insert: '^{|}', tip: 'Puissance (exposant)' },
    { label: 'x²', insert: '^{2}', tip: 'Au carré' },
    { label: 'x₁', insert: '_{|}', tip: 'Indice' },
    { label: '√', insert: '\\sqrt{|}', tip: 'Racine carrée' },
    { label: '( )', insert: '(|)', tip: 'Parenthèses' },
    { label: '[ ; ]', insert: '[|;]', tip: 'Intervalle' },
    { label: '|x|', insert: '|||', tip: 'Valeur absolue' },
    { label: 'AB⃗', insert: '\\overrightarrow{|}', tip: 'Vecteur' },
    { label: 'Â', insert: '\\widehat{|}', tip: 'Angle' },
    { label: 'Texte', insert: '\\text{|}', tip: 'Mots dans une formule' },
  ] },
  { title: 'Symboles', keys: [
    { label: '×', insert: '×', tip: 'Multiplié' }, { label: '÷', insert: '÷', tip: 'Divisé' },
    { label: '−', insert: '-', tip: 'Moins' }, { label: '±', insert: '±', tip: 'Plus ou moins' },
    { label: '≈', insert: '≈', tip: 'Environ égal (égal ondulé)' }, { label: '≠', insert: '≠', tip: 'Différent' },
    { label: '≤', insert: '≤', tip: 'Inférieur ou égal' }, { label: '≥', insert: '≥', tip: 'Supérieur ou égal' },
    { label: '°', insert: '°', tip: 'Degré' }, { label: '%', insert: '%', tip: 'Pour cent' },
    { label: 'π', insert: 'π', tip: 'Pi' }, { label: '∞', insert: '∞', tip: 'Infini' },
    { label: '€', insert: '€', tip: 'Euro' },
  ] },
  { title: 'Ensembles et logique', keys: [
    { label: 'ℕ', insert: 'ℕ', tip: 'Entiers naturels' }, { label: 'ℤ', insert: 'ℤ', tip: 'Entiers relatifs' },
    { label: 'ⅅ', insert: '\\mathbb{D}', tip: 'Décimaux' }, { label: 'ℚ', insert: 'ℚ', tip: 'Rationnels' },
    { label: 'ℝ', insert: 'ℝ', tip: 'Réels' }, { label: '∈', insert: '∈', tip: 'Appartient à' },
    { label: '∉', insert: '∉', tip: "N'appartient pas à" }, { label: '⊂', insert: '⊂', tip: 'Inclus dans' },
    { label: '∪', insert: '∪', tip: 'Union' }, { label: '∩', insert: '∩', tip: 'Intersection' },
    { label: '∅', insert: '∅', tip: 'Ensemble vide' }, { label: '⇒', insert: '⇒', tip: 'Implique' },
    { label: '⇔', insert: '⇔', tip: 'Équivaut à' }, { label: '//', insert: '//', tip: 'Parallèle' },
    { label: '⊥', insert: '⊥', tip: 'Perpendiculaire' },
  ] },
  { title: 'Lettres grecques', keys: [
    { label: 'α', insert: 'α', tip: 'alpha' }, { label: 'β', insert: 'β', tip: 'bêta' },
    { label: 'θ', insert: 'θ', tip: 'thêta' }, { label: 'Δ', insert: 'Δ', tip: 'delta' },
  ] },
]

type Field = HTMLInputElement | HTMLTextAreaElement

/** Insère `template` à la place de la sélection ; `|` marque le curseur (la sélection y va). */
function insertAt(el: Field, value: string, template: string): { value: string; caret: number } {
  const a = el.selectionStart ?? value.length, b = el.selectionEnd ?? value.length
  const sel = value.slice(a, b)
  const at = template.indexOf('|')
  const t = at < 0 ? template + sel : template.slice(0, at) + sel + template.slice(at + 1)
  const caret = a + (at < 0 ? t.length : at + sel.length)
  return { value: value.slice(0, a) + t + value.slice(b), caret }
}

/** Tab : place le curseur dans la prochaine accolade vide `{}` (dénominateur…). */
function nextSlot(el: Field): boolean {
  const from = el.selectionEnd ?? 0
  const i = el.value.indexOf('{}', from)
  if (i < 0) return false
  el.setSelectionRange(i + 1, i + 1)
  return true
}

function MathKeyboard({ onKey }: { onKey: (k: Key) => void }) {
  return (
    <Stack gap={6} mt="xs">
      {KEY_ROWS.map((row) => (
        <Group key={row.title} gap={4} wrap="wrap" align="center">
          <Text size="10px" c="dimmed" w={78} style={{ lineHeight: 1.1 }}>{row.title}</Text>
          {row.keys.map((k) => (
            <Tooltip key={k.label} label={k.tip} openDelay={300}>
              <Button size="compact-sm" variant="default" miw={34} px={8}
                styles={{ label: { fontSize: 15 } }}
                // ne vole pas le focus : le curseur reste dans la saisie
                onMouseDown={(e) => e.preventDefault()} onClick={() => onKey(k)}>
                {k.label}
              </Button>
            </Tooltip>
          ))}
        </Group>
      ))}
    </Stack>
  )
}

function scanUrl(u: Unit) {
  return `/api/scans/responses/${u.respId}/scan${u.cellIndex != null ? `?cell=${u.cellIndex}` : ''}`
}

/** Scan de la case, avec les cases à cocher / points de liaison cliquables. */
function ScanView({ url, item, choices, pairs, matchStart, onChoice, onMatchPoint }: {
  url: string | null | undefined; item: OcrItem; choices: number[]; pairs: number[][]
  matchStart: number | null; onChoice: (b: Box4) => void; onMatchPoint: (side: 'left' | 'right', i: number) => void
}) {
  if (url === null) return <Text size="sm" c="dimmed" p="md">Scan indisponible pour cette case.</Text>
  if (!url) return <Text size="sm" c="dimmed" p="md">Chargement du scan…</Text>
  const controls = item.ocr_controls
  const grid = item.response_type === 'checkbox_grid'
  const chosen = (b: Box4) => grid && b.row != null && b.col != null ? choices[b.row] === b.col : choices.includes(b.index)
  const left = controls?.left ?? [], right = controls?.right ?? []
  const point = (side: 'left' | 'right', i: number) => (side === 'left' ? left : right).find((p) => p.index === i)
  // écriture seule (pas de coches à cliquer) : la case est agrandie à la largeur du
  // panneau, une case de tableau minuscule devient lisible d'un coup d'œil
  const zoom = !controls || controls.kind === 'text'
  return (
    <Box style={{ display: zoom ? 'block' : 'inline-block', position: 'relative', maxWidth: '100%' }}>
      <img src={url} alt="Scan de la réponse de l'élève" style={{ display: 'block', maxWidth: '100%',
        ...(zoom ? { width: '100%', height: 'auto' } : {}),
        maxHeight: '58vh', objectFit: 'contain', background: '#fff',
        border: '1px solid var(--mp-border-subtle)', borderRadius: 4 }} />
      {controls?.kind === 'boxes' && (controls.boxes ?? []).map((b) => (
        <button key={`${b.row ?? '-'}:${b.col ?? b.index}`} type="button"
          aria-label={`Coche ${b.index + 1}`} onClick={() => onChoice(b)}
          style={{ position: 'absolute', cursor: 'pointer', padding: 0, minWidth: 12, minHeight: 12,
            left: `${b.x * 100}%`, top: `${b.y * 100}%`,
            width: `${Math.max(b.w, .018) * 100}%`, height: `${Math.max(b.h, .025) * 100}%`,
            border: '2px solid #1971c2', background: chosen(b) ? 'rgba(25, 113, 194, 0.35)' : 'transparent' }} />
      ))}
      {controls?.kind === 'matching' && (
        <>
          <svg aria-hidden style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
            {pairs.map(([l, r], i) => {
              const a = point('left', l), b = point('right', r)
              return a && b ? <line key={i} x1={`${a.x * 100}%`} y1={`${a.y * 100}%`} x2={`${b.x * 100}%`}
                y2={`${b.y * 100}%`} stroke="#fd7e14" strokeWidth="10" strokeOpacity="0.4" strokeLinecap="round" /> : null
            })}
          </svg>
          {[...left.map((p) => ({ ...p, side: 'left' as const })), ...right.map((p) => ({ ...p, side: 'right' as const }))]
            .map((p) => (
              <button type="button" key={`${p.side}:${p.index}`} onClick={() => onMatchPoint(p.side, p.index)}
                aria-label={`Point ${p.side === 'left' ? 'gauche' : 'droit'} ${p.index + 1}`}
                style={{ position: 'absolute', left: `${p.x * 100}%`, top: `${p.y * 100}%`,
                  transform: 'translate(-50%, -50%)', width: 18, height: 18, borderRadius: '50%',
                  cursor: 'pointer', padding: 0, border: '3px solid #fd7e14',
                  background: p.side === 'left' && matchStart === p.index ? 'rgba(253, 126, 20, 0.5)' : 'transparent' }} />
            ))}
        </>
      )}
    </Box>
  )
}

export default function OcrAssistant({ batch, onClose }: { batch: OcrBatchRef | null; onClose: () => void }) {
  const [items, setItems] = useState<OcrItem[]>([])
  const [units, setUnits] = useState<Unit[]>([])
  const [idx, setIdx] = useState(0)                       // units.length = écran de fin
  const [done, setDone] = useState<Record<string, Done>>({})
  const [remaining, setRemaining] = useState<number | null>(null)
  const [manual, setManual] = useState('')
  const [choices, setChoices] = useState<number[]>([])
  const [pairs, setPairs] = useState<number[][]>([])
  const [matchStart, setMatchStart] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [finishing, setFinishing] = useState(false)
  const [, bump] = useState(0)
  const images = useRef(new Map<string, string | null>())  // clé d'unité → URL objet (null = échec)
  const inflight = useRef(new Set<string>())
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const inputRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null)

  // ------------------------------------------------------------ chargement
  useEffect(() => {
    if (!batch) return
    let cancelled = false
    setLoading(true); setDone({}); setIdx(0); setRemaining(null)
    api.get<OcrItem[]>(`/api/scans/batches/${batch.id}/ocr-items${batch.query ? `?${batch.query}` : ''}`)
      .then((rs) => {
        if (cancelled) return
        const us = buildUnits(rs, batch.ocr_threshold || 0.9)
        const d = doneFromServer(rs, us)
        setItems(rs); setUnits(us); setDone(d)
        // reprise : première case encore à lire, ou l'écran de fin si tout est lu
        const first = us.findIndex((u) => !d[u.key])
        setIdx(first < 0 ? us.length : first)
      })
      .catch((e) => { notifications.show({ color: 'red', message: (e as Error).message }); onClose() })
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [batch?.id])

  useEffect(() => () => {                                // fermeture : libère les images
    images.current.forEach((u) => u && URL.revokeObjectURL(u))
    images.current.clear(); inflight.current.clear()
  }, [batch?.id])

  const fetchImage = useCallback((u: Unit) => {
    if (images.current.has(u.key) || inflight.current.has(u.key)) return
    inflight.current.add(u.key)
    fetch(scanUrl(u), { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => { images.current.set(u.key, URL.createObjectURL(b)) })
      .catch(() => { images.current.set(u.key, null) })
      .finally(() => { inflight.current.delete(u.key); bump((n) => n + 1) })
  }, [])

  // préchargement : la case courante, puis les suivantes (et la précédente)
  useEffect(() => {
    for (let i = Math.max(0, idx - 1); i < Math.min(units.length, idx + 1 + PREFETCH); i++) fetchImage(units[i])
  }, [idx, units, fetchImage])

  const unit = units[idx]
  const item = unit ? items.find((x) => x.response_id === unit.respId) ?? null : null
  const cell = item && unit?.cellIndex != null ? item.cells[unit.cellIndex] ?? null : null
  const options: OcrOption[] = (cell ? cell.ocr_options : item?.ocr_options) ?? []
  const mine = unit ? done[unit.key] : undefined
  const answered = units.filter((u) => done[u.key]).length

  // nouvelle case : saisie vide (ou celle déjà faite), coches de l'élève, focus clavier
  useEffect(() => {
    if (!unit || !item) return
    const d = done[unit.key]
    const fromOption = d?.text != null && options.some((o) => o.text === d.text)
    setManual(d?.text != null && !fromOption ? d.text : '')
    setChoices(d?.choices ?? [...(item.selected_choices ?? [])])
    setPairs(d?.pairs ?? (item.selected_pairs ?? []).map((p) => [...p]))
    setMatchStart(null)
    if (!isBoxes(item) && !isMatching(item)) window.setTimeout(() => inputRef.current?.focus(), 0)
  }, [unit?.key, item?.response_id])

  // ------------------------------------------------------------ enregistrement
  function save(u: Unit, it: OcrItem, value: Omit<Done, 'status'>) {
    setDone((d) => ({ ...d, [u.key]: { ...value, status: 'saving' } }))
    const body = isBoxes(it) ? { selected_choices: value.choices }
      : isMatching(it) ? { selected_pairs: value.pairs }
        : { text: value.text ?? '', latex: value.text ?? '', cell_index: u.cellIndex }
    // en file, dans l'ordre : deux cases d'un même tableau ne s'écrasent jamais
    queue.current = queue.current.then(() =>
      api.post<{ remaining: number }>(`/api/scans/responses/${u.respId}/ocr`, body)
        .then((r) => {
          setRemaining(r.remaining)
          setDone((d) => ({ ...d, [u.key]: { ...value, status: 'saved' } }))
        })
        .catch((e) => {
          setDone((d) => ({ ...d, [u.key]: { ...value, status: 'error' } }))
          notifications.show({ color: 'red', title: 'Lecture non enregistrée',
            message: `${it.student} : ${(e as Error).message}. Revenez sur la case pour la valider à nouveau.` })
        }))
    setIdx((i) => Math.min(i + 1, units.length))
  }

  const choose = (text: string) => unit && item && save(unit, item, { text })
  const validateManual = () => { if (manual.trim()) choose(manual) }

  function pressKey(k: Key) {
    const el = inputRef.current
    if (!el) { setManual((m) => m + k.insert.replace('|', '')); return }
    const r = insertAt(el, manual, k.insert)
    setManual(r.value)
    window.setTimeout(() => { el.focus(); el.setSelectionRange(r.caret, r.caret) }, 0)
  }
  const onFieldTab = (e: ReactKeyboardEvent<Field>) => {
    if (e.key === 'Tab' && !e.shiftKey && nextSlot(e.currentTarget)) e.preventDefault()
  }
  const validateMarks = () => unit && item && save(unit, item, isBoxes(item) ? { choices } : { pairs })

  const go = useCallback((delta: number) => setIdx((i) => Math.max(0, Math.min(units.length, i + delta))), [units.length])

  // ← / → : navigation, sauf pour déplacer le curseur dans une saisie non vide
  useEffect(() => {
    if (!batch) return
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      const typing = (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA') && (t as HTMLInputElement).value !== ''
      if (e.key === 'ArrowLeft' && !typing) { e.preventDefault(); go(-1) }
      else if (e.key === 'ArrowRight' && !typing) { e.preventDefault(); go(1) }
      else if (e.key === 'Enter' && item && (isBoxes(item) || isMatching(item))
               && t.tagName !== 'BUTTON') { e.preventDefault(); validateMarks() }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

  async function finish() {
    if (!batch) return
    setFinishing(true)
    try {
      await queue.current
      await api.post(`/api/scans/batches/${batch.id}/ocr/complete`)
      notifications.show({ color: 'blue', message: 'Lectures validées — correction automatique démarrée' })
      onClose()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setFinishing(false)
    }
  }

  function toggleChoice(b: Box4) {
    if (item?.response_type === 'checkbox_grid' && b.row != null && b.col != null) {
      setChoices((prev) => {
        const next = [...prev]
        while (next.length <= b.row!) next.push(-1)
        next[b.row!] = next[b.row!] === b.col ? -1 : b.col!
        return next
      })
    } else {
      setChoices((prev) => prev.includes(b.index) ? prev.filter((x) => x !== b.index) : [...prev, b.index].sort((a, c) => a - c))
    }
  }
  function toggleMatch(side: 'left' | 'right', i: number) {
    if (side === 'left') { setMatchStart((v) => (v === i ? null : i)); return }
    if (matchStart == null) return
    setPairs((prev) => prev.some(([l, r]) => l === matchStart && r === i)
      ? prev.filter(([l, r]) => l !== matchStart || r !== i)
      : [...prev.filter(([l, r]) => l !== matchStart && r !== i), [matchStart, i]])
    setMatchStart(null)
  }

  const errors = units.filter((u) => done[u.key]?.status === 'error').length
  const canFinish = answered === units.length && errors === 0
  const sameGroup = useMemo(() => !item ? [] : units.filter((u) => {
    const it = items.find((x) => x.response_id === u.respId)
    return it?.group_key === item.group_key && u.cellIndex === unit?.cellIndex
  }), [units, items, item?.group_key, unit?.cellIndex])

  // ------------------------------------------------------------ rendu
  const header = (
    <Group justify="space-between" wrap="nowrap" w="100%" pr="md">
      <Group gap="xs" wrap="nowrap">
        <Text fw={650} c="var(--mp-text-blue)">OCRiser</Text>
        <Text fw={500} lineClamp={1}>— {batch?.assessment_title}</Text>
      </Group>
      <Group gap={6} wrap="nowrap">
        <Tooltip label="Précédente (←)">
          <ActionIcon variant="light" size="lg" disabled={idx <= 0} onClick={() => go(-1)} aria-label="Précédente">
            <ChevronLeft size={18} /></ActionIcon>
        </Tooltip>
        <Text size="sm" c="dimmed" w={70} ta="center">
          {units.length ? `${Math.min(idx + 1, units.length)} / ${units.length}` : '—'}
        </Text>
        <Tooltip label="Suivante (→)">
          <ActionIcon variant="light" size="lg" disabled={idx >= units.length} onClick={() => go(1)} aria-label="Suivante">
            <ChevronRight size={18} /></ActionIcon>
        </Tooltip>
      </Group>
    </Group>
  )

  return (
    <Modal opened={!!batch} onClose={onClose} size="min(1280px, 96vw)" title={header}
      styles={{ title: { flex: 1 } }}>
      <Stack gap="sm">
        <Progress value={units.length ? (answered / units.length) * 100 : 0} size="sm" radius="xl"
          aria-label={`${answered} lectures sur ${units.length}`} />
        {loading ? <Text c="dimmed" py="xl" ta="center">Chargement des lectures…</Text>
          : !units.length ? (
              <Stack align="center" py="xl" gap="md">
                <Text c="dimmed">Aucune lecture à vérifier.</Text>
                <Button color="blue" loading={finishing} onClick={finish}>Lancer la correction</Button>
              </Stack>
            )
            : idx >= units.length ? (
              <Stack align="center" py="xl" gap="md">
                <Text fz="xl" fw={650}>{answered === units.length ? 'Toutes les lectures sont faites' : `${answered} lectures faites sur ${units.length}`}</Text>
                <Text c="dimmed" size="sm" ta="center" maw={520}>
                  {errors ? `${errors} lecture${errors > 1 ? 's' : ''} n'a pas pu être enregistrée : revenez dessus avec ←.`
                    : answered < units.length ? 'Les cases passées sans réponse restent à lire : revenez dessus avec ←.'
                      : remaining ? `Il reste ${remaining} lecture${remaining > 1 ? 's' : ''} à faire hors de cette liste.`
                        : 'Vous pouvez encore revenir en arrière avec ← pour corriger une lecture.'}
                </Text>
                <Group>
                  <Button variant="default" leftSection={<ChevronLeft size={16} />} onClick={() => go(-1)}>Revoir</Button>
                  <Button color="blue" loading={finishing} disabled={!canFinish} onClick={finish}>Lancer la correction</Button>
                </Group>
              </Stack>
            ) : item && unit && (
              <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
                {/* ------------------------------------------- gauche : le scan */}
                <Card withBorder padding="sm">
                  <Group justify="space-between" mb="xs" wrap="nowrap">
                    <Group gap={8} wrap="nowrap">
                      <Text fw={650}>{item.student}</Text>
                      {item.ocr_audit && (
                        <Tooltip multiline maw={280} label="Lecture sûre tirée au hasard pour mesurer la fiabilité : cliquez la bonne lecture, même si c'est celle retenue">
                          <Badge color="grape" variant="light">Contrôle</Badge>
                        </Tooltip>
                      )}
                    </Group>
                    {sameGroup.length > 1 && (
                      <Text size="xs" c="dimmed">{sameGroup.findIndex((u) => u.key === unit.key) + 1}/{sameGroup.length} même case</Text>
                    )}
                  </Group>
                  <Text size="xs" c="dimmed" mb={6}>
                    {item.group_label}{cell?.label ? <> — <MathText text={cell.label} /></> : null}
                  </Text>
                  <ScanView url={images.current.get(unit.key)} item={item} choices={choices} pairs={pairs}
                    matchStart={matchStart} onChoice={toggleChoice} onMatchPoint={toggleMatch} />
                </Card>

                {/* ------------------------------------------- droite : les lectures */}
                <Stack gap="sm">
                  {isBoxes(item) || isMatching(item) ? (
                    <Card withBorder padding="md">
                      <Text size="sm" mb="md">
                        {isBoxes(item) ? 'Cliquez les carrés bleus sur le scan : plein = coché, vide = non coché.'
                          : 'Cliquez un point à gauche puis son correspondant à droite. Recliquez une liaison pour la retirer.'}
                      </Text>
                      <Button color="blue" onClick={validateMarks}>Valider et continuer <Kbd ml={8}>Entrée</Kbd></Button>
                    </Card>
                  ) : (
                    <>
                      <Text size="xs" fw={600} c="dimmed" tt="uppercase">Lectures des moteurs — cliquez la bonne</Text>
                      {options.length === 0 && (
                        <Text size="sm" c="dimmed">Aucun moteur n'a proposé de lecture pour cette case.</Text>
                      )}
                      {options.map((o) => {
                        const picked = mine?.text === o.text
                        return (
                          <UnstyledButton key={o.surface} onClick={() => choose(o.text)}
                            aria-label={`Retenir la lecture ${o.surface}`}
                            style={{ display: 'block', borderRadius: 8, padding: '12px 14px',
                              border: `2px solid ${picked ? 'var(--mantine-color-teal-6)' : 'var(--mp-border-subtle)'}`,
                              background: picked ? 'var(--mantine-color-teal-light)' : 'var(--mantine-color-body)' }}>
                            <Group justify="space-between" wrap="nowrap" align="flex-start">
                              <Box fz="1.6rem" style={{ minHeight: 36, overflowX: 'auto' }}>
                                <MathAnswer text={o.text} />
                              </Box>
                              {picked && <Check size={20} color="var(--mantine-color-teal-6)" />}
                            </Group>
                            <Group gap={4} mt={6}>
                              {o.engines.map((e) => (
                                <Badge key={e} size="xs" variant="light" color={e === 'mathpix' ? 'violet' : 'blue'}>
                                  {ENGINE_LABELS[e] ?? e}
                                </Badge>
                              ))}
                            </Group>
                          </UnstyledButton>
                        )
                      })}
                      <Button variant="subtle" color="gray" size="xs" w="fit-content"
                        onClick={() => choose('')}>Rien d'écrit dans la case</Button>

                      <Card withBorder padding="sm" mt="auto" style={{ borderColor: 'var(--mp-border-blue)' }}>
                        <Group gap={6} mb={6}>
                          <PencilLine size={14} />
                          <Text size="xs" fw={600} c="var(--mp-text-blue)" tt="uppercase">Aucune lecture juste ? Écrivez-la</Text>
                        </Group>
                        {isMultiline(item) ? (
                          <Textarea ref={inputRef} autosize minRows={2} value={manual}
                            onChange={(e) => setManual(e.currentTarget.value)}
                            placeholder="Texte ou LaTeX — Maj+Entrée pour une nouvelle ligne"
                            styles={{ input: { fontFamily: 'monospace' } }}
                            onKeyDown={(e) => { onFieldTab(e); if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); validateManual() } }} />
                        ) : (
                          <TextInput ref={inputRef} value={manual} onChange={(e) => setManual(e.currentTarget.value)}
                            placeholder="Ex. 17   ·   3/4   ·   x^2+1   — ou les touches ci-dessous" autoComplete="off" spellCheck={false}
                            styles={{ input: { fontFamily: 'monospace', fontSize: 16 } }}
                            onKeyDown={(e) => { onFieldTab(e); if (e.key === 'Enter') { e.preventDefault(); validateManual() } }} />
                        )}
                        <Group justify="space-between" mt="xs" wrap="nowrap">
                          <Box fz="1.4rem" style={{ minHeight: 30, overflowX: 'auto' }}>
                            {manual.trim() ? <MathAnswer text={manual} /> : <Text size="xs" c="dimmed">Aperçu</Text>}
                          </Box>
                          <Button size="xs" disabled={!manual.trim()} onClick={validateManual}>
                            Valider <Kbd ml={6} size="xs">Entrée</Kbd>
                          </Button>
                        </Group>
                        <MathKeyboard onKey={pressKey} />
                      </Card>
                    </>
                  )}
                </Stack>
              </SimpleGrid>
            )}
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            Un clic sur une lecture la retient et passe à la suivante · <Kbd size="xs">←</Kbd> <Kbd size="xs">→</Kbd> pour naviguer
          </Text>
          <Group gap="sm" wrap="nowrap">
            <Text size="xs" c={errors ? 'red' : 'dimmed'}>
              {answered}/{units.length} lues{errors ? ` · ${errors} en erreur` : ''}
            </Text>
            {units.length > 0 && idx < units.length && (
              <Tooltip label={canFinish ? 'Toutes les lectures sont faites' : `Encore ${units.length - answered} lecture${units.length - answered > 1 ? 's' : ''} à faire`}>
                <Button size="xs" color="blue" variant={canFinish ? 'filled' : 'light'} loading={finishing}
                  disabled={!canFinish} onClick={finish}>Lancer la correction</Button>
              </Tooltip>
            )}
          </Group>
        </Group>
      </Stack>
    </Modal>
  )
}
