import { Alert, Button, Card, Center, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'

export default function Register() {
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('invitation') || '')
  const [name, setName] = useState(''); const [email, setEmail] = useState(''); const [password, setPassword] = useState('')
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const navigate = useNavigate()
  useEffect(() => {
    if (!token) { setError('Lien d’inscription manquant. Demandez votre lien personnel à Kironina.'); return }
    api.post<{ name: string }>('/api/auth/invitation/check', { token }).then(r => setName(r.name)).catch(e => setError(e.message))
  }, [token])
  async function submit() {
    setBusy(true); setError('')
    try {
      const r = await api.post<{ token: string }>('/api/auth/register', { token, email, password })
      setToken(r.token); window.history.replaceState(null, '', '/'); navigate('/')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <Center mih="100vh"><Card withBorder shadow="sm" radius="lg" p="xl" w={420}><Stack>
    <Title order={2}>Bienvenue sur Kironina</Title>
    {name && <Text>{name}, activez votre accès pour retrouver vos classes, vos élèves et vos résultats.</Text>}
    {error && <Alert color="red">{error}</Alert>}
    {name && <><TextInput label="Adresse e-mail" type="email" autoComplete="email" value={email} onChange={e => setEmail(e.currentTarget.value)} />
      <PasswordInput label="Mot de passe" description="Au moins 10 caractères" autoComplete="new-password" value={password} onChange={e => setPassword(e.currentTarget.value)} />
      <Button loading={busy} disabled={!email || password.length < 10} onClick={submit}>Créer mon accès</Button></>}
  </Stack></Card></Center>
}
