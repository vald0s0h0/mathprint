// Aperçu partagé d'un exercice tel qu'il apparaît sur une copie.
//
// Ce composant est volontairement indépendant des écrans Banque / Exercices /
// assistant : le cadre ne contient que l'énoncé, la figure et la zone de
// réponse, comme le PDF. Les badges de gestion sont fournis par le parent et
// restent donc toujours à l'extérieur du cadre imprimé.
import { Badge, Box, Group, Paper, Stack, Table, Text } from '@mantine/core'
import { BookOpen, Calculator, Check, Slash } from 'lucide-react'
import type { ReactNode } from 'react'
import { useLayoutEffect, useRef, useState } from 'react'
import { parseBlocks, stripBold, type RichBlock } from '../utils/richblocks'
import AuthImg from './AuthImg'
import FigurePreview from './FigurePreview'
import MathText from './MathText'

export type PrintableExercise = {
  statement: string
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
  className?: string
}

const SUBLABEL_RE = /^([a-h]|\d{1,2})[.)]\s+/
const BULLET_RE = /^[•–—-]\s+/
const FIGURE_TOKEN = '{{figure}}'

const stripFigureToken = (s: string) =>
  (s || '').replace(/^[ \t]*\{\{figure\}\}[ \t]*\n?/gm, '')
    .replace(/\{\{figure\}\}/g, '').trim()

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

function Figure({ exercise }: { exercise: PrintableExercise }) {
  if (exercise.figure_url) {
    return <AuthImg src={exercise.figure_url} alt="figure" style={{
      maxWidth: '100%', maxHeight: 180, margin: '6px auto', display: 'block', objectFit: 'contain',
    }} />
  }
  if (exercise.figure) {
    return <Box my={6} style={{ display: 'flex', justifyContent: 'center' }}>
      <FigurePreview figureJson={exercise.figure} />
    </Box>
  }
  return null
}

function AnswerBox({ height = '13mm' }: { height?: string }) {
  return <Box mt={8} style={{
    height, border: '1px solid var(--mantine-color-orange-3)', borderRadius: 5,
  }} />
}

function LinedAnswerBox({ lines }: { lines: number }) {
  return <Box mt={8} p="2mm 1.5mm" style={{
    height: `calc(${lines} * 9mm + 4mm)`,
    border: '1px solid var(--mantine-color-orange-3)', borderRadius: 5,
    display: 'grid', gridTemplateRows: `repeat(${lines}, minmax(0, 1fr))`,
  }}>
    {Array.from({ length: lines }, (_, index) => <Box key={index} style={{
      borderBottom: '1px solid var(--mantine-color-orange-2)',
    }} />)}
  </Box>
}

// Case (QCM/grille) : creuse normalement — EXACTEMENT le rendu d'avant, une
// copie ne s'imprime jamais pré-cochée — pleine + coche quand elle porte la
// bonne réponse en relecture (showAnswers), même couleur que la carte, pour
// qu'un coup d'œil suffise à confirmer que la réponse attendue est la bonne.
function AnswerCheckbox({ checked, color, size, iconSize, radius = 2 }: {
  checked: boolean; color: string; size: string; iconSize: number; radius?: number
}) {
  return (
    <Box style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: size, height: size, borderRadius: radius, flex: '0 0 auto',
      border: `1px solid var(--mantine-color-${checked ? color : 'orange'}-${checked ? 6 : 4})`,
      background: checked ? `var(--mantine-color-${color}-6)` : undefined,
    }}>
      {checked && <Check size={iconSize} color="white" strokeWidth={3} />}
    </Box>
  )
}

/** Choix d'un QCM en grille de 1 à 4 colonnes. Le nombre de colonnes part d'un
 *  plafond (mesure typographique grossière sur la longueur apparente) puis est
 *  RÉDUIT si la mesure RÉELLE, une fois KaTeX rendu dans le DOM, montre qu'un
 *  libellé déborde de sa colonne (une formule rend toujours plus large que son
 *  nombre de caractères ne le laisse deviner — cf. pdfgen._qcm_layout côté PDF,
 *  qui applique exactement la même correction par mesure).
 *
 *  Pendant que columns > 1, chaque libellé est contraint en une seule ligne
 *  (nowrap) pour permettre cette mesure d'occupation ; à columns === 1 (dernier
 *  recours), la contrainte est levée et le texte peut se replier normalement —
 *  jamais au milieu d'une formule (cf. MathSpan), seulement entre mots/formules. */
function QcmChoices({ choices, correct, showAnswers, color }: {
  choices: string[]; correct: Set<number>; showAnswers?: boolean; color: string
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const maxLength = Math.max(0, ...choices.map((c) => stripMathLength(c)))
  const cap = Math.max(1, Math.min(choices.length,
    maxLength > 16 ? 1 : maxLength > 8 ? 2 : maxLength > 4 ? 3 : 4))
  const [columns, setColumns] = useState(cap)
  const choicesKey = choices.join('')

  useLayoutEffect(() => { setColumns(cap) }, [choicesKey, cap])

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el || columns <= 1) return
    const overflow = Array.from(el.querySelectorAll<HTMLElement>('[data-qcm-label]'))
      .some((node) => node.scrollWidth > node.clientWidth + 1)
    if (overflow) setColumns((n) => Math.max(1, n - 1))
  })

  return (
    <Box ref={containerRef} mt={7} style={{
      display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
      columnGap: 14, rowGap: 5,
    }}>
      {choices.map((choice, index) => (
        <Group key={index} gap={8} wrap="nowrap" align="center" style={{ minWidth: 0 }}>
          <AnswerCheckbox checked={!!showAnswers && correct.has(index)} color={color}
            size="2mm" iconSize={7} radius={0} />
          <Box data-qcm-label style={{
            minWidth: 0, flex: '1 1 auto',
            overflow: columns > 1 ? 'hidden' : 'visible',
            whiteSpace: columns > 1 ? 'nowrap' : 'normal',
          }}>
            <MathText text={choice} size="sm" />
          </Box>
        </Group>
      ))}
    </Box>
  )
}

function ResponseZone({ exercise, color, showAnswers }: {
  exercise: PrintableExercise; color: string; showAnswers?: boolean
}) {
  const rt = exercise.response_type
  const expected = exercise.expected || {}
  const grading = exercise.grading || {}
  const choices = exercise.choices || grading.choices || []
  const inlineBlank = /\{\{(blank(_right)?|mini)\}\}/.test(exercise.statement || '')

  if (rt === 'qcm_single' || rt === 'qcm_multiple') {
    const correct = new Set<number>(expected.correct || grading.correct || [])
    return <QcmChoices choices={choices} correct={correct} showAnswers={showAnswers} color={color} />
  }
  if (rt === 'checkbox_grid') {
    const cols: string[] = expected.cols || grading.cols || []
    const rows: { label: string; correct?: number }[] = expected.rows || grading.rows || []
    return (
      <Table withTableBorder withColumnBorders mt={8} styles={{ td: { padding: 4 }, th: { padding: 4 } }}>
        <Table.Thead><Table.Tr><Table.Th />
          {cols.map((col, i) => <Table.Th key={i} ta="center"><MathText text={col} size="xs" /></Table.Th>)}
        </Table.Tr></Table.Thead>
        <Table.Tbody>{rows.map((row, ri) => (
          <Table.Tr key={ri}><Table.Td><MathText text={row.label} size="sm" /></Table.Td>
            {cols.map((_col, ci) => <Table.Td key={ci} ta="center">
              <AnswerCheckbox checked={!!showAnswers && row.correct === ci} color={color}
                size="13px" iconSize={10} />
            </Table.Td>)}
          </Table.Tr>
        ))}</Table.Tbody>
      </Table>
    )
  }
  if (rt === 'matching') {
    const left: string[] = expected.left || grading.left || []
    const right: string[] = expected.right || grading.right || []
    const pairs: [number, number][] = expected.pairs || grading.pairs || []
    // pas de tracé de trait dans cet aperçu compact : le n° de paire, posé sur
    // les deux pastilles reliées, suffit à vérifier l'appariement attendu.
    const pairOf = (side: 'left' | 'right', index: number) => {
      const k = pairs.findIndex((p) => p[side === 'left' ? 0 : 1] === index)
      return k >= 0 ? k + 1 : null
    }
    const dot = (n: number | null) => {
      const marked = !!showAnswers && n != null
      return (
        <Box style={{
          width: marked ? '4mm' : '2.2mm', height: marked ? '4mm' : '2.2mm',
          borderRadius: '50%', flex: '0 0 auto',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '8px', fontWeight: 700, lineHeight: 1,
          color: marked ? 'white' : undefined,
          background: marked ? `var(--mantine-color-${color}-6)` : undefined,
          border: `1px solid var(--mantine-color-${marked ? color : 'orange'}-${marked ? 6 : 4})`,
        }}>{marked ? n : ''}</Box>
      )
    }
    return (
      <Group mt={8} align="flex-start" justify="space-between" wrap="nowrap" gap={24}>
        <Box style={{ flex: 1 }}>{left.map((label, i) => <Group key={i} gap={8} wrap="nowrap" mb={6}><MathText text={label} size="sm" />{dot(pairOf('left', i))}</Group>)}</Box>
        <Box style={{ flex: 1 }}>{right.map((label, i) => <Group key={i} gap={8} wrap="nowrap" mb={6}>{dot(pairOf('right', i))}<MathText text={label} size="sm" /></Group>)}</Box>
      </Group>
    )
  }
  if (rt === 'manual_drawing') return <AnswerBox height="60mm" />
  if (rt === 'short_text') return inlineBlank ? null : <AnswerBox />
  if (rt === 'multi_blank') return null
  if (rt === 'multiline_text') {
    const lineCount = Math.max(3, Math.min(12, Number(exercise.lines ?? grading.lines ?? 5)))
    return <LinedAnswerBox lines={lineCount} />
  }
  if (rt === 'table_fill') {
    const cells: any[][] = expected.cells || grading.cells || []
    const colLabels = exercise.col_labels || grading.col_labels
    const rowLabels = exercise.row_labels || grading.row_labels
    return (
      <Table withTableBorder withColumnBorders mt={8} styles={{ td: { padding: 4, borderColor: 'var(--mantine-color-orange-3)' } }}>
        {colLabels && <Table.Thead><Table.Tr>{rowLabels && <Table.Th />}{colLabels.map((label: string, i: number) => <Table.Th key={i}><MathText text={label} size="xs" /></Table.Th>)}</Table.Tr></Table.Thead>}
        <Table.Tbody>{cells.map((row, r) => <Table.Tr key={r}>
          {rowLabels && <Table.Td><MathText text={rowLabels[r] || ''} size="xs" /></Table.Td>}
          {row.map((cell, c) => <Table.Td key={c} ta="center" style={{ minWidth: '10mm', height: '10.4mm', background: cell?.given ? 'var(--mantine-color-gray-1)' : undefined }}>
            {cell?.given ? <MathText text={String(cell.value ?? '')} size="xs" /> : null}
          </Table.Td>)}
        </Table.Tr>)}</Table.Tbody>
      </Table>
    )
  }
  return <AnswerBox />
}

function Statement({ exercise, color, showAnswers }: {
  exercise: PrintableExercise; color: string; showAnswers?: boolean
}) {
  const figure = <Figure exercise={exercise} />
  if (exercise.response_type === 'composite') {
    const parts: any[] = exercise.expected?.parts || exercise.grading?.parts || []
    return (
      <Box>
        <ExerciseRichBody text={stripFigureToken(exercise.statement)} color={color} />
        {figure}
        <Stack gap={9} mt={8}>{parts.map((part, index) => {
          const partExercise: PrintableExercise = {
            ...exercise, statement: part.statement || '', response_type: part.response_type || 'short_text',
            expected: part.expected || {}, grading: part.grading || {},
            choices: part.grading?.choices || [], figure: null, figure_url: null,
          }
          return <Group key={index} gap={6} align="flex-start" wrap="nowrap">
            <Badge color={color} radius="sm" size="sm" variant="filled" style={{ flex: '0 0 auto', marginTop: 2 }}>{String.fromCharCode(97 + index)}</Badge>
            <Box style={{ flex: 1, minWidth: 0 }}><ExerciseRichBody text={part.statement || ''} color={color} /><ResponseZone exercise={partExercise} color={color} showAnswers={showAnswers} /></Box>
          </Group>
        })}</Stack>
      </Box>
    )
  }
  if ((exercise.figure_url || exercise.figure) && exercise.statement.includes(FIGURE_TOKEN)) {
    const index = exercise.statement.indexOf(FIGURE_TOKEN)
    const before = exercise.statement.slice(0, index).replace(/\n+$/, '')
    const after = exercise.statement.slice(index + FIGURE_TOKEN.length).replace(/^\n+/, '')
    return <Box>{before.trim() && <ExerciseRichBody text={before} color={color} />}{figure}{after.trim() && <ExerciseRichBody text={after} color={color} />}<ResponseZone exercise={exercise} color={color} showAnswers={showAnswers} /></Box>
  }
  return <Box><ExerciseRichBody text={stripFigureToken(exercise.statement)} color={color} />{figure}<ResponseZone exercise={exercise} color={color} showAnswers={showAnswers} /></Box>
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
  className }: PreviewProps) {
  return (
    <Stack gap={3} className={className} style={{ width: '100%', maxWidth: 340 }}>
      {beforeFrame}
      {(badges || actions) && <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Box style={{ minWidth: 0 }}>{badges}</Box>{actions}
      </Group>}
      <Paper withBorder p="xs" radius={8} style={{
        position: 'relative',
        fontSize: 12,
        background: 'var(--mantine-color-body)',
        borderColor: 'var(--mantine-color-gray-4)',
        boxShadow: '1px 1px 0 rgba(0,0,0,.07)',
      }}>
        {exercise.calculator && exercise.calculator !== 'autorisee' && <Box style={{
          position: 'absolute', top: 6, right: 6, width: 17, height: 17, zIndex: 2,
        }}>
          <Calculator size={17} color={exercise.calculator === 'necessaire'
            ? 'var(--mantine-color-blue-6)' : 'var(--mantine-color-red-6)'} />
          {exercise.calculator === 'interdite' && <Slash size={17}
            color="var(--mantine-color-red-6)" style={{ position: 'absolute', inset: 0 }} />}
        </Box>}
        <Statement exercise={exercise} color={color} showAnswers={showAnswers} />
      </Paper>
      {afterFrame}
      {showGuide && exercise.correction_guide && <DetailBlock label="Guide (élève)" text={exercise.correction_guide} color={color} guide />}
      {showCorrection && exercise.correction_solution && <DetailBlock label="Corrigé (prof)" text={exercise.correction_solution} color={color} />}
    </Stack>
  )
}
