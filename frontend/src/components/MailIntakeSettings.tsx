import {
  Alert, Badge, Box, Button, Card, Group, NumberInput, PasswordInput, Stack,
  Switch, TagsInput, Text, TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { CheckCircle2, Info, Mail, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

type MailIntakeConfig = {
  host: string
  port: number
  username: string
  password_preview: string
  folder: string
  poll_interval_s: number
  sender_allowlist: string[]
  active: boolean
  delete_after_import: boolean
  last_checked_at: string | null
  last_error: string | null
}

const EMPTY: MailIntakeConfig = {
  host: '', port: 993, username: '', password_preview: '', folder: 'INBOX',
  poll_interval_s: 120, sender_allowlist: [], active: false, delete_after_import: true,
  last_checked_at: null, last_error: null,
}

export default function MailIntakeSettings() {
  const [cfg, setCfg] = useState<MailIntakeConfig>(EMPTY)
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null)

  function refresh() {
    setLoading(true)
    api.get<MailIntakeConfig>('/api/settings/mail-intake')
      .then((c) => { setCfg(c); setTestResult(null) })
      .finally(() => setLoading(false))
  }

  useEffect(refresh, [])

  async function save() {
    setSaving(true)
    try {
      await api.post('/api/settings/mail-intake', {
        host: cfg.host, port: cfg.port, username: cfg.username, password,
        folder: cfg.folder, poll_interval_s: cfg.poll_interval_s,
        sender_allowlist: cfg.sender_allowlist, active: cfg.active,
        delete_after_import: cfg.delete_after_import,
      })
      setPassword('')
      notifications.show({ color: 'green', message: 'Configuration enregistrée' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSaving(false)
    }
  }

  async function testConnection() {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.post<{ ok: boolean; error?: string }>(
        '/api/settings/mail-intake/test', {
          host: cfg.host, port: cfg.port, username: cfg.username,
          password, folder: cfg.folder,
        })
      setTestResult(result)
    } catch (e) {
      setTestResult({ ok: false, error: (e as Error).message })
    } finally {
      setTesting(false)
    }
  }

  return (
    <Stack gap="md">
      <Card withBorder>
        <Group justify="space-between" align="flex-start" mb="sm">
          <Group gap="xs">
            <Mail size={18} />
            <Box>
              <Title order={5}>Scans par mail</Title>
              <Text size="sm" c="dimmed">
                Relève automatique d’une boîte mail dédiée (ex. celle d’un scanner réseau ADF) :
                les copies reçues en pièce jointe sont déposées comme au bac à sable, sans action du professeur.
              </Text>
            </Box>
          </Group>
          <Switch
            checked={cfg.active}
            label={cfg.active ? 'Active' : 'Inactive'}
            onChange={(e) => setCfg({ ...cfg, active: e.currentTarget.checked })}
          />
        </Group>

        {cfg.last_error && (
          <Alert color="red" icon={<XCircle size={16} />} mb="md">
            Dernière relève en échec : {cfg.last_error}
          </Alert>
        )}
        {!cfg.last_error && cfg.last_checked_at && (
          <Text size="xs" c="dimmed" mb="md">
            Dernière relève : {new Date(cfg.last_checked_at).toLocaleString('fr-FR', {
              dateStyle: 'short', timeStyle: 'short',
            })}
          </Text>
        )}

        <Stack gap="sm">
          <Group grow>
            <TextInput label="Serveur IMAP" placeholder="imap.gmail.com"
              value={cfg.host} onChange={(e) => setCfg({ ...cfg, host: e.currentTarget.value })} />
            <NumberInput label="Port" value={cfg.port} min={1} max={65535}
              onChange={(v) => setCfg({ ...cfg, port: Number(v) || 993 })} />
          </Group>
          <Group grow>
            <TextInput label="Adresse mail" placeholder="scans@etablissement.fr"
              value={cfg.username} onChange={(e) => setCfg({ ...cfg, username: e.currentTarget.value })} />
            <PasswordInput label="Mot de passe"
              placeholder={cfg.password_preview ? `Enregistré (${cfg.password_preview})` : 'Mot de passe ou mot de passe d’application'}
              value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
          </Group>
          <Group grow>
            <TextInput label="Dossier IMAP" value={cfg.folder}
              onChange={(e) => setCfg({ ...cfg, folder: e.currentTarget.value })} />
            <NumberInput label="Intervalle de relève (secondes)" value={cfg.poll_interval_s}
              min={30} step={30}
              onChange={(v) => setCfg({ ...cfg, poll_interval_s: Number(v) || 120 })} />
          </Group>
          <TagsInput label="Expéditeurs autorisés" placeholder="Laisser vide pour accepter tout expéditeur"
            description="Un mail dont l’expéditeur n’est pas dans cette liste est ignoré."
            value={cfg.sender_allowlist}
            onChange={(value) => setCfg({ ...cfg, sender_allowlist: value })} />
          <Switch
            checked={cfg.delete_after_import}
            label="Supprimer les mails après traitement"
            description="Sur Gmail, ceci déplace le mail vers la Corbeille (purge définitive après 30 jours) plutôt que de le supprimer immédiatement."
            onChange={(e) => setCfg({ ...cfg, delete_after_import: e.currentTarget.checked })}
          />
        </Stack>

        <Group mt="md" justify="space-between">
          <Group gap="sm">
            <Button variant="light" loading={testing} onClick={testConnection}
              disabled={!cfg.host || !cfg.username}>
              Tester la connexion
            </Button>
            {testResult && (
              <Badge color={testResult.ok ? 'green' : 'red'} variant="light"
                leftSection={testResult.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}>
                {testResult.ok ? 'Connexion réussie' : testResult.error || 'Échec'}
              </Badge>
            )}
          </Group>
          <Button loading={saving || loading} onClick={save}>Enregistrer</Button>
        </Group>

        <Alert color="blue" icon={<Info size={16} />} mt="md" variant="light">
          Seuls les mails avec une pièce jointe PDF/JPEG/PNG/HEIC contenant une copie MathPrint
          déjà générée (QR reconnu) sont pris en compte — les autres pièces jointes sont ignorées.
        </Alert>
      </Card>
    </Stack>
  )
}
