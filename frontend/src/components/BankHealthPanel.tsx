// État de la banque, pour l'administrateur : exercices manquants et comportement
// réel des exercices. Le panneau signale ; rien n'est modifié ni généré d'ici.
import {
  ActionIcon, Badge, Box, Button, Collapse, Group, List, Paper, Table, Text, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { AlertTriangle, ChevronDown, ChevronUp, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import MathText from './MathText'

type Exhaustion = {
  severity: 'rouge' | 'orange'; programme: string; cycle: string; target: string; label: string
  kind: 'exercice' | 'probleme'; difficulty: string; stock: number; students: number
  students_out: number; median_unseen: number; served_per_week: number
  weeks_left: number | null; advice: number; command: string
}
type FieldItem = {
  exercise_id: string; statement: string; source: string; source_version: string
  difficulty: string; responses: number; classes: number; success: number
  notes: { signal: string; message: string }[]
}
type Health = { exhaustion: Exhaustion[]; field: FieldItem[] }

function copyCommand(command: string) {
  void navigator.clipboard.writeText(command).then(() => {
    notifications.show({ message: 'Commande copiée : colle-la dans une session Codex ou Claude Code.' })
  })
}

export default function BankHealthPanel() {
  const [health, setHealth] = useState<Health | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => { api.get<Health>('/api/content/bank-health').then(setHealth).catch(() => setHealth(null)) }, [])
  if (!health || (!health.exhaustion.length && !health.field.length)) return null
  const urgent = health.exhaustion.filter((a) => a.severity === 'rouge').length
  return (
    <Paper withBorder radius="md" p="sm">
      <Group justify="space-between" wrap="nowrap">
        <Group gap={8}>
          <AlertTriangle size={16} color={urgent ? 'var(--mantine-color-red-6)' : 'var(--mantine-color-orange-6)'} />
          <Text size="sm" fw={600}>
            {health.exhaustion.length} alerte(s) de stock
            {health.field.length > 0 && ` · ${health.field.length} exercice(s) à regarder`}
          </Text>
          {urgent > 0 && <Badge size="xs" color="red">{urgent} épuisée(s)</Badge>}
        </Group>
        <Button size="compact-xs" variant="subtle" onClick={() => setOpen((v) => !v)}
          rightSection={open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}>
          {open ? 'Masquer' : 'Voir'}
        </Button>
      </Group>
      <Collapse in={open}>
        {health.exhaustion.length > 0 && (
          <Table mt="sm" fz="xs" striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cible</Table.Th><Table.Th>Difficulté</Table.Th><Table.Th>Stock</Table.Th>
                <Table.Th>Élèves sans inédit</Table.Th><Table.Th>Semaines restantes</Table.Th>
                <Table.Th>À générer</Table.Th><Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {health.exhaustion.map((a) => (
                <Table.Tr key={a.command}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <Badge size="xs" color={a.severity === 'rouge' ? 'red' : 'orange'}>{a.severity}</Badge>
                      <Box>
                        <Text size="xs" fw={600}>{a.programme.toUpperCase()} {a.cycle} {a.target}</Text>
                        <Text size="xs" c="dimmed"><MathText text={a.label} /></Text>
                      </Box>
                    </Group>
                  </Table.Td>
                  <Table.Td>{a.difficulty}</Table.Td>
                  <Table.Td>{a.stock}</Table.Td>
                  <Table.Td>{a.students_out} / {a.students}</Table.Td>
                  <Table.Td>{a.weeks_left ?? '—'}</Table.Td>
                  <Table.Td>{a.advice}</Table.Td>
                  <Table.Td>
                    <Tooltip label={a.command}>
                      <ActionIcon size="sm" variant="subtle" aria-label="Copier la commande"
                        onClick={() => copyCommand(a.command)}><Copy size={14} /></ActionIcon>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {health.field.length > 0 && (
          <Box mt="sm">
            <Text size="xs" fw={600} mb={4}>Retour du terrain : à vérifier, rien n'est modifié automatiquement</Text>
            {health.field.map((item) => (
              <Paper key={item.exercise_id} withBorder p={8} radius="sm" mb={6}>
                <Group gap={6} mb={4}>
                  <Badge size="xs" variant="light">{item.difficulty}</Badge>
                  <Text size="xs" c="dimmed">
                    {item.responses} réponses, {item.classes} classes, {Math.round(item.success * 100)} % de réussite
                  </Text>
                </Group>
                <MathText text={item.statement} size="xs" />
                <List size="xs" mt={4}>
                  {item.notes.map((note) => <List.Item key={note.message}><MathText text={note.message} /></List.Item>)}
                </List>
              </Paper>
            ))}
          </Box>
        )}
      </Collapse>
    </Paper>
  )
}
