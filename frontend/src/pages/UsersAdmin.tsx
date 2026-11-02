import { Alert, Badge, Button, Card, Group, Modal, PasswordInput, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { showError } from '../components/ClassEditor'

type User = { id: string; email: string; display_name: string; role: string; active: boolean; last_login_at: string | null }
export default function UsersAdmin() {
  const [users, setUsers] = useState<User[]>([]); const [error, setError] = useState(''); const [opened, setOpened] = useState(false)
  const [name, setName] = useState(''); const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [busy, setBusy] = useState(false)
  const refresh = () => api.get<User[]>('/api/admin/users').then(setUsers).catch(e => setError(e.message))
  useEffect(() => { void refresh() }, [])
  async function create() {
    setBusy(true)
    try { await api.post('/api/admin/users/correctors', { display_name: name, email, password }); setOpened(false); setPassword(''); await refresh() }
    catch (e) { showError(e) } finally { setBusy(false) }
  }
  return <Stack><Group justify="space-between"><Title order={4}>Comptes de connexion</Title><Button onClick={() => setOpened(true)}>Créer un correcteur</Button></Group>
    <Text size="sm" c="dimmed">Les professeurs sont créés dans Établissements, puis activent leur accès avec leur lien personnel.</Text>
    {error && <Alert color="red">{error}</Alert>}
    <Card withBorder><Table><Table.Thead><Table.Tr><Table.Th>Nom</Table.Th><Table.Th>E-mail</Table.Th><Table.Th>Rôle</Table.Th><Table.Th>État</Table.Th><Table.Th /></Table.Tr></Table.Thead><Table.Tbody>
      {users.map(u => <Table.Tr key={u.id}><Table.Td>{u.display_name}</Table.Td><Table.Td>{u.email}</Table.Td><Table.Td>{{ admin: 'Administrateur', teacher: 'Professeur', corrector: 'Correcteur' }[u.role]}</Table.Td><Table.Td><Badge color={u.active ? 'green' : 'gray'}>{u.active ? 'Actif' : 'Suspendu'}</Badge></Table.Td>
        <Table.Td>{u.role !== 'admin' && <Button size="xs" variant="subtle" onClick={() => api.patch(`/api/admin/users/${u.id}/status`, { active: !u.active }).then(refresh).catch(showError)}>{u.active ? 'Suspendre l’accès' : 'Réactiver l’accès'}</Button>}</Table.Td></Table.Tr>)}
    </Table.Tbody></Table></Card>
    <Modal opened={opened} onClose={() => setOpened(false)} title="Nouveau correcteur"><Stack><TextInput label="Nom" value={name} onChange={e => setName(e.currentTarget.value)} /><TextInput label="E-mail" value={email} onChange={e => setEmail(e.currentTarget.value)} /><PasswordInput label="Mot de passe initial" description="Au moins 10 caractères. À transmettre manuellement." value={password} onChange={e => setPassword(e.currentTarget.value)} /><Button loading={busy} disabled={!name || !email || password.length < 10} onClick={create}>Créer le compte</Button></Stack></Modal>
  </Stack>
}
