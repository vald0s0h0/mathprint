// Paramètres (§9.6) : Mon compte, API, Imprimantes, Calibration, Pédagogie, Documents, Système.
import {
  Badge, Button, Card, FileButton, Group, PasswordInput, Stack, Switch, Table, Tabs, Text,
  TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useEffect, useState } from 'react'
import { api, getToken } from '../api'

type Me = { id: string; email: string; display_name: string; role: string }
type Provider = { provider: string; model: string; secret_preview: string; active: boolean }
type PrintersInfo = {
  local: { name: string; default?: boolean; status: string }[]
  network: { name: string; uri: string; status: string }[]
}
type SystemStatus = {
  version: string; database: { ok: boolean; url_scheme: string }
  mathalea: { status?: string; mathaleaVersion?: string; exercises?: number }
  disk: { total_gb: number; free_gb: number; alert: boolean }
  mock_mode: boolean; last_backup: string | null
}

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null)
  const [curPwd, setCurPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [pwdLoading, setPwdLoading] = useState(false)
  const [providers, setProviders] = useState<Provider[]>([])
  const [system, setSystem] = useState<Record<string, any>>({})
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [printers, setPrinters] = useState<PrintersInfo | null>(null)
  const [backups, setBackups] = useState<{ name: string; size: number }[]>([])
  const [calibrations, setCalibrations] = useState<any[]>([])
  const [edit, setEdit] = useState<Record<string, { model: string; secret: string }>>({})
  const [netName, setNetName] = useState('')
  const [netUri, setNetUri] = useState('')

  function refresh() {
    api.get<Me>('/api/auth/me').then(setMe)
    api.get<Provider[]>('/api/settings/providers').then(setProviders)
    api.get<Record<string, any>>('/api/settings/system').then(setSystem)
    api.get<SystemStatus>('/api/system/status').then(setStatus)
    api.get<PrintersInfo>('/api/printers').then(setPrinters)
    api.get<{ name: string; size: number }[]>('/api/system/backups').then(setBackups)
    api.get<any[]>('/api/system/calibration/profiles').then(setCalibrations)
  }
  useEffect(refresh, [])

  async function changePassword() {
    if (newPwd.length < 8) {
      notifications.show({ color: 'red', message: 'Nouveau mot de passe : 8 caractères minimum' })
      return
    }
    if (newPwd !== confirmPwd) {
      notifications.show({ color: 'red', message: 'Les mots de passe ne correspondent pas' })
      return
    }
    setPwdLoading(true)
    try {
      await api.post('/api/auth/me/password', { current_password: curPwd, new_password: newPwd })
      notifications.show({ color: 'green', message: 'Mot de passe mis à jour' })
      setCurPwd(''); setNewPwd(''); setConfirmPwd('')
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setPwdLoading(false)
    }
  }

  async function save(provider: string) {
    const e = edit[provider] || { model: '', secret: '' }
    await api.post('/api/settings/providers', { provider, ...e, active: true })
    notifications.show({ color: 'green', message: `${provider} enregistré` })
    refresh()
  }

  async function setMock(enabled: boolean) {
    await api.post('/api/settings/system', { key: 'mock_mode', value: { enabled } })
    notifications.show({ color: 'blue', message: `Mode mock ${enabled ? 'activé' : 'désactivé'}` })
    refresh()
  }

  async function syncMathalea() {
    try {
      const r = await api.post<{ created: number; updated: number; competency_mapped: number }>(
        '/api/assessments/exercises/sync-mathalea')
      notifications.show({
        color: 'green',
        message: `MathALÉA : ${r.created} créés, ${r.updated} mis à jour, ${r.competency_mapped} rattachés aux compétences`,
      })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function doBackup() {
    const r = await api.post<{ file: string }>('/api/system/backup')
    notifications.show({ color: 'green', message: `Sauvegarde : ${r.file}` })
    refresh()
  }

  async function registerNetwork() {
    await api.post('/api/printers/network', { name: netName, uri: netUri })
    setNetName(''); setNetUri('')
    refresh()
  }

  async function downloadCalibrationPage() {
    const res = await fetch('/api/system/calibration/page', {
      method: 'POST', headers: { Authorization: `Bearer ${getToken()}` },
    })
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'calibration_page.pdf'
    a.click()
  }

  async function uploadCalibrationScan(file: File | null) {
    if (!file) return
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r = await api.post<any>('/api/system/calibration/measure', fd)
      notifications.show({
        color: r.verdict === 'ok' ? 'green' : 'orange',
        message: `Échelle ${r.scale_x}×${r.scale_y}, rotation ${r.rotation_deg}° — ${r.verdict}`,
      })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  const defaults: Record<string, string> = {
    mathpix: 'v3/text', deepseek: 'deepseek-v4-flash', anthropic: 'claude-haiku-4-5-20251001',
  }

  return (
    <Stack>
      <Title order={2}>Paramètres</Title>
      <Tabs defaultValue="compte">
        <Tabs.List>
          <Tabs.Tab value="compte">Mon compte</Tabs.Tab>
          <Tabs.Tab value="api">API</Tabs.Tab>
          <Tabs.Tab value="imprimantes">Imprimantes</Tabs.Tab>
          <Tabs.Tab value="calibration">Calibration</Tabs.Tab>
          <Tabs.Tab value="pedagogie">Pédagogie</Tabs.Tab>
          <Tabs.Tab value="documents">Documents</Tabs.Tab>
          <Tabs.Tab value="systeme">Système</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="compte" pt="md">
          <Card withBorder maw={420}>
            {me && (
              <Group justify="space-between" mb="md">
                <div>
                  <Text fw={600}>{me.display_name}</Text>
                  <Text size="sm" c="dimmed">{me.email}</Text>
                </div>
                <Badge>{me.role}</Badge>
              </Group>
            )}
            <Stack gap="xs">
              <Text fw={600} size="sm">Changer le mot de passe</Text>
              <PasswordInput label="Mot de passe actuel" value={curPwd}
                onChange={(e) => setCurPwd(e.target.value)} />
              <PasswordInput label="Nouveau mot de passe" value={newPwd}
                description="8 caractères minimum"
                onChange={(e) => setNewPwd(e.target.value)} />
              <PasswordInput label="Confirmer le nouveau mot de passe" value={confirmPwd}
                onChange={(e) => setConfirmPwd(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && changePassword()} />
              <Button size="xs" onClick={changePassword} loading={pwdLoading}
                disabled={!curPwd || !newPwd}>
                Mettre à jour le mot de passe
              </Button>
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="api" pt="md">
          <Stack>
            <Text size="sm" c="dimmed">
              Les identifiants de modèles ne sont jamais codés en dur (RM-011). Les clés sont
              chiffrées au repos et jamais renvoyées intégralement.
            </Text>
            {(['mathpix', 'deepseek', 'anthropic'] as const).map((p) => {
              const row = providers.find((x) => x.provider === p)
              return (
                <Card key={p} withBorder>
                  <Group justify="space-between">
                    <Group>
                      <Text fw={600} tt="capitalize">{p}</Text>
                      {row?.active
                        ? <Badge color="green">configuré {row.secret_preview}</Badge>
                        : <Badge color="gray">mode mock</Badge>}
                    </Group>
                  </Group>
                  <Group mt="sm" grow>
                    <TextInput label="Modèle" placeholder={defaults[p]}
                      defaultValue={row?.model}
                      onChange={(e) => setEdit({ ...edit, [p]: { ...(edit[p] || { secret: '' }), model: e.target.value } })} />
                    <TextInput label={p === 'mathpix' ? 'app_id:app_key' : 'Clé API'} type="password"
                      onChange={(e) => setEdit({ ...edit, [p]: { ...(edit[p] || { model: '' }), secret: e.target.value } })} />
                  </Group>
                  <Button size="xs" mt="sm" onClick={() => save(p)}>Enregistrer</Button>
                </Card>
              )
            })}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="imprimantes" pt="md">
          <Stack>
            <Card withBorder>
              <Text fw={600} mb="xs">Imprimantes locales (CUPS du poste / NAS)</Text>
              {printers?.local.length
                ? printers.local.map((p) => (
                  <Group key={p.name} gap="xs" py={2}>
                    <Text size="sm">🖨 {p.name}</Text>
                    {p.default && <Badge size="xs" color="blue">par défaut</Badge>}
                    <Badge size="xs" color="gray" variant="light">{p.status}</Badge>
                  </Group>
                ))
                : <Text size="sm" c="dimmed">Aucune file CUPS détectée sur cette machine.</Text>}
            </Card>
            <Card withBorder>
              <Text fw={600} mb="xs">Imprimantes réseau (IPP, pilotées depuis le NAS)</Text>
              {printers?.network.map((p) => (
                <Group key={p.name} gap="xs" py={2}>
                  <Text size="sm">🌐 {p.name}</Text>
                  <Text size="xs" c="dimmed">{p.uri}</Text>
                </Group>
              ))}
              <Group mt="sm" gap="xs">
                <TextInput size="xs" placeholder="Nom" value={netName}
                  onChange={(e) => setNetName(e.target.value)} />
                <TextInput size="xs" placeholder="ipp://192.168.1.50/ipp/print" value={netUri}
                  onChange={(e) => setNetUri(e.target.value)} style={{ flex: 1 }} />
                <Button size="xs" onClick={registerNetwork} disabled={!netName || !netUri}>
                  Ajouter
                </Button>
              </Group>
              <Text size="xs" c="dimmed" mt="xs">
                Impression toujours à taille réelle 100 % (print-scaling=none) ; chaque job est journalisé.
              </Text>
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="calibration" pt="md">
          <Card withBorder>
            <Text fw={600}>Assistant de calibration imprimante/scanner (§11.5)</Text>
            <Text size="sm" c="dimmed" mt="xs">
              1. Télécharger la page test → 2. L'imprimer à 100 % → 3. La scanner →
              4. Déposer le scan : offsets, échelle et rotation sont mesurés sur les 4 marqueurs.
            </Text>
            <Group mt="sm">
              <Button size="xs" onClick={downloadCalibrationPage}>Télécharger la page test</Button>
              <FileButton onChange={uploadCalibrationScan} accept="application/pdf,image/*">
                {(props) => <Button size="xs" variant="light" {...props}>Déposer le scan de la page test</Button>}
              </FileButton>
            </Group>
            {calibrations.length > 0 && (
              <Table mt="md" striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Imprimante</Table.Th><Table.Th>Échelle X/Y</Table.Th>
                    <Table.Th>Rotation</Table.Th><Table.Th>Offset (mm)</Table.Th><Table.Th>Validé</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {calibrations.map((c) => (
                    <Table.Tr key={c.id}>
                      <Table.Td>{c.printer || '—'}</Table.Td>
                      <Table.Td>{c.scale_x} / {c.scale_y}</Table.Td>
                      <Table.Td>{c.rotation_deg}°</Table.Td>
                      <Table.Td>{c.offset_x_mm} / {c.offset_y_mm}</Table.Td>
                      <Table.Td><Text size="xs">{c.validated_at?.slice(0, 16)}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="pedagogie" pt="md">
          <Card withBorder>
            <Table>
              <Table.Tbody>
                <Table.Tr>
                  <Table.Td>Seuil de courbe d'oubli (probabilité de rappel)</Table.Td>
                  <Table.Td>{system.forgetting_threshold?.value ?? 0.8}</Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Td>Variation automatique max du niveau par cycle</Table.Td>
                  <Table.Td>±1</Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Td>Répartition entraînement</Table.Td>
                  <Table.Td>60 % consolidation / 30 % cible / 10 % défi</Table.Td>
                </Table.Tr>
              </Table.Tbody>
            </Table>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="documents" pt="md">
          <Card withBorder>
            <Table>
              <Table.Tbody>
                <Table.Tr>
                  <Table.Td>Couleur dropout des zones</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <div style={{ width: 18, height: 18, background: system.dropout_color?.value ?? '#F5B7A8', borderRadius: 3 }} />
                      {system.dropout_color?.value ?? '#F5B7A8'} (rouge saumon clair)
                    </Group>
                  </Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Td>Couleur de correction (overlay)</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <div style={{ width: 18, height: 18, background: system.correction_color?.value ?? '#C62828', borderRadius: 3 }} />
                      {system.correction_color?.value ?? '#C62828'}
                    </Group>
                  </Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Td>QR principal (identité)</Table.Td>
                  <Table.Td>24 mm, haut droit, unique par page, signé HMAC</Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Td>Fiduciels de placement</Table.Td>
                  <Table.Td>3 tags AprilTag 16h5 (11 mm) — un type par coin TL/BL/BR, translation/rotation/échelle uniquement</Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Td>Marges de page</Table.Td>
                  <Table.Td>9 mm</Table.Td>
                </Table.Tr>
              </Table.Tbody>
            </Table>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="systeme" pt="md">
          <Stack>
            {status && (
              <Card withBorder>
                <Text fw={600} mb="xs">État des services</Text>
                <Group gap="lg">
                  <Badge color={status.database.ok ? 'green' : 'red'}>
                    Base {status.database.url_scheme} {status.database.ok ? 'OK' : 'KO'}
                  </Badge>
                  <Badge color={status.mathalea.status === 'ok' ? 'green' : 'red'}>
                    MathALÉA {status.mathalea.status === 'ok'
                      ? `v${status.mathalea.mathaleaVersion} (${status.mathalea.exercises} exos)`
                      : 'injoignable'}
                  </Badge>
                  <Badge color={status.disk.alert ? 'red' : 'green'}>
                    Disque {status.disk.free_gb} / {status.disk.total_gb} Go libres
                  </Badge>
                </Group>
                <Group mt="sm">
                  <Button size="xs" variant="light" onClick={syncMathalea}>
                    Synchroniser le catalogue MathALÉA
                  </Button>
                </Group>
              </Card>
            )}
            <Card withBorder>
              <Group justify="space-between">
                <div>
                  <Text fw={600}>Sauvegardes</Text>
                  <Text size="sm" c="dimmed">
                    Dump de la base dans /data/backups — rétention 30 fichiers (§11.6).
                  </Text>
                </div>
                <Button size="xs" onClick={doBackup}>Sauvegarder maintenant</Button>
              </Group>
              {backups.slice(0, 5).map((b) => (
                <Group key={b.name} gap="xs" py={1}>
                  <Text size="xs" ff="monospace">{b.name}</Text>
                  <Text size="xs" c="dimmed">{(b.size / 1024).toFixed(0)} Ko</Text>
                </Group>
              ))}
            </Card>
            <Card withBorder>
              <Group justify="space-between">
                <div>
                  <Text fw={600}>Classe mock</Text>
                  <Text size="sm" c="dimmed">
                    Classe « 5e Mock » avec 5 élèves imaginaires pour les tests.
                  </Text>
                </div>
                <Switch checked={system.mock_mode?.enabled ?? true}
                  onChange={(e) => setMock(e.currentTarget.checked)} label="Mode mock" />
              </Group>
            </Card>
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  )
}
