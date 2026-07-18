// Écran de démarrage (premier lancement) : crée l'unique compte
// administrateur tant que la base est vide (routers/setup.py).
import {
  Accordion, Button, Card, Center, PasswordInput, Stack, Text, TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'

export default function Setup({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [mathpix, setMathpix] = useState('')
  const [deepseek, setDeepseek] = useState('')
  const [anthropic, setAnthropic] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function submit() {
    if (!email.trim() || !displayName.trim()) {
      notifications.show({ color: 'red', message: 'E-mail et prénom requis' })
      return
    }
    if (password.length < 8) {
      notifications.show({ color: 'red', message: 'Mot de passe : 8 caractères minimum' })
      return
    }
    if (password !== confirm) {
      notifications.show({ color: 'red', message: 'Les mots de passe ne correspondent pas' })
      return
    }
    setLoading(true)
    try {
      const providers: Record<string, { model: string; secret: string }> = {}
      if (mathpix.trim()) providers.mathpix = { model: '', secret: mathpix.trim() }
      if (deepseek.trim()) providers.deepseek = { model: '', secret: deepseek.trim() }
      if (anthropic.trim()) providers.anthropic = { model: '', secret: anthropic.trim() }
      const r = await api.post<{ token: string }>('/api/setup', {
        email: email.trim(), display_name: displayName.trim(), password, providers,
      })
      setToken(r.token)
      notifications.show({ color: 'green', message: `Bienvenue ${displayName.trim()}, MathPrint est prêt` })
      onDone()
      navigate('/')
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <Center h="100vh" p="md">
      <Card w={460} shadow="md" padding="xl" withBorder>
        <Stack>
          <Title order={2}>Bienvenue sur MathPrint</Title>
          <Text c="dimmed" size="sm">
            Premier démarrage : créez votre compte administrateur pour commencer.
            C'est le seul écran de ce type — il disparaît une fois le compte créé.
          </Text>
          <TextInput label="E-mail" required value={email}
            onChange={(e) => setEmail(e.target.value)} />
          <TextInput label="Votre prénom" required value={displayName}
            onChange={(e) => setDisplayName(e.target.value)} />
          <PasswordInput label="Mot de passe" required value={password}
            description="8 caractères minimum"
            onChange={(e) => setPassword(e.target.value)} />
          <PasswordInput label="Confirmer le mot de passe" required value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()} />

          <Accordion variant="contained">
            <Accordion.Item value="providers">
              <Accordion.Control>
                Clés API (facultatif)
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap="xs">
                  <Text size="xs" c="dimmed">
                    Sans clé, ces services tournent sur un repli hors-ligne
                    (données factices) ; vous pourrez les ajouter ou les changer
                    plus tard dans Paramètres → API.
                  </Text>
                  <TextInput label="Mathpix (app_id:app_key)" value={mathpix}
                    onChange={(e) => setMathpix(e.target.value)} />
                  <TextInput label="DeepSeek (clé API)" value={deepseek}
                    onChange={(e) => setDeepseek(e.target.value)} />
                  <TextInput label="Anthropic / Claude (clé API)" value={anthropic}
                    onChange={(e) => setAnthropic(e.target.value)} />
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <Button onClick={submit} loading={loading}>Créer le compte et démarrer</Button>
        </Stack>
      </Card>
    </Center>
  )
}
