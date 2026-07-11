import { Button, Card, Center, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'

export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function submit() {
    setLoading(true)
    try {
      const r = await api.post<{ token: string }>('/api/auth/login', { email, password })
      setToken(r.token)
      navigate('/')
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <Center h="100vh">
      <Card w={380} shadow="md" padding="xl" withBorder>
        <Stack>
          <Title order={2}>MathPrint</Title>
          <Text c="dimmed" size="sm">
            Génération, correction automatisée et suivi adaptatif en mathématiques
          </Text>
          <TextInput label="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <PasswordInput label="Mot de passe" value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()} />
          <Button onClick={submit} loading={loading}>Connexion</Button>
        </Stack>
      </Card>
    </Center>
  )
}
