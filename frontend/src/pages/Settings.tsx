// Paramètres (§9.6) : Mon compte, API, Imprimantes, Calibration, Pédagogie,
// Documents (éditeur de templates), Système (dont mode démo désactivable).
import {
  ActionIcon, Alert, Badge, Button, Card, ColorInput, FileButton, Group, Modal,
  PasswordInput, Stack, Switch, Table, Tabs, Text, TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, Database, FileText, FlaskConical, KeyRound, Printer, Ruler,
  Save, SlidersHorizontal, Trash2, UserRound,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, getToken } from '../api'
import TemplateEditor from '../components/TemplateEditor'
import { useAppState } from '../state/AppState'

type Me = { id: string; email: string; display_name: string; role: string }
type Provider = { provider: string; secret_preview: string; active: boolean }
type PrintersInfo = {
  local: { name: string; default?: boolean; status: string }[]
  network: { name: string; uri: string; status: string }[]
}
type Build = { sha: string; time: string }
type ClassRow = {
  id: string; name: string; grade_level: string; school_year: string | null
  is_mock: boolean; archived: boolean; student_count: number; assessment_count: number
}
type StudentRow = {
  id: string; first_name: string; last_name: string; class_name: string
  active: boolean; copy_count: number
}
type AssessmentRow = {
  id: string; title: string; type: string; status: string; class_name: string
  created_at: string; copy_count: number; scan_batch_count: number
}
type CorrectionRow = {
  id: string; assessment_title: string; class_name: string; status: string
  page_count: number; created_at: string
}
type DeleteKind = 'classes' | 'students' | 'assessments' | 'corrections'
type SystemStatus = {
  version: string; build?: Build
  database: { ok: boolean; url_scheme: string }
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
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [netName, setNetName] = useState('')
  const [netUri, setNetUri] = useState('')
  const [webBuild, setWebBuild] = useState<Build | null>(null)
  const [dataClasses, setDataClasses] = useState<ClassRow[]>([])
  const [dataStudents, setDataStudents] = useState<StudentRow[]>([])
  const [dataAssessments, setDataAssessments] = useState<AssessmentRow[]>([])
  const [dataCorrections, setDataCorrections] = useState<CorrectionRow[]>([])
  const [confirmTarget, setConfirmTarget] = useState<{ kind: DeleteKind; id: string; label: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [purgeConfirmOpen, setPurgeConfirmOpen] = useState(false)
  const [purging, setPurging] = useState(false)
  const { refreshSystem } = useAppState()

  function refresh() {
    api.get<Me>('/api/auth/me').then(setMe)
    api.get<Provider[]>('/api/settings/providers').then(setProviders)
    api.get<Record<string, any>>('/api/settings/system').then(setSystem)
    api.get<SystemStatus>('/api/system/status').then(setStatus)
    api.get<PrintersInfo>('/api/printers').then(setPrinters)
    api.get<{ name: string; size: number }[]>('/api/system/backups').then(setBackups)
    api.get<any[]>('/api/system/calibration/profiles').then(setCalibrations)
    // build de l'interface web (image nginx) — absent en dev, servi en no-cache
    fetch('/build.json').then((r) => (r.ok ? r.json() : null)).then(setWebBuild)
      .catch(() => setWebBuild(null))
  }
  useEffect(refresh, [])

  // onglet Données : réservé au rôle admin côté API — silencieux si 403
  function refreshData() {
    api.get<ClassRow[]>('/api/data/classes').then(setDataClasses).catch(() => {})
    api.get<StudentRow[]>('/api/data/students').then(setDataStudents).catch(() => {})
    api.get<AssessmentRow[]>('/api/data/assessments').then(setDataAssessments).catch(() => {})
    api.get<CorrectionRow[]>('/api/data/corrections').then(setDataCorrections).catch(() => {})
  }
  useEffect(refreshData, [])

  async function purgeBank() {
    setPurging(true)
    try {
      const r = await api.post<{ exercises_deleted: number; extractions_reset: number }>(
        '/api/content/bank/purge')
      notifications.show({
        color: 'green',
        message: `Banque purgée : ${r.exercises_deleted} exercice(s) supprimé(s), `
          + `${r.extractions_reset} extraction(s) réinitialisée(s) — la prochaine `
          + 'génération repart de zéro.',
      })
      setPurgeConfirmOpen(false)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setPurging(false)
    }
  }

  async function confirmDelete() {
    if (!confirmTarget) return
    setDeleting(true)
    try {
      await api.del(`/api/data/${confirmTarget.kind}/${confirmTarget.id}`)
      notifications.show({ color: 'green', message: 'Supprimé définitivement' })
      setConfirmTarget(null)
      refreshData()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setDeleting(false)
    }
  }

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
    const secret = edit[provider] || ''
    await api.post('/api/settings/providers', { provider, secret, active: true })
    notifications.show({ color: 'green', message: `${provider} enregistré` })
    refresh()
  }

  async function setMock(enabled: boolean) {
    await api.post('/api/settings/system', { key: 'mock_mode', value: { enabled } })
    notifications.show({
      color: 'blue',
      message: enabled
        ? 'Mode démo activé — la classe de démonstration réapparaît'
        : 'Mode démo désactivé — toutes les données de démonstration sont masquées',
    })
    refresh()
    refreshSystem() // met à jour le badge « démo » global et les boutons de simulation
  }

  async function saveColor(key: string, value: string) {
    await api.post('/api/settings/system', { key, value: { value } })
    notifications.show({ color: 'green', message: 'Couleur enregistrée' })
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

  return (
    <Stack>
      <Title order={2}>Paramètres</Title>
      <Tabs defaultValue="compte" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="compte" leftSection={<UserRound size={15} />}>Mon compte</Tabs.Tab>
          <Tabs.Tab value="api" leftSection={<KeyRound size={15} />}>API</Tabs.Tab>
          <Tabs.Tab value="imprimantes" leftSection={<Printer size={15} />}>Imprimantes</Tabs.Tab>
          <Tabs.Tab value="calibration" leftSection={<Ruler size={15} />}>Calibration</Tabs.Tab>
          <Tabs.Tab value="pedagogie" leftSection={<SlidersHorizontal size={15} />}>Pédagogie</Tabs.Tab>
          <Tabs.Tab value="documents" leftSection={<FileText size={15} />}>Documents</Tabs.Tab>
          <Tabs.Tab value="systeme" leftSection={<Database size={15} />}>Système</Tabs.Tab>
          <Tabs.Tab value="donnees" leftSection={<Trash2 size={15} />}>Données</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="compte" pt="md">
          <Card withBorder maw={420}>
            {me && (
              <Group justify="space-between" mb="md">
                <div>
                  <Text fw={600}>{me.display_name}</Text>
                  <Text size="sm" c="dimmed">{me.email}</Text>
                </div>
                <Badge variant="light">{me.role}</Badge>
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
                leftSection={<Save size={14} />} disabled={!curPwd || !newPwd}>
                Mettre à jour le mot de passe
              </Button>
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="api" pt="md">
          <Stack>
            <Text size="sm" c="dimmed">
              Sans clé, un service reste en mode simulé. Les clés sont chiffrées au repos
              et jamais renvoyées intégralement.
            </Text>
            {(['mathpix', 'deepseek-flash', 'deepseek-pro', 'anthropic', 'mistral', 'gemini'] as const).map((p) => {
              const row = providers.find((x) => x.provider === p)
              const labels: Record<string, string> = {
                'deepseek-flash': 'DeepSeek Flash',
                'deepseek-pro': 'DeepSeek Pro',
                mistral: 'Mistral OCR (extraction Sésamaths)',
                gemini: 'Gemini (création d\'exercices)',
              }
              return (
                <Card key={p} withBorder>
                  <Group justify="space-between">
                    <Group>
                      <Text fw={600}>{labels[p] ?? p}</Text>
                      {row?.active && row.secret_preview
                        ? <Badge variant="light" color="green">configuré {row.secret_preview}</Badge>
                        : <Badge variant="light" color="gray"
                            leftSection={<FlaskConical size={11} />}>simulé</Badge>}
                    </Group>
                  </Group>
                  <TextInput mt="sm" label={p === 'mathpix' ? 'app_id:app_key' : 'Clé API'} type="password"
                    onChange={(e) => setEdit({ ...edit, [p]: e.target.value })} />
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
                    <Printer size={14} />
                    <Text size="sm">{p.name}</Text>
                    {p.default && <Badge size="xs" variant="light" color="blue">par défaut</Badge>}
                    <Badge size="xs" color="gray" variant="light">{p.status}</Badge>
                  </Group>
                ))
                : <Text size="sm" c="dimmed">Aucune file CUPS détectée sur cette machine.</Text>}
            </Card>
            <Card withBorder>
              <Text fw={600} mb="xs">Imprimantes réseau (IPP, pilotées depuis le NAS)</Text>
              {printers?.network.map((p) => (
                <Group key={p.name} gap="xs" py={2}>
                  <Text size="sm">{p.name}</Text>
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
            <Text fw={600}>Assistant de calibration imprimante/scanner</Text>
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
          <Card withBorder maw={640}>
            <Table>
              <Table.Tbody>
                <Table.Tr>
                  <Table.Td>Seuil de courbe d'oubli (probabilité de rappel)</Table.Td>
                  <Table.Td>{system.forgetting_threshold?.value ?? 0.8}</Table.Td>
                </Table.Tr>
                <Table.Tr>
                  <Table.Td>Variation automatique max du niveau (1-10) par cycle de révision</Table.Td>
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
          <Stack>
            <TemplateEditor />
            <Card withBorder maw={640}>
              <Text fw={600} mb="xs">Couleurs techniques</Text>
              <Group grow>
                <ColorInput size="xs" label="Zones de réponse élève (dropout)"
                  description="Supprimée avant OCR — garder un ton clair"
                  value={system.dropout_color?.value ?? '#F5B7A8'}
                  onChangeEnd={(v) => saveColor('dropout_color', v)} />
                <ColorInput size="xs" label="Encre de correction (overlay)"
                  value={system.correction_color?.value ?? '#C62828'}
                  onChangeEnd={(v) => saveColor('correction_color', v)} />
              </Group>
              <Text size="xs" c="dimmed" mt="sm">
                Figé pour le repérage scanner : QR 24 mm signé HMAC (haut droit),
                3 fiduciels AprilTag 11 mm (coins), marges 9 mm.
              </Text>
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="systeme" pt="md">
          <Stack>
            {status && (
              <Card withBorder>
                <Text fw={600} mb="xs">État des services</Text>
                <Group gap="lg">
                  <Badge variant="light" color={status.database.ok ? 'green' : 'red'}>
                    Base {status.database.url_scheme} {status.database.ok ? 'OK' : 'KO'}
                  </Badge>
                  <Badge variant="light" color={status.mathalea.status === 'ok' ? 'green' : 'red'}>
                    MathALÉA {status.mathalea.status === 'ok'
                      ? `v${status.mathalea.mathaleaVersion} (${status.mathalea.exercises} exos)`
                      : 'injoignable'}
                  </Badge>
                  <Badge variant="light" color={status.disk.alert ? 'red' : 'green'}>
                    Disque {status.disk.free_gb} / {status.disk.total_gb} Go libres
                  </Badge>
                </Group>
                <Group mt="sm">
                  <Button size="xs" variant="light" onClick={syncMathalea}>
                    Synchroniser le catalogue MathALÉA
                  </Button>
                </Group>
                <Text size="xs" c="dimmed" mt="sm">
                  Version {status.version}
                  {status.build?.sha && status.build.sha !== 'dev' &&
                    ` — API build ${status.build.sha}${status.build.time ? ` (${status.build.time})` : ''}`}
                  {webBuild?.sha && webBuild.sha !== 'dev' &&
                    ` · Web build ${webBuild.sha}`}
                  {status.build?.sha && webBuild?.sha && status.build.sha !== webBuild.sha &&
                    ' — ⚠ web et API sur des builds différents (mise à jour en cours ou incomplète)'}
                </Text>
              </Card>
            )}
            <Card withBorder>
              <Group justify="space-between">
                <div>
                  <Text fw={600}>Sauvegardes</Text>
                  <Text size="sm" c="dimmed">
                    Dump de la base dans /data/backups — rétention 30 fichiers.
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
              <Group justify="space-between" align="flex-start">
                <div>
                  <Group gap={6}>
                    <FlaskConical size={16} />
                    <Text fw={600}>Mode démonstration</Text>
                  </Group>
                  <Text size="sm" c="dimmed" maw={480}>
                    Classe fictive « 5e Mock » et fournisseurs simulés pour découvrir
                    l'application sans clé API ni scanner. Une fois désactivé, plus
                    aucune donnée ni bouton de démonstration n'apparaît.
                  </Text>
                </div>
                <Switch checked={system.mock_mode?.enabled ?? false}
                  onChange={(e) => setMock(e.currentTarget.checked)}
                  label={system.mock_mode?.enabled ? 'Activé' : 'Désactivé'} />
              </Group>
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="donnees" pt="md">
          <Stack>
            <Alert color="red" variant="light" icon={<AlertTriangle size={16} />}>
              Suppression définitive et irréversible — aucune corbeille. Pour les classes et
              sujets, tout ce qui en dépend (élèves, copies, scans, PDF/images sur le disque)
              disparaît avec.
            </Alert>

            <Card withBorder style={{ borderColor: 'var(--mantine-color-red-6)' }}>
              <Text fw={600} mb={2}>Banque d'exercices (Sésamaths)</Text>
              <Text size="sm" c="dimmed" mb="sm">
                Supprime TOUS les exercices de la banque (quelle que soit leur source) ainsi
                que l'état d'extraction Sésamaths déjà en cache — pour repartir d'une banque
                vide et propre si des exercices étranges ou répétés s'y sont accumulés. La
                prochaine génération réextrait tout depuis le manuel.
              </Text>
              <Button color="red" variant="outline" size="xs" leftSection={<Trash2 size={14} />}
                onClick={() => setPurgeConfirmOpen(true)}>
                Purger toute la banque
              </Button>
            </Card>

            <Card withBorder>
              <Text fw={600} mb="xs">Classes ({dataClasses.length})</Text>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Nom</Table.Th><Table.Th>Cycle</Table.Th>
                    <Table.Th>Élèves</Table.Th><Table.Th>Sujets</Table.Th><Table.Th /></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {dataClasses.map((c) => (
                    <Table.Tr key={c.id}>
                      <Table.Td>{c.name} {c.archived && <Badge size="xs" ml={6} color="gray">archivée</Badge>}
                        {c.is_mock && <Badge size="xs" ml={6} color="grape">démo</Badge>}</Table.Td>
                      <Table.Td>{c.grade_level}</Table.Td>
                      <Table.Td>{c.student_count}</Table.Td>
                      <Table.Td>{c.assessment_count}</Table.Td>
                      <Table.Td>
                        <ActionIcon color="red" variant="subtle" onClick={() => setConfirmTarget({
                          kind: 'classes', id: c.id,
                          label: `la classe « ${c.name} » (${c.student_count} élève(s), ${c.assessment_count} sujet(s))`,
                        })}>
                          <Trash2 size={15} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>

            <Card withBorder>
              <Text fw={600} mb="xs">Élèves ({dataStudents.length})</Text>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Nom</Table.Th><Table.Th>Classe</Table.Th>
                    <Table.Th>Copies</Table.Th><Table.Th>Actif</Table.Th><Table.Th /></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {dataStudents.map((s) => (
                    <Table.Tr key={s.id}>
                      <Table.Td>{s.last_name} {s.first_name}</Table.Td>
                      <Table.Td>{s.class_name}</Table.Td>
                      <Table.Td>{s.copy_count}</Table.Td>
                      <Table.Td>{s.active
                        ? <Badge size="xs" color="green" variant="light">oui</Badge>
                        : <Badge size="xs" color="gray" variant="light">désactivé</Badge>}</Table.Td>
                      <Table.Td>
                        <ActionIcon color="red" variant="subtle" onClick={() => setConfirmTarget({
                          kind: 'students', id: s.id,
                          label: `l'élève « ${s.last_name} ${s.first_name} » (${s.copy_count} copie(s))`,
                        })}>
                          <Trash2 size={15} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>

            <Card withBorder>
              <Text fw={600} mb="xs">Sujets ({dataAssessments.length})</Text>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Titre</Table.Th><Table.Th>Classe</Table.Th><Table.Th>Statut</Table.Th>
                    <Table.Th>Copies</Table.Th><Table.Th>Lots scannés</Table.Th><Table.Th /></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {dataAssessments.map((a) => (
                    <Table.Tr key={a.id}>
                      <Table.Td>{a.title}</Table.Td>
                      <Table.Td>{a.class_name}</Table.Td>
                      <Table.Td><Badge size="xs" variant="light">{a.status}</Badge></Table.Td>
                      <Table.Td>{a.copy_count}</Table.Td>
                      <Table.Td>{a.scan_batch_count}</Table.Td>
                      <Table.Td>
                        <ActionIcon color="red" variant="subtle" onClick={() => setConfirmTarget({
                          kind: 'assessments', id: a.id,
                          label: `le sujet « ${a.title} » (${a.copy_count} copie(s), ${a.scan_batch_count} lot(s) scanné(s))`,
                        })}>
                          <Trash2 size={15} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>

            <Card withBorder>
              <Text fw={600} mb="xs">Corrections ({dataCorrections.length})</Text>
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Sujet</Table.Th><Table.Th>Classe</Table.Th><Table.Th>Statut</Table.Th>
                    <Table.Th>Pages</Table.Th><Table.Th /></Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {dataCorrections.map((b) => (
                    <Table.Tr key={b.id}>
                      <Table.Td>{b.assessment_title}</Table.Td>
                      <Table.Td>{b.class_name}</Table.Td>
                      <Table.Td><Badge size="xs" variant="light">{b.status}</Badge></Table.Td>
                      <Table.Td>{b.page_count}</Table.Td>
                      <Table.Td>
                        <ActionIcon color="red" variant="subtle" onClick={() => setConfirmTarget({
                          kind: 'corrections', id: b.id,
                          label: `la correction du sujet « ${b.assessment_title} » (${b.page_count} page(s) scannée(s))`,
                        })}>
                          <Trash2 size={15} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>
          </Stack>
        </Tabs.Panel>
      </Tabs>

      <Modal opened={!!confirmTarget} onClose={() => setConfirmTarget(null)}
        title={<Text fw={650}>Confirmer la suppression</Text>}>
        <Stack>
          <Text size="sm">Supprimer définitivement {confirmTarget?.label} ?</Text>
          <Text size="xs" c="dimmed">Cette action est irréversible, y compris les fichiers stockés.</Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setConfirmTarget(null)}>Annuler</Button>
            <Button color="red" loading={deleting} onClick={confirmDelete}>Supprimer définitivement</Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={purgeConfirmOpen} onClose={() => setPurgeConfirmOpen(false)}
        title={<Text fw={650}>Confirmer la purge de la banque</Text>}>
        <Stack>
          <Text size="sm">
            Supprimer définitivement TOUS les exercices de la banque (toutes sources) et
            réinitialiser l'état d'extraction Sésamaths ?
          </Text>
          <Text size="xs" c="dimmed">
            Cette action est irréversible. La prochaine génération réextrait tout depuis le
            manuel — les premières copies après la purge seront plus lentes à générer.
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={() => setPurgeConfirmOpen(false)}>Annuler</Button>
            <Button color="red" loading={purging} onClick={purgeBank}>Purger toute la banque</Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  )
}
