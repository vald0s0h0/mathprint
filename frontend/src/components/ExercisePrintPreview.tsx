// Aperçu partagé d'un exercice tel qu'il apparaît sur une copie.
//
// Ce composant est volontairement indépendant des écrans Banque / Exercices /
// assistant : le cadre ne contient que l'énoncé, la figure et la zone de
// réponse, comme le PDF. Les badges de gestion sont fournis par le parent et
// restent donc toujours à l'extérieur du cadre imprimé.
import { Badge, Box, Group, Paper, Stack, Table, Text } from '@mantine/core'
import { BookOpen, Calculator, Slash } from 'lucide-react'
import type { ReactNode } from 'react'
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
  className?: string
}

const SUBLABEL_RE = /^([a-h]|\d{1,2})[.)]\s+/
const BULLET_RE = /^[•–—-]\s+/
const FIGURE_TOKEN = '{{figure}}'

const stripFigureToken = (s: string) =>
  (s || '').replace(/^[ \t]*\{\{figure\}\}[ \t]*\n?/gm, '')
    .replace(/\{\{figure\}\}/g, '').trim()

/** Même convention visuelle que le PDF : les sous-questions sont détachées du
 * corps, tandis que les marqueurs {{blank}}, {{mini}} et {{blank_right}} sont
 * transformés en champs par MathText. */
export function ExerciseRichBody({ text, color = 'indigo', size }: {
  text: string; color?: string; size?: string | number
}) {
  const lines = (text || '').split('\n')
  const sizeOf = (line: string) =>
    (/\{\{(blank(_right)?|mini)\}\}/.test(line) ? '1.12em' : size)
  return (
    <Box fz={size}>
      {lines.map((line, index) => {
        const lineSize = sizeOf(line)
        const label = line.match(SUBLABEL_RE)
        if (label) {
          return (
            <Group key={index} gap={6} align="flex-start" wrap="nowrap" mt={index ? 4 : 0}>
              <Badge color={color} radius="sm" size="sm" variant="filled"
                style={{ flex: '0 0 auto', marginTop: 2 }}>{label[1]}</Badge>
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

function ResponseZone({ exercise, color }: { exercise: PrintableExercise; color: string }) {
  const rt = exercise.response_type
  const expected = exercise.expected || {}
  const grading = exercise.grading || {}
  const choices = exercise.choices || grading.choices || []
  const inlineBlank = /\{\{(blank(_right)?|mini)\}\}/.test(exercise.statement || '')

  if (rt === 'qcm_single' || rt === 'qcm_multiple') {
    const maxLength = Math.max(0, ...choices.map((c: string) => c.replace(/\$/g, '').length))
    const columns = Math.max(1, Math.min(choices.length, maxLength > 16 ? 1 : maxLength > 8 ? 2 : 3))
    return (
      <Box mt={7} style={{ display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, columnGap: 14, rowGap: 5 }}>
        {choices.map((choice: string, index: number) => (
          <Group key={index} gap={8} wrap="nowrap" align="center">
            <Box style={{ width: '2mm', height: '2mm', border: '1px solid var(--mantine-color-orange-4)', flex: '0 0 auto' }} />
            <MathText text={choice} size="sm" />
          </Group>
        ))}
      </Box>
    )
  }
  if (rt === 'checkbox_grid') {
    const cols: string[] = expected.cols || grading.cols || []
    const rows: { label: string }[] = expected.rows || grading.rows || []
    return (
      <Table withTableBorder withColumnBorders mt={8} styles={{ td: { padding: 4 }, th: { padding: 4 } }}>
        <Table.Thead><Table.Tr><Table.Th />
          {cols.map((col, i) => <Table.Th key={i} ta="center"><MathText text={col} size="xs" /></Table.Th>)}
        </Table.Tr></Table.Thead>
        <Table.Tbody>{rows.map((row, ri) => (
          <Table.Tr key={ri}><Table.Td><MathText text={row.label} size="sm" /></Table.Td>
            {cols.map((_col, ci) => <Table.Td key={ci} ta="center"><Box style={{ display: 'inline-block', width: 13, height: 13, border: '1.5px solid var(--mantine-color-orange-4)', borderRadius: 2 }} /></Table.Td>)}
          </Table.Tr>
        ))}</Table.Tbody>
      </Table>
    )
  }
  if (rt === 'matching') {
    const left: string[] = expected.left || grading.left || []
    const right: string[] = expected.right || grading.right || []
    const dot = { width: '2.2mm', height: '2.2mm', borderRadius: '50%', border: '1px solid var(--mantine-color-orange-4)', flex: '0 0 auto' }
    return (
      <Group mt={8} align="flex-start" justify="space-between" wrap="nowrap" gap={24}>
        <Box style={{ flex: 1 }}>{left.map((label, i) => <Group key={i} gap={8} wrap="nowrap" mb={6}><MathText text={label} size="sm" /><Box style={dot} /></Group>)}</Box>
        <Box style={{ flex: 1 }}>{right.map((label, i) => <Group key={i} gap={8} wrap="nowrap" mb={6}><Box style={dot} /><MathText text={label} size="sm" /></Group>)}</Box>
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

function Statement({ exercise, color }: { exercise: PrintableExercise; color: string }) {
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
            <Box style={{ flex: 1, minWidth: 0 }}><ExerciseRichBody text={part.statement || ''} color={color} /><ResponseZone exercise={partExercise} color={color} /></Box>
          </Group>
        })}</Stack>
      </Box>
    )
  }
  if ((exercise.figure_url || exercise.figure) && exercise.statement.includes(FIGURE_TOKEN)) {
    const index = exercise.statement.indexOf(FIGURE_TOKEN)
    const before = exercise.statement.slice(0, index).replace(/\n+$/, '')
    const after = exercise.statement.slice(index + FIGURE_TOKEN.length).replace(/^\n+/, '')
    return <Box>{before.trim() && <ExerciseRichBody text={before} color={color} />}{figure}{after.trim() && <ExerciseRichBody text={after} color={color} />}<ResponseZone exercise={exercise} color={color} /></Box>
  }
  return <Box><ExerciseRichBody text={stripFigureToken(exercise.statement)} color={color} />{figure}<ResponseZone exercise={exercise} color={color} /></Box>
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
  beforeFrame, afterFrame, showCorrection = false, showGuide = false, className }: PreviewProps) {
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
        <Statement exercise={exercise} color={color} />
      </Paper>
      {afterFrame}
      {showGuide && exercise.correction_guide && <DetailBlock label="Guide (élève)" text={exercise.correction_guide} color={color} guide />}
      {showCorrection && exercise.correction_solution && <DetailBlock label="Corrigé (prof)" text={exercise.correction_solution} color={color} />}
    </Stack>
  )
}
