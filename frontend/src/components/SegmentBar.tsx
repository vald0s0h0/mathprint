// Visualiseur des étapes MÉTIER de la correction d'un lot de scan. L'état se lit
// à la forme, pas seulement à la couleur :
//   ✓ texte vert        = étape faite
//   pastille bleue ⟳    = en cours (traitement automatique)
//   pastille orange     = à vous (correction manuelle)
//   pastille rouge      = bloqué
//   texte gris          = à venir
// Utilisé par chaque lot scanné (principal comme suivants), sur une seule ligne :
// les colonnes d'une carte restent alignées d'un lot à l'autre.
import { Badge, Group, Loader, Text } from '@mantine/core'
import { Check } from 'lucide-react'

export type SegState = 'green' | 'orange' | 'blue' | 'gray' | 'red'
export type Segment = { phase: string; label?: string; state: SegState }

export const SEG_COLORS: Record<SegState, string> = {
  green: 'var(--mantine-color-green-6)', orange: 'var(--mantine-color-orange-6)',
  blue: 'var(--mantine-color-blue-5)', gray: 'var(--mantine-color-gray-4)',
  red: 'var(--mantine-color-red-6)',
}

function Step({ s }: { s: Segment }) {
  const label = s.label ?? s.phase
  if (s.state === 'green') {
    return (
      <Group gap={4} wrap="nowrap">
        <Check size={13} strokeWidth={3} color={SEG_COLORS.green} />
        <Text size="xs" fw={500} style={{ whiteSpace: 'nowrap' }}>{label}</Text>
      </Group>
    )
  }
  if (s.state === 'gray') {
    return <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>{label}</Text>
  }
  return (
    <Badge variant="light" color={s.state} size="md" radius="sm" tt="none" fw={700}
      leftSection={s.state === 'blue' ? <Loader size={9} color="blue" /> : undefined}
      style={{ flexShrink: 0 }}>
      {label}
    </Badge>
  )
}

export default function SegmentBar({ segments }: { segments: Segment[] }) {
  return (
    <Group gap={8} wrap="nowrap">
      {segments.map((s, i) => (
        <Group key={s.phase} gap={8} wrap="nowrap">
          <Step s={s} />
          {i < segments.length - 1 && <Text size="xs" c="dimmed">›</Text>}
        </Group>
      ))}
    </Group>
  )
}
