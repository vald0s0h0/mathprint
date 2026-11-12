// Bloc de réglage commun aux onglets de « Paramètres admin » : en-tête (titre,
// description, action à droite) séparé du corps par un filet, largeur homogène.
import { Box, Card, Divider, Group, Text } from '@mantine/core'
import type { CSSProperties, ReactNode } from 'react'

type Props = {
  title: ReactNode
  description?: ReactNode
  /** Bouton ou interrupteur aligné à droite de l'en-tête. */
  action?: ReactNode
  /** Pastille d'état à côté du titre. */
  badge?: ReactNode
  tone?: 'default' | 'warning' | 'danger'
  children?: ReactNode
  style?: CSSProperties
}

const TONES = {
  default: undefined,
  warning: 'var(--mantine-color-orange-5)',
  danger: 'var(--mantine-color-red-6)',
} as const

export default function SettingsSection({
  title, description, action, badge, tone = 'default', children, style,
}: Props) {
  return (
    <Card withBorder padding={0} radius="md"
      style={{ borderColor: TONES[tone], overflow: 'hidden', ...style }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="lg" p="md"
        style={{ background: 'var(--mantine-color-default-hover)' }}>
        <Box style={{ minWidth: 0 }}>
          <Group gap="xs" mb={description ? 2 : 0}>
            <Text fw={650}>{title}</Text>
            {badge}
          </Group>
          {description && <Text size="sm" c="dimmed" maw={560} lh={1.45}>{description}</Text>}
        </Box>
        {action && <Box style={{ flexShrink: 0 }}>{action}</Box>}
      </Group>
      {children != null && children !== false && (
        <>
          <Divider />
          <Box p="md">{children}</Box>
        </>
      )}
    </Card>
  )
}
