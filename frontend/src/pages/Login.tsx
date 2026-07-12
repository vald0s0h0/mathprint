import {
  Button, Card, Center, Group, PasswordInput, Stack, Text, TextInput, ThemeIcon,
  Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { GraduationCap, LogIn } from 'lucide-react'
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
      <Card w={380} shadow="md" padding="xl" withBorder radius="lg">
        <Stack>
          <Group gap="sm">
            <ThemeIcon size={44} radius="md" variant="light">
              <GraduationCap size={26} />
            </ThemeIcon>
            <div>
              <Title order={2}>MathPrint</Title>
              <Text c="dimmed" size="xs">
                Génération, correction automatisée et suivi adaptatif
              </Text>
            </div>
          </Group>
          <TextInput label="Email" value={email} autoComplete="username"
            onChange={(e) => setEmail(e.target.value)} />
          <PasswordInput label="Mot de passe" value={password} autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()} />
          <Button onClick={submit} loading={loading} leftSection={<LogIn size={16} />}>
            Connexion
          </Button>
        </Stack>
      </Card>
    </Center>
  )
}
