import { ActionIcon, Badge, Box, Group, Paper, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Copy, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import ExercisePrintPreview, { type PrintableExercise } from './ExercisePrintPreview'
import MathText from './MathText'

export type CatalogExercise = PrintableExercise & {
  id: string
  ref?: string
  bareme_points?: number
  estimated_seconds?: number
  preference?: 'like' | 'dislike' | null
  tags?: string[]
  family_id?: string
  raw?: Record<string, any> | null
  raw_ocr?: Record<string, any> | null
  status?: string
  review_notes?: string[]
  review_verdict?: ReviewVerdict | null
}
export type ReviewVerdict = 'like' | 'dislike'
export const exerciseDifficulty = (ex: CatalogExercise) => Math.max(1, Math.min(3, ex.difficulty ?? ex.level ?? 2))
export const formatPoints = (v: number) => v.toLocaleString('fr-FR', { maximumFractionDigits: 3 })

function Feedback({ grading, prefix = '' }: { grading: Record<string, any>; prefix?: string }) {
  const feedback = grading.feedback
  const messages: { label: string; text: string }[] = []
  if (feedback && typeof feedback === 'object') {
    for (const [key, label] of [['correct', 'Bonne réponse'], ['blank', 'Sans réponse'], ['fallback', 'Autre erreur']]) {
      if (feedback[key]?.text) messages.push({ label, text: feedback[key].text })
    }
    for (const [id, node] of Object.entries(feedback.options ?? {})) {
      const option = node as { text?: string }
      const index = (grading.option_ids ?? grading.right_ids ?? []).indexOf(id)
      const choice = index >= 0 ? (grading.choices ?? grading.right ?? [])[index] : null
      if (option?.text) messages.push({ label: choice ? `Réponse : ${choice}` : 'Erreur prévue', text: option.text })
    }
    for (const wrong of feedback.wrong_answers ?? []) {
      if (wrong?.text) messages.push({ label: `Réponse : ${wrong.value ?? 'incorrecte'}`, text: wrong.text })
    }
  }
  return <>
    {messages.length > 0 && <Stack gap={6}>
      {prefix && <Text size="xs" fw={650}>{prefix}</Text>}
      {messages.map((message, i) => <Box key={i}>
        <Text size="xs" c="dimmed"><MathText text={message.label} /></Text>
        <MathText text={message.text} size="sm" />
      </Box>)}
    </Stack>}
    {(grading.parts ?? []).map((part: any, i: number) => <Feedback key={i} grading={part.grading ?? {}} prefix={`Question ${i + 1}`} />)}
  </>
}

function hasFeedback(grading: Record<string, any>): boolean {
  return !!grading.feedback || (grading.parts ?? []).some((part: any) => hasFeedback(part.grading ?? {}))
}
function routes(ex: CatalogExercise): string[] {
  const route = (rtype: string, grading: Record<string, any>): string[] => {
    if (rtype === 'composite') return (grading.parts ?? ex.expected?.parts ?? []).flatMap((p: any) => route(p.response_type, p.grading ?? {}))
    const value = grading.route
    if (value) return [({ cv: 'CV', ocr_llm: 'CV+LLM', cv_llm: 'CV+LLM', llm: 'LLM', geo: 'CV' } as Record<string, string>)[value] ?? value]
    if (['qcm_single', 'qcm_multiple', 'checkbox_grid', 'matching'].includes(rtype)) return ['CV']
    if (['short_text', 'multi_blank', 'table_fill'].includes(rtype)) return ['CV+LLM']
    if (rtype === 'multiline_text') return ['LLM']
    return ['Manuelle']
  }
  const values = new Set(route(ex.response_type, ex.grading ?? {}))
  if (values.has('CV+LLM') || (values.has('CV') && values.has('LLM'))) {
    values.delete('CV'); values.delete('LLM'); values.add('CV+LLM')
  }
  return [...values]
}

/** Relecture admin : ce qu'il faut vérifier, puis l'avis. Rouge clair tant que rien n'est
 * tranché, sans rouge une fois la carte acceptée, rouge fort si elle est à reprendre. */
function ReviewPanel({ notes, verdict, onChange }: {
  notes: string[]; verdict?: ReviewVerdict | null
  onChange: (value: ReviewVerdict | null) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const strong = verdict === 'dislike'
  const red = strong || (notes.length > 0 && verdict !== 'like')
  const lines = strong && !notes.length ? ['À reprendre'] : notes
  const choose = async (value: ReviewVerdict) => {
    setBusy(true)
    try { await onChange(verdict === value ? null : value) }
    catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : 'Avis non enregistré.' }) }
    finally { setBusy(false) }
  }
  return <Group data-review={strong ? 'strong' : red ? 'light' : 'none'} gap={8} mt={6} align="flex-start" wrap="nowrap"
    justify={lines.length ? 'space-between' : 'flex-end'} style={red ? {
      background: `var(--mp-review-${strong ? 'strong-' : ''}bg)`,
      border: `1px solid var(--mp-review-${strong ? 'strong-' : ''}border)`,
      borderRadius: 'var(--mantine-radius-sm)', padding: '6px 8px',
    } : undefined}>
    {lines.length > 0 && <Stack gap={4} style={{ minWidth: 0 }}>
      {lines.map((line, i) => <Box key={i} c={red ? undefined : 'dimmed'}><MathText text={line} size="xs" /></Box>)}
    </Stack>}
    <Group gap={2} wrap="nowrap" style={{ flexShrink: 0 }}>
      <Tooltip label="Rien à reprendre"><ActionIcon aria-label="Rien à reprendre" aria-pressed={verdict === 'like'} size="sm"
        disabled={busy} color="teal" variant={verdict === 'like' ? 'filled' : 'subtle'} onClick={() => choose('like')}><ThumbsUp size={14} /></ActionIcon></Tooltip>
      <Tooltip label="À reprendre"><ActionIcon aria-label="À reprendre" aria-pressed={strong} size="sm"
        disabled={busy} color="red" variant={strong ? 'filled' : 'subtle'} onClick={() => choose('dislike')}><ThumbsDown size={14} /></ActionIcon></Tooltip>
    </Group>
  </Group>
}

export default function ExerciseCatalogCard({ exercise: ex, admin, onPreference, onReview, guides = true, caption }: {
  exercise: CatalogExercise; admin?: boolean
  onPreference?: (value: 'like' | 'dislike' | null) => Promise<void>
  // avis de relecture, côté admin
  onReview?: (value: ReviewVerdict | null) => Promise<void>
  // encadrés d'aide affichés sur la carte (false : sujet sans guides)
  guides?: boolean
  // ligne de contexte au-dessus de la carte, quand les compétences sont mélangées
  caption?: ReactNode
}) {
  const [busy, setBusy] = useState(false)
  const level = exerciseDifficulty(ex)
  const raw = ex.raw_ocr ?? ex.raw ?? {}
  const seconds = ex.estimated_seconds || raw.estimated_seconds || raw.indigo?.estimated_seconds || 0
  const displayId = ex.ref || ex.id
  const grading = ex.response_type === 'composite'
    ? { ...ex.grading, parts: ex.expected?.parts ?? ex.grading?.parts ?? [] } : ex.grading ?? {}
  const setPreference = async (value: 'like' | 'dislike') => {
    setBusy(true)
    try { await onPreference?.(ex.preference === value ? null : value) }
    catch (error) { notifications.show({ color: 'red', message: error instanceof Error ? error.message : 'Avis non enregistré.' }) }
    finally { setBusy(false) }
  }
  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(displayId)
      notifications.show({ color: 'teal', message: 'ID copié' })
    } catch {
      notifications.show({ color: 'red', message: 'Impossible de copier l’ID.' })
    }
  }
  return <Paper p="sm" withBorder radius="md">
    <ExercisePrintPreview exercise={ex} guides={guides} showAnswers maxWidth={null}
      beforeFrame={<>{caption}<Group gap={6} justify="space-between" wrap="nowrap" mb={3} style={{ overflowX: 'auto' }}>
        <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
          <Badge size="xs" variant="light" color={['green', 'yellow', 'red'][level - 1] ?? 'gray'}>
            {['Facile', 'Moyen', 'Difficile'][level - 1] ?? 'Moyen'}
          </Badge>
          {admin && routes(ex).map(route => <Badge key={route} size="xs" variant="light" color="blue">{route}</Badge>)}
          <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>{ex.bareme_points != null ? `${formatPoints(ex.bareme_points)} pt` : 'Barème —'}</Text>
        </Group>
        <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
          <Tooltip label="Temps estimé pour un élève">
            <Text size="xs" c="dimmed" ta="right" style={{ whiteSpace: 'nowrap' }}>
              {seconds > 0 ? (seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min${seconds % 60 ? ` ${seconds % 60} s` : ''}`) : 'Temps —'}
            </Text>
          </Tooltip>
          {onPreference && <Group gap={2} wrap="nowrap">
            <Tooltip label="Aimer cet exercice"><ActionIcon aria-label="Aimer cet exercice" aria-pressed={ex.preference === 'like'}
              disabled={busy} color="blue" variant={ex.preference === 'like' ? 'filled' : 'subtle'} onClick={() => setPreference('like')}><ThumbsUp size={16} /></ActionIcon></Tooltip>
            <Tooltip label="Écarter cet exercice"><ActionIcon aria-label="Écarter cet exercice" aria-pressed={ex.preference === 'dislike'}
              disabled={busy} color="red" variant={ex.preference === 'dislike' ? 'filled' : 'subtle'} onClick={() => setPreference('dislike')}><ThumbsDown size={16} /></ActionIcon></Tooltip>
          </Group>}
        </Group>
      </Group></>}
      afterFrame={<>
        {admin && <Tooltip label="Cliquer pour copier l’ID">
          <UnstyledButton onClick={copyId} aria-label={`Copier l’ID ${displayId}`} mt={4} c="dimmed"
            style={{ display: 'flex', alignItems: 'center', gap: 6, maxWidth: '100%' }}>
            <Copy size={12} style={{ flexShrink: 0 }} />
            <Text component="span" size="xs" style={{ overflowWrap: 'anywhere', textAlign: 'left' }}>ID : {displayId}</Text>
          </UnstyledButton>
        </Tooltip>}
        {hasFeedback(grading) && <Box component="details" mt={6} onClick={(e: { stopPropagation: () => void }) => e.stopPropagation()}>
          <Text component="summary" size="xs" fw={600} style={{ cursor: 'pointer' }}>Micro-corrections</Text>
          <Box pt={8}><Feedback grading={grading} /></Box>
        </Box>}
        {onReview && <ReviewPanel notes={ex.review_notes ?? []} verdict={ex.review_verdict} onChange={onReview} />}
      </>} />
  </Paper>
}
