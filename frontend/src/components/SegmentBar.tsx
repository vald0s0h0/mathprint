// Visualiseur des étapes MÉTIER de la correction : chaque étape porte son
// libellé et sa couleur (vert = fait, bleu = en cours, orange = à corriger,
// gris = à venir, rouge = bloqué). Une flèche montre que le flux avance.
// Utilisé par chaque lot scanné : le lot principal et chaque lot tardif.
import { Group, Text } from '@mantine/core'

export type SegState = 'green' | 'orange' | 'blue' | 'gray' | 'red'
export type Segment = { phase: string; label?: string; state: SegState }

export const SEG_COLORS: Record<SegState, string> = {
  green: 'var(--mantine-color-green-6)', orange: 'var(--mantine-color-orange-6)',
  blue: 'var(--mantine-color-blue-5)', gray: 'var(--mantine-color-gray-4)',
  red: 'var(--mantine-color-red-6)',
}

export default function SegmentBar({ segments }: { segments: Segment[] }) {
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
