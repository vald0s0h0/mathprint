// Dashboard : indicateurs clés + classes du cycle filtré + activité récente.
import {
  Badge, Button, Card, Group, Progress, SimpleGrid, Skeleton, Stack, Text,
  ThemeIcon, Title,
} from '@mantine/core'
import {
  AlertTriangle, CircleDollarSign, FileText, Plus, ScanLine, Users,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAppState } from '../state/AppState'

type Dash = {
  pending_reviews: number
  recent_batches: { id: string; status: string; assessment_title: string; grade_level: string; created_at: string }[]
  classes: { id: string; name: string; grade_level: string; students: number; avg_mastery: number; due_competencies: number }[]
  assessments_draft: number
  system: { version: string }
}
type Costs = Record<string, { day_eur: number; month_eur: number; calls_month: number }>

const BATCH_LABELS: Record<string, string> = {
  overlay_ready: 'overlay prêt', review_pending: 'à valider', finalized: 'finalisé',
  graded: 'corrigé', uploaded: 'déposé', ocr_complete: 'OCR terminé',
}

function StatCard({ icon, label, value, color, hint, onClick }: {
  icon: React.ReactNode; label: string; value: React.ReactNode
  color: string; hint?: string; onClick?: () => void
}) {
  return (
    <Card withBorder padding="md" style={onClick ? { cursor: 'pointer' } : undefined}
      onClick={onClick}>
      <Group gap="sm" wrap="nowrap">
        <ThemeIcon variant="light" color={color} size={42} radius="md">{icon}</ThemeIcon>
        <div style={{ minWidth: 0 }}>
          <Text fz={22} fw={700} lh={1.15}>{value}</Text>
          <Text size="sm" c="dimmed" lineClamp={1}>{label}</Text>
        </div>
      </Group>
      {hint && <Text size="xs" c="dimmed" mt={6}>{hint}</Text>}
    </Card>
  )
}

export default function Dashboard() {
  const [dash, setDash] = useState<Dash | null>(null)
  const [costs, setCosts] = useState<Costs | null>(null)
  const navigate = useNavigate()
  const { cycle, matches } = useAppState()

  useEffect(() => {
    api.get<Dash>('/api/dashboard').then(setDash)
    api.get<Costs>('/api/costs').then(setCosts)
  }, [])

  if (!dash) {
    return (
      <Stack>
        <Skeleton h={36} w={280} />
        <SimpleGrid cols={{ base: 2, md: 4 }}>
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} h={84} />)}
        </SimpleGrid>
        <Skeleton h={220} />
      </Stack>
    )
  }

  const classes = dash.classes.filter((c) => matches(c.grade_level))
  const batches = dash.recent_batches.filter((b) => matches(b.grade_level))
  const monthCost = costs
    ? Object.values(costs).reduce((n, c) => n + c.month_eur, 0) : 0

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2}>Dashboard</Title>
          <Text size="sm" c="dimmed">
            {cycle === 'all' ? 'Tous les cycles' : `Cycle ${cycle}`} — {classes.length} classe(s)
          </Text>
        </div>
        <Button size="md" leftSection={<Plus size={18} />}
          onClick={() => navigate('/sujets?nouveau=1')}>
          Créer un sujet
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatCard icon={<ScanLine size={22} />} label="validation(s) en attente"
          value={dash.pending_reviews} color={dash.pending_reviews ? 'orange' : 'green'}
          onClick={() => navigate('/corrections')} />
        <StatCard icon={<Users size={22} />} label="élèves suivis"
          value={classes.reduce((n, c) => n + c.students, 0)} color="indigo"
          onClick={() => navigate('/eleves')} />
        <StatCard icon={<FileText size={22} />} label="sujet(s) en brouillon"
          value={dash.assessments_draft} color="blue"
          onClick={() => navigate('/sujets')} />
        <StatCard icon={<CircleDollarSign size={22} />} label="coût API sur 30 jours"
          value={`${monthCost.toFixed(2)} €`} color="teal" />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        <Card withBorder padding="lg">
          <Group justify="space-between" mb="sm">
            <Text fw={650}>Classes</Text>
            <Button size="compact-xs" variant="subtle" onClick={() => navigate('/eleves')}>
              Gérer
            </Button>
          </Group>
          {classes.length === 0 && (
            <Text size="sm" c="dimmed">
              Aucune classe {cycle !== 'all' && `en ${cycle}`} — créez-en une depuis l'écran Élèves.
            </Text>
          )}
          <Stack gap="sm">
            {classes.map((c) => (
              <div key={c.id}>
                <Group justify="space-between" mb={4} wrap="nowrap">
                  <Group gap={6} wrap="nowrap">
                    <Text fw={600} size="sm">{c.name}</Text>
                    <Badge size="xs" variant="light">{c.grade_level}</Badge>
                  </Group>
                  <Group gap={8} wrap="nowrap">
                    <Text size="xs" c="dimmed">{c.students} élèves</Text>
                    {c.due_competencies > 0 && (
                      <Badge size="xs" color="orange" variant="light"
                        leftSection={<AlertTriangle size={10} />}>
                        {c.due_competencies} à revoir
                      </Badge>
                    )}
                  </Group>
                </Group>
                <Group gap={8} wrap="nowrap">
                  <Progress value={c.avg_mastery * 100} size="sm" style={{ flex: 1 }}
                    color={c.avg_mastery > 0.6 ? 'green' : c.avg_mastery > 0.3 ? 'yellow' : 'red'} />
                  <Text size="xs" c="dimmed" ta="right" style={{ whiteSpace: 'nowrap' }}>
                    maîtrise {(c.avg_mastery * 100).toFixed(0)} %
                  </Text>
                </Group>
              </div>
            ))}
          </Stack>
        </Card>

        <Card withBorder padding="lg">
          <Group justify="space-between" mb="sm">
            <Text fw={650}>Corrections récentes</Text>
            <Button size="compact-xs" variant="subtle" onClick={() => navigate('/corrections')}>
              Tout voir
            </Button>
          </Group>
          {batches.length === 0 && (
            <Text size="sm" c="dimmed">Aucun lot de scans pour le moment.</Text>
          )}
          <Stack gap={10}>
            {batches.slice(0, 6).map((b) => (
              <Group key={b.id} justify="space-between" wrap="nowrap">
                <Text size="sm" lineClamp={1} style={{ flex: 1 }}>{b.assessment_title}</Text>
                <Badge size="sm" variant="light"
                  color={b.status === 'overlay_ready' ? 'green'
                    : b.status === 'review_pending' ? 'orange' : 'blue'}>
                  {BATCH_LABELS[b.status] ?? b.status}
                </Badge>
              </Group>
            ))}
          </Stack>
        </Card>
      </SimpleGrid>
    </Stack>
  )
}
