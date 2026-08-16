import {
  Alert, Badge, Card, Group, Loader, ScrollArea, SimpleGrid, Stack, Table, Text, Title,
} from '@mantine/core'
import { CircleAlert, Crown, ScanText, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'

type UserRole = 'admin' | 'teacher' | 'corrector'
type SubscriptionPlan = 'free' | 'pro' | 'max'

type UserRow = {
  id: string
  email: string
  display_name: string
  role: UserRole
  subscription_plan: SubscriptionPlan | null
  active: boolean
  last_login_at: string | null
}

const ROLE_INFO: Record<UserRole, { label: string; color: string }> = {
  admin: { label: 'Admin', color: 'red' },
  teacher: { label: 'Utilisateur', color: 'blue' },
  corrector: { label: 'Correcteur', color: 'violet' },
}

const PLAN_INFO: Record<SubscriptionPlan, { label: string; color: string; access: string }> = {
  free: { label: 'Free', color: 'gray', access: '100 copies corrigées par mois' },
  pro: { label: 'Pro', color: 'blue', access: 'Accès à toutes les fonctionnalités' },
  max: {
    label: 'Max',
    color: 'grape',
    access: 'Accès complet, OCR et correction manuelle par un correcteur (à venir)',
  },
}

function formatLastLogin(value: string | null) {
  if (!value) return 'Jamais'
  return new Intl.DateTimeFormat('fr-FR', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function accessLabel(user: UserRow) {
  if (user.role === 'admin') return 'Accès administrateur complet'
  if (user.role === 'corrector') return 'Interface correcteur à venir'
  return PLAN_INFO[user.subscription_plan ?? 'free'].access
}

export default function UsersAdmin() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api.get<UserRow[]>('/api/admin/users')
      .then((result) => { if (active) setUsers(result) })
      .catch((reason: Error) => { if (active) setError(reason.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const counts = useMemo(() => ({
    admin: users.filter((user) => user.role === 'admin').length,
    teacher: users.filter((user) => user.role === 'teacher').length,
    corrector: users.filter((user) => user.role === 'corrector').length,
  }), [users])

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Utilisateurs</Title>
        <Text size="sm" c="dimmed">
          Comptes, rôles et niveaux d'abonnement de MathPrint.
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
        <Card withBorder>
          <Group justify="space-between" align="flex-start">
            <div>
              <Text size="sm" c="dimmed">Administrateurs</Text>
              <Text size="xl" fw={700}>{counts.admin}</Text>
              <Text size="xs" c="dimmed">Accès complet, sans abonnement</Text>
            </div>
            <Crown size={20} color="var(--mantine-color-red-6)" />
          </Group>
        </Card>
        <Card withBorder>
          <Group justify="space-between" align="flex-start">
            <div>
              <Text size="sm" c="dimmed">Utilisateurs classiques</Text>
              <Text size="xl" fw={700}>{counts.teacher}</Text>
              <Text size="xs" c="dimmed">Offres Free, Pro ou Max</Text>
            </div>
            <Users size={20} color="var(--mantine-color-blue-6)" />
          </Group>
        </Card>
        <Card withBorder>
          <Group justify="space-between" align="flex-start">
            <div>
              <Text size="sm" c="dimmed">Correcteurs</Text>
              <Text size="xl" fw={700}>{counts.corrector}</Text>
              <Text size="xs" c="dimmed">Sans abonnement · interface à venir</Text>
            </div>
            <ScanText size={20} color="var(--mantine-color-violet-6)" />
          </Group>
        </Card>
      </SimpleGrid>

      <Card withBorder>
        <Text size="sm" fw={650} mb="sm">Niveaux d'abonnement</Text>
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
          {(Object.keys(PLAN_INFO) as SubscriptionPlan[]).map((key) => {
            const plan = PLAN_INFO[key]
            return (
              <Group key={key} gap="sm" wrap="nowrap" align="flex-start">
                <Badge variant="light" color={plan.color}>{plan.label}</Badge>
                <Text size="sm" c="dimmed">{plan.access}</Text>
              </Group>
            )
          })}
        </SimpleGrid>
      </Card>

      {error && (
        <Alert color="red" icon={<CircleAlert size={16} />}>{error}</Alert>
      )}

      <Card withBorder padding={0}>
        <Group justify="space-between" px="md" py="sm">
          <Text fw={650}>Tous les utilisateurs</Text>
          <Badge variant="light" color="gray">{users.length}</Badge>
        </Group>
        {loading ? (
          <Group justify="center" py="xl"><Loader size="sm" /></Group>
        ) : users.length === 0 ? (
          <Text c="dimmed" ta="center" py="xl">Aucun utilisateur</Text>
        ) : (
          <ScrollArea type="auto">
            <Table highlightOnHover verticalSpacing="sm" miw={820}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Utilisateur</Table.Th>
                  <Table.Th>Type</Table.Th>
                  <Table.Th>Abonnement</Table.Th>
                  <Table.Th>Accès</Table.Th>
                  <Table.Th>Statut</Table.Th>
                  <Table.Th>Dernière connexion</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {users.map((user) => {
                  const role = ROLE_INFO[user.role]
                  const plan = user.subscription_plan
                    ? PLAN_INFO[user.subscription_plan]
                    : null
                  return (
                    <Table.Tr key={user.id}>
                      <Table.Td>
                        <Text size="sm" fw={600}>{user.display_name || '—'}</Text>
                        <Text size="xs" c="dimmed">{user.email}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge variant="light" color={role.color}>{role.label}</Badge>
                      </Table.Td>
                      <Table.Td>
                        {plan
                          ? <Badge variant="outline" color={plan.color}>{plan.label}</Badge>
                          : <Text size="sm" c="dimmed">—</Text>}
                      </Table.Td>
                      <Table.Td><Text size="sm">{accessLabel(user)}</Text></Table.Td>
                      <Table.Td>
                        <Badge variant="dot" color={user.active ? 'green' : 'gray'}>
                          {user.active ? 'Actif' : 'Inactif'}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" c="dimmed">{formatLastLogin(user.last_login_at)}</Text>
                      </Table.Td>
                    </Table.Tr>
                  )
                })}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
      </Card>
    </Stack>
  )
}
