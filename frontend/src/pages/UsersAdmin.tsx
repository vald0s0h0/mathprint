import { Alert, Badge, Button, Card, Group, Modal, PasswordInput, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { showError } from '../components/ClassEditor'

type User = { id: string; email: string; display_name: string; role: string; active: boolean; last_login_at: string | null }
export default function UsersAdmin() {
  const [users, setUsers] = useState<User[]>([]); const [error, setError] = useState(''); const [opened, setOpened] = useState(false)
  const [name, setName] = useState(''); const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<{ id: string; name: string }[]>([]); const [inviteOpened, setInviteOpened] = useState(false)
  const [teacherName, setTeacherName] = useState(''); const [inviteUrl, setInviteUrl] = useState('')
  const refresh = () => Promise.all([api.get<User[]>('/api/admin/users').then(setUsers),
    api.get<{ id: string; name: string }[]>('/api/admin/users/autonomous-teachers').then(setPending)]).catch(e => setError(e.message))
  useEffect(() => { void refresh() }, [])
  async function invite(path: string, body?: object) {
    setBusy(true)
    try {
      const r = await api.post<{ path: string }>(path, body)
      setInviteUrl(window.location.origin + r.path); setInviteOpened(false); setTeacherName(''); await refresh()
    } catch (e) { showError(e) } finally { setBusy(false) }
  }
  async function create() {
    setBusy(true)
    try { await api.post('/api/admin/users/correctors', { display_name: name, email, password }); setOpened(false); setPassword(''); await refresh() }
    catch (e) { showError(e) } finally { setBusy(false) }
  }
  return <Stack><Group justify="space-between"><Title order={4}>Comptes de connexion</Title><Group gap="xs"><Button variant="light" onClick={() => setInviteOpened(true)}>Inviter un professeur autonome</Button><Button onClick={() => setOpened(true)}>Créer un correcteur</Button></Group></Group>
    <Text size="sm" c="dimmed">Les professeurs accompagnés sont créés dans Établissements. Un professeur autonome est invité ici, puis choisit son programme et sa matière dans ses paramètres.</Text>
    {error && <Alert color="red">{error}</Alert>}
    {pending.length > 0 && <Card withBorder><Text fw={600} size="sm" mb="xs">Professeurs autonomes en attente d’inscription</Text>
      {pending.map(t => <Group key={t.id} justify="space-between" py={2}><Text size="sm">{t.name}</Text><Button size="xs" variant="subtle" loading={busy} onClick={() => invite(`/api/admin/users/autonomous-teachers/${t.id}/invitation`)}>Nouveau lien d’inscription</Button></Group>)}</Card>}
    <Card withBorder><Table><Table.Thead><Table.Tr><Table.Th>Nom</Table.Th><Table.Th>E-mail</Table.Th><Table.Th>Rôle</Table.Th><Table.Th>État</Table.Th><Table.Th /></Table.Tr></Table.Thead><Table.Tbody>
      {users.map(u => <Table.Tr key={u.id}><Table.Td>{u.display_name}</Table.Td><Table.Td>{u.email}</Table.Td><Table.Td>{{ admin: 'Administrateur', teacher: 'Professeur', corrector: 'Correcteur' }[u.role]}</Table.Td><Table.Td><Badge color={u.active ? 'green' : 'gray'}>{u.active ? 'Actif' : 'Suspendu'}</Badge></Table.Td>
        <Table.Td>{u.role !== 'admin' && <Button size="xs" variant="subtle" onClick={() => api.patch(`/api/admin/users/${u.id}/status`, { active: !u.active }).then(refresh).catch(showError)}>{u.active ? 'Suspendre l’accès' : 'Réactiver l’accès'}</Button>}</Table.Td></Table.Tr>)}
    </Table.Tbody></Table></Card>
    <Modal opened={opened} onClose={() => setOpened(false)} title="Nouveau correcteur"><Stack><TextInput label="Nom" value={name} onChange={e => setName(e.currentTarget.value)} /><TextInput label="E-mail" value={email} onChange={e => setEmail(e.currentTarget.value)} /><PasswordInput label="Mot de passe initial" description="Au moins 10 caractères. À transmettre manuellement." value={password} onChange={e => setPassword(e.currentTarget.value)} /><Button loading={busy} disabled={!name || !email || password.length < 10} onClick={create}>Créer le compte</Button></Stack></Modal>
    <Modal opened={inviteOpened} onClose={() => setInviteOpened(false)} title="Inviter un professeur autonome"><Stack><TextInput label="Nom affiché et imprimé" value={teacherName} onChange={e => setTeacherName(e.currentTarget.value)} /><Button loading={busy} disabled={!teacherName.trim()} onClick={() => invite('/api/admin/users/autonomous-teachers', { name: teacherName })}>Créer le lien d’inscription</Button></Stack></Modal>
    <Modal opened={!!inviteUrl} onClose={() => setInviteUrl('')} title="Lien personnel d’inscription"><Stack><Text size="sm">À transmettre manuellement au professeur. Valable 14 jours, une seule fois. Un nouveau lien invalide le précédent.</Text>
      <TextInput readOnly value={inviteUrl} onFocus={e => e.currentTarget.select()} /><Button leftSection={<Copy size={15} />} onClick={() => navigator.clipboard.writeText(inviteUrl).then(() => notifications.show({ message: 'Lien copié' })).catch(showError)}>Copier le lien</Button></Stack></Modal>
  </Stack>
}
