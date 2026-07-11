import { Badge, Button, Card, Grid, Group, Stack, Table, Text, Title } from '@mantine/core'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

type Dash = {
  pending_reviews: number
  recent_batches: { id: string; status: string; assessment_title: string; created_at: string }[]
  classes: { id: string; name: string; students: number; avg_mastery: number; due_competencies: number; is_mock: boolean }[]
  assessments_draft: number
  system: { mock_mode: boolean; version: string }
}
type Costs = Record<string, { day_eur: number; month_eur: number; calls_month: number; daily_budget_eur: number }>

export default function Dashboard() {
  const [dash, setDash] = useState<Dash | null>(null)
  const [costs, setCosts] = useState<Costs | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.get<Dash>('/api/dashboard').then(setDash)
    api.get<Costs>('/api/costs').then(setCosts)
  }, [])

  if (!dash) return <Text>Chargement…</Text>

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Dashboard</Title>
        <Button size="md" onClick={() => navigate('/sujets')}>+ Créer un sujet</Button>
      </Group>
      <Grid>
        <Grid.Col span={4}>
          <Card withBorder>
            <Text fw={600}>Corrections</Text>
            <Text size="xl" fw={700} c={dash.pending_reviews ? 'orange' : 'green'}>
              {dash.pending_reviews}
            </Text>
            <Text size="sm" c="dimmed">validation(s) en attente</Text>
            {dash.recent_batches.slice(0, 3).map((b) => (
              <Group key={b.id} justify="space-between" mt="xs">
                <Text size="sm" lineClamp={1}>{b.assessment_title}</Text>
                <Badge color={b.status === 'overlay_ready' ? 'green' : b.status === 'review_pending' ? 'orange' : 'blue'}>
                  {b.status}
                </Badge>
              </Group>
            ))}
          </Card>
        </Grid.Col>
        <Grid.Col span={4}>
          <Card withBorder>
            <Text fw={600}>Classes</Text>
            <Table>
              <Table.Tbody>
                {dash.classes.map((c) => (
                  <Table.Tr key={c.id}>
                    <Table.Td>{c.name}{c.is_mock && <Badge ml={4} size="xs" color="gray">mock</Badge>}</Table.Td>
                    <Table.Td>{c.students} él.</Table.Td>
                    <Table.Td>maîtrise {(c.avg_mastery * 100).toFixed(0)} %</Table.Td>
                    <Table.Td>
                      {c.due_competencies > 0 && <Badge color="orange">{c.due_competencies} dues</Badge>}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Card>
        </Grid.Col>
        <Grid.Col span={4}>
          <Card withBorder>
            <Text fw={600}>Coûts API (30 j)</Text>
            {costs && Object.entries(costs).map(([p, c]) => (
              <Group key={p} justify="space-between" mt="xs">
                <Text size="sm" tt="capitalize">{p}</Text>
                <Text size="sm">{c.month_eur.toFixed(2)} € · {c.calls_month} appels</Text>
              </Group>
            ))}
            <Text size="xs" c="dimmed" mt="sm">
              Système v{dash.system.version} — mode mock : {dash.system.mock_mode ? 'activé' : 'désactivé'}
            </Text>
          </Card>
        </Grid.Col>
      </Grid>
    </Stack>
  )
}
