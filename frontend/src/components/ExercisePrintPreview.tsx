// Aperçu partagé d'un exercice tel qu'il apparaît sur une copie.
//
// Ce composant est volontairement indépendant des écrans Banque / Exercices /
// assistant : le cadre ne contient que l'énoncé, la figure et la zone de
// réponse, comme le PDF. Les badges de gestion sont fournis par le parent et
// restent donc toujours à l'extérieur du cadre imprimé.
import { Badge, Box, Group, Loader, Stack, Text } from '@mantine/core'
import { BookOpen, Lightbulb } from 'lucide-react'
import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { parseBlocks, stripBold, type RichBlock } from '../utils/richblocks'
import { getToken } from '../api'
import MathText from './MathText'

export type PrintableExercise = {
  statement: string
  title?: string
  badge_type?: string
  difficulty?: number
  level?: number
  kind?: string
  is_problem?: boolean
  response_type: string
  expected?: Record<string, any> | null
  choices?: string[] | null
  grading?: Record<string, any> | null
  row_labels?: string[] | null
  col_labels?: string[] | null
  lines?: number | null
  figure_url?: string | null
  figure?: Record<string, any> | null
  correction_solution?: string | null
  correction_guide?: string | null
  calculator?: string | null
}

type PreviewProps = {
  exercise: PrintableExercise
  color?: string
  badges?: ReactNode
  actions?: ReactNode
  beforeFrame?: ReactNode
  afterFrame?: ReactNode
  showCorrection?: boolean
  showGuide?: boolean
  // Coche la (les) bonne(s) réponse(s) sur la carte elle-même (QCM, grille,
  // points à relier) — utile en RELECTURE (onglet Exercices) pour vérifier
  // d'un coup d'œil que la réponse attendue est la bonne, jamais sur une
  // copie destinée à l'élève (Banque, mise en page d'un sujet).
  showAnswers?: boolean
  // Encadrés guide « {{aide}} » intégrés à l'énoncé : affichés par défaut
  // (sujet « Inclure les guides »), masqués à false.
  guides?: boolean
  className?: string
}

/** Encadré GUIDE intégré à l'énoncé — même présentation qu'à l'impression
 *  (pdfgen._draw_guide_block) : fond jaune clair, liseré ambre, ampoule. */
function GuideBox({ text }: { text: string }) {
  return (
    <Group gap={6} align="flex-start" wrap="nowrap" my={4} style={{
      background: '#FFF4C2', border: '1px solid #E4B42D', borderRadius: 5,
      padding: '4px 7px', color: '#4D3B05',
    }}>
      <Lightbulb size={15} color="#C99A12" style={{ flex: '0 0 auto', marginTop: 1 }} />
      <Box style={{ flex: 1, minWidth: 0, fontSize: '0.95em' }}>
        {text.split('\n').map((ln, i) => <Box key={i}><MathText text={ln} /></Box>)}
      </Box>
    </Group>
  )
}

const SUBLABEL_RE = /^([a-h]|\d{1,2})[.)]\s+/
const BULLET_RE = /^[•–—-]\s+/
/** Tableau de données d'un énoncé (cf. backend services/blocks) : colonnes
 *  ajustées au contenu, cellules centrées horizontalement ET verticalement,
 *  en-tête en gras sur fond léger — la même présentation qu'à l'impression.
 *  Le tableau est centré, et défile horizontalement s'il est trop large pour
 *  la carte plutôt que de déborder. */
function StatementTable({ block }: { block: Extract<RichBlock, { kind: 'table' }> }) {
  const rule = '1px solid var(--mantine-color-gray-4)'
  const cell = {
    border: rule, padding: '3px 6px', textAlign: 'center' as const,
    verticalAlign: 'middle' as const, lineHeight: 1.25,
  }
  return (
    <Box my={6} style={{ overflowX: 'auto' }}>
      <Box style={{ display: 'flex', justifyContent: 'center', minWidth: 'min-content' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: '0.92em' }}>
          <tbody>
            {block.rows.map((row, r) => (
              <tr key={r}>
                {row.map((value, c) => (block.header && r === 0 ? (
                  <th key={c} style={{
                    ...cell, fontWeight: 700,
                    background: 'var(--mantine-color-gray-1)',
                  }}><MathText text={stripBold(value)} /></th>
                ) : (
                  <td key={c} style={cell}><MathText text={value} /></td>
                )))}
              </tr>
            ))}
          </tbody>
        </table>
      </Box>
    </Box>
  )
}

/** Longueur APPARENTE d'une valeur (les délimiteurs `$` ne s'affichent pas). */
const stripMathLength = (value: string) => value.replace(/\$/g, '').length

/** Série de valeurs : la même grille qu'un tableau, SANS filets. Le nombre de
 *  colonnes suit la règle de l'impression — le plus grand qui tienne, puis
 *  RÉÉQUILIBRÉ sur le nombre de lignes obtenu, pour qu'une dernière ligne ne
 *  reste pas seule avec une valeur (cf. pdfgen._series_entry). */
function StatementSeries({ items }: { items: string[] }) {
  const longest = Math.max(...items.map((v) => stripMathLength(v)))
  const perRow = Math.max(1, Math.floor(38 / (longest + 2)))
  const fit = Math.min(items.length, perRow)
  const rows = Math.max(1, Math.ceil(items.length / fit))
  const columns = Math.max(1, Math.ceil(items.length / rows))
  return (
    <Box my={5} style={{
      display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
      rowGap: 4, columnGap: 6, justifyItems: 'center', alignItems: 'center',
    }}>
      {items.map((value, index) => <MathText key={index} text={value} />)}
    </Box>
  )
}

/** Même convention visuelle que le PDF : les sous-questions sont détachées du
 * corps, les marqueurs {{blank}}, {{mini}} et {{blank_right}} sont transformés
 * en champs par MathText, et les tableaux/séries reçoivent leur géométrie
 * propre (cf. utils/richblocks, miroir de backend services/blocks). */
export function ExerciseRichBody({ text, color = 'indigo', size }: {
  text: string; color?: string; size?: string | number
}) {
  const blocks = parseBlocks(text || '')
  const sizeOf = (line: string) =>
    (/\{\{(blank(_right)?|mini)\}\}/.test(line) ? '1.12em' : size)
  const badge = (label: string) => (
    <Badge color={color} radius="sm" size="sm" variant="filled"
      style={{ flex: '0 0 auto', marginTop: 2 }}>{label}</Badge>
  )
  return (
    <Box fz={size}>
      {blocks.map((block, index) => {
        if (block.kind === 'guide') return <GuideBox key={index} text={block.text} />
        if (block.kind === 'table') return <StatementTable key={index} block={block} />
        if (block.kind === 'series') {
          const grid = <StatementSeries items={block.items} />
          if (!block.label) return <Box key={index}>{grid}</Box>
          return (
            <Group key={index} gap={6} align="flex-start" wrap="nowrap" mt={index ? 4 : 0}>
              {badge(block.label)}
              <Box style={{ flex: 1, minWidth: 0 }}>{grid}</Box>
            </Group>
          )
        }
        const line = block.text
        const lineSize = sizeOf(line)
        const label = line.match(SUBLABEL_RE)
        if (label) {
          return (
            <Group key={index} gap={6} align="flex-start" wrap="nowrap" mt={index ? 4 : 0}>
              {badge(label[1])}
              <Box style={{ flex: 1, minWidth: 0 }}>
                <MathText text={line.slice(label[0].length)} size={lineSize} />
              </Box>
            </Group>
          )
        }
        const bullet = line.match(BULLET_RE)
        if (bullet) {
          return (
            <Group key={index} gap={6} align="flex-start" wrap="nowrap" mt={index ? 3 : 0}>
              <Text component="span" fw={900} style={{
                flex: '0 0 auto', lineHeight: 1.35,
                color: `var(--mantine-color-${color}-6)`,
              }}>•</Text>
              <Box style={{ flex: 1, minWidth: 0 }}>
                <MathText text={line.slice(bullet[0].length)} size={lineSize} />
              </Box>
            </Group>
          )
        }
        return <Box key={index} mt={index ? 2 : 0}><MathText text={line} size={lineSize} /></Box>
      })}
    </Box>
  )
}

/** Le PDF est l'autorité de mise en page, y compris dans les deux banques.
 * Chargement à l'approche de la carte ; les réponses se superposent aux mêmes
 * coordonnées que les zones de correction, sans modifier la géométrie. */
function PrintedCard({ exercise, guides, showAnswers }: {
  exercise: PrintableExercise; guides: boolean; showAnswers: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState(false)
  const ex = Object.fromEntries([
    'statement', 'response_type', 'expected', 'grading', 'choices', 'figure', 'figure_url',
    'calculator', 'title', 'badge_type', 'difficulty', 'level', 'kind', 'is_problem',
  ].map((k) => [k, (exercise as Record<string, any>)[k]]))
  const key = JSON.stringify({ exercise: ex, guides, show_answers: showAnswers })
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '300px' })
    if (ref.current) observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (!visible) return
    const controller = new AbortController()
    let url: string | null = null
    setSrc(null); setError(false)
    const token = getToken()
    fetch('/api/content/card-preview.png', {
      method: 'POST', body: key, signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.blob() })
      .then((blob) => { if (!controller.signal.aborted) { url = URL.createObjectURL(blob); setSrc(url) } })
      .catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url) }
  }, [key, visible])
  return <Box ref={ref} style={{ minHeight: src ? undefined : 100 }}>
    {src ? <img src={src} alt={exercise.title || exercise.statement || 'Exercice de calcul'}
      style={{ width: '100%', height: 'auto', display: 'block' }} />
      : error ? <Text c="red" size="sm">Aperçu indisponible. Recharge la page pour réessayer.</Text>
      : <Box py="lg" ta="center"><Loader size="sm" /></Box>}
  </Box>
}

function DetailBlock({ label, text, color, guide }: { label: string; text: string; color: string; guide?: boolean }) {
  return <Box>
    <Text size="10px" fw={700} c="dimmed" mb={2}>{label}</Text>
    <Box style={guide ? { borderLeft: `3px solid var(--mantine-color-${color}-4)`, background: 'var(--mantine-color-gray-0)', borderRadius: 4, padding: '6px 8px' } : undefined}>
      <Group gap={6} align="flex-start" wrap="nowrap">
        {guide && <BookOpen size={15} color={`var(--mantine-color-${color}-6)`} style={{ flex: '0 0 auto', marginTop: 2 }} />}
        <Box style={{ flex: 1, minWidth: 0 }}><ExerciseRichBody text={text} color={color} size="sm" /></Box>
      </Group>
    </Box>
  </Box>
}

export default function ExercisePrintPreview({ exercise, color = 'indigo', badges, actions,
  beforeFrame, afterFrame, showCorrection = false, showGuide = false, showAnswers = false,
  guides = true, className }: PreviewProps) {
  return (
    <Stack gap={3} className={className} style={{ width: '100%', maxWidth: 340 }}>
      {beforeFrame}
      {(badges || actions) && <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Box style={{ minWidth: 0 }}>{badges}</Box>{actions}
      </Group>}
      <PrintedCard exercise={exercise} guides={guides} showAnswers={showAnswers} />
      {afterFrame}
      {showGuide && exercise.correction_guide && <DetailBlock label="Guide (élève)" text={exercise.correction_guide} color={color} guide />}
      {showCorrection && exercise.correction_solution && <DetailBlock label="Corrigé (prof)" text={exercise.correction_solution} color={color} />}
    </Stack>
  )
}
