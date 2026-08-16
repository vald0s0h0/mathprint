import {
  Alert, Badge, Box, Button, Card, Checkbox, Group, Image, Modal, Paper, Stack,
  Table, Text, TextInput, Timeline, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  ClipboardList, Eye, Info, Network, Printer, RefreshCw, ScanLine,
  TestTube2, Trash2,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { api } from '../api'

export type PrinterRow = {
  id?: string
  name: string
  display_name?: string
  device_name?: string
  device_platform?: string
  connector_id?: string
  source: 'cups_local' | 'connector_local' | 'network_ipp'
  uri?: string
  status: string
  available: boolean
  last_seen_at?: string | null
  system_default?: boolean
  app_default: boolean
  duplex: boolean
  pickup_reverse_order: boolean
  output_reverse_order: boolean
  adf_reverse_order: boolean
}

export type PrintersInfo = {
  local: PrinterRow[]
  network: PrinterRow[]
  connectors: {
    id: string
    name: string
    platform: string
    status: 'online' | 'offline'
    last_seen_at?: string | null
    printer_count: number
  }[]
  online_connector_count: number
  printing_available: boolean
}

function lastSeenLabel(value?: string | null) {
  if (!value) return 'jamais vu'
  return `dernière activité ${new Date(value).toLocaleString('fr-FR', {
    dateStyle: 'short', timeStyle: 'short',
  })}`
}

function PilePicture({ reversed, caption, muted = false, onInvert, inverting = false }: {
  reversed: boolean; caption: string; muted?: boolean
  onInvert?: () => void; inverting?: boolean
}) {
  return (
    <Paper withBorder p="sm" maw={430} bg={muted ? 'gray.0' : undefined}>
      <Group gap="lg" wrap="nowrap" align="center">
        <Image src={`/printer-order/${reversed ? '321' : '123'}.png`}
          alt={reversed ? 'Pile de feuilles dans l’ordre 3, 2, 1' : 'Pile de feuilles dans l’ordre 1, 2, 3'}
          w={138} h={138} fit="contain" />
        <Box style={{ flex: 1 }}>
          <Badge variant="light" color={muted ? 'gray' : reversed ? 'orange' : 'blue'} mb={6}>
            {reversed ? '3 → 2 → 1' : '1 → 2 → 3'}
          </Badge>
          <Text size="sm" fw={650}>{caption}</Text>
          {muted && <Text size="xs" c="dimmed" mt={4}>L’ordre final n’a plus d’importance.</Text>}
          {onInvert && (
            <Button mt="sm" size="compact-sm" variant="light" leftSection={<RefreshCw size={13} />}
              loading={inverting} onClick={onInvert}>
              Inverser
            </Button>
          )}
        </Box>
      </Group>
    </Paper>
  )
}

export default function PrinterSettings({ printers, refresh }: {
  printers: PrintersInfo | null
  refresh: () => void
}) {
  const [netName, setNetName] = useState('')
  const [netUri, setNetUri] = useState('')
  const [saving, setSaving] = useState<string | null>(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [previewName, setPreviewName] = useState<string | null>(null)

  const rows = useMemo(() => printers ? [...printers.local, ...printers.network] : [], [printers])
  const preview = rows.find((p) => p.name === previewName) ?? null

  async function setPreference(row: PrinterRow, field: keyof Pick<PrinterRow,
    'duplex' | 'pickup_reverse_order' | 'output_reverse_order' | 'app_default'
    | 'adf_reverse_order'>, value: boolean) {
    // La destination préférée sert de repli lorsque plusieurs postes sont en
    // ligne. Le dernier choix du navigateur reste prioritaire.
    if (field === 'app_default' && !value) return
    setSaving(`${row.name}:${field}`)
    try {
      await api.patch('/api/printers/preferences', { name: row.name, [field]: value })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSaving(null)
    }
  }

  async function registerNetwork() {
    try {
      await api.post('/api/printers/network', { name: netName.trim(), uri: netUri.trim() })
      setNetName(''); setNetUri('')
      notifications.show({ color: 'green', message: 'Imprimante réseau ajoutée' })
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function deleteNetwork(row: PrinterRow) {
    if (!row.id) return
    try {
      await api.del(`/api/printers/network/${encodeURIComponent(row.id)}`)
      notifications.show({ color: 'green', message: `${row.name} supprimée` })
      if (previewName === row.name) setPreviewName(null)
      refresh()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  async function printTest(row: PrinterRow) {
    setTesting(row.name)
    try {
      await api.post('/api/printers/test', { printer: row.name })
      notifications.show({
        color: 'green',
        message: 'Deux feuilles de test envoyées en recto et dans l’ordre 1 → 2.',
      })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setTesting(null)
    }
  }

  const adfReversed = Boolean(preview?.adf_reverse_order)
  const pickupReversed = Boolean(preview?.pickup_reverse_order)
  const printerPileReversed = Boolean(preview
    && pickupReversed !== preview.output_reverse_order)

  return (
    <Stack gap="md">
      <Card withBorder>
        <Group justify="space-between" align="flex-start" mb="sm">
          <Box>
            <Text fw={700}>Imprimantes disponibles</Text>
            <Text size="sm" c="dimmed">
              Les réglages sont propres à MathPrint et peuvent différer du défaut de l’ordinateur.
            </Text>
          </Box>
          <Badge variant="light" color={printers?.printing_available ? 'green' : 'gray'}>
            {rows.filter((row) => row.available).length} disponible{rows.filter((row) => row.available).length > 1 ? 's' : ''}
          </Badge>
        </Group>

        {printers && printers.connectors.length > 0 && (
          <Group gap="xs" mb="md">
            {printers.connectors.map((connector) => (
              <Badge key={connector.id} variant="light"
                color={connector.status === 'online' ? 'green' : 'gray'}>
                {connector.name} · {connector.status === 'online' ? 'connecté' : 'hors ligne'}
                {' · '}{connector.printer_count} imprimante{connector.printer_count > 1 ? 's' : ''}
              </Badge>
            ))}
          </Group>
        )}

        {printers && printers.online_connector_count > 1 && (
          <Alert color="blue" icon={<Info size={16} />} mb="md">
            Plusieurs postes sont connectés. Une imprimante portant le même nom reste une destination distincte sur chaque poste.
          </Alert>
        )}

        {rows.length ? (
          <Table.ScrollContainer minWidth={1210}>
            <Table striped highlightOnHover verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Imprimante</Table.Th>
                  <Table.Th>Connexion</Table.Th>
                  <Table.Th ta="center">Destination préférée</Table.Th>
                  <Table.Th ta="center">Recto verso automatique</Table.Th>
                  <Table.Th ta="center">ADF inverse la pile</Table.Th>
                  <Table.Th ta="center">Prélèvement : dernière d’abord</Table.Th>
                  <Table.Th ta="center">Bac de réception inverse la pile</Table.Th>
                  <Table.Th ta="right">Vérifier</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row) => (
                  <Table.Tr key={`${row.source}:${row.name}`}>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Printer size={16} />
                        <Box>
                          <Text size="sm" fw={600}>{row.display_name || row.name}</Text>
                          {row.device_name && <Text size="xs" c="dimmed">
                            {row.device_name}{row.device_platform ? ` · ${row.device_platform === 'macos' ? 'macOS' : 'Windows'}` : ''}
                          </Text>}
                          {row.source === 'connector_local' && <Text size="xs"
                            c={row.available ? 'green' : 'dimmed'}>
                            {row.available ? 'Connecté' : `Hors ligne · ${lastSeenLabel(row.last_seen_at)}`}
                          </Text>}
                          {row.uri && <Text size="xs" c="dimmed">{row.uri}</Text>}
                        </Box>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Badge size="sm" variant="light"
                        color={!row.available ? 'gray' : row.source === 'network_ipp' ? 'violet' : row.source === 'connector_local' ? 'teal' : 'blue'}>
                        {row.source === 'cups_local' ? 'Locale · CUPS'
                          : row.source === 'connector_local'
                            ? `Connecteur · ${row.available ? 'en ligne' : 'hors ligne'}` : 'Réseau · IPP'}
                      </Badge>
                    </Table.Td>
                    <Table.Td ta="center">
                      <Box style={{ display: 'flex', justifyContent: 'center' }}>
                        <Checkbox aria-label={`Choisir ${row.name} par défaut dans MathPrint`}
                          checked={row.app_default}
                          disabled={saving === `${row.name}:app_default`}
                          onChange={(e) => setPreference(row, 'app_default', e.currentTarget.checked)} />
                      </Box>
                    </Table.Td>
                    <Table.Td ta="center">
                      <Box style={{ display: 'flex', justifyContent: 'center' }}>
                        <Checkbox aria-label={`Recto verso automatique sur ${row.name}`} checked={row.duplex}
                          disabled={saving === `${row.name}:duplex`}
                          onChange={(e) => setPreference(row, 'duplex', e.currentTarget.checked)} />
                      </Box>
                    </Table.Td>
                    <Table.Td ta="center">
                      <Box style={{ display: 'flex', justifyContent: 'center' }}>
                        <Checkbox aria-label={`ADF inversé pour ${row.name}`} checked={row.adf_reverse_order}
                          disabled={saving === `${row.name}:adf_reverse_order`}
                          onChange={(e) => setPreference(row, 'adf_reverse_order', e.currentTarget.checked)} />
                      </Box>
                    </Table.Td>
                    <Table.Td ta="center">
                      <Box style={{ display: 'flex', justifyContent: 'center' }}>
                        <Checkbox aria-label={`${row.name} commence par la dernière copie`}
                          checked={row.pickup_reverse_order}
                          disabled={saving === `${row.name}:pickup_reverse_order`}
                          onChange={(e) => setPreference(row, 'pickup_reverse_order', e.currentTarget.checked)} />
                      </Box>
                    </Table.Td>
                    <Table.Td ta="center">
                      <Box style={{ display: 'flex', justifyContent: 'center' }}>
                        <Checkbox aria-label={`Le bac de ${row.name} inverse la pile`}
                          checked={row.output_reverse_order}
                          disabled={saving === `${row.name}:output_reverse_order`}
                          onChange={(e) => setPreference(row, 'output_reverse_order', e.currentTarget.checked)} />
                      </Box>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={6} justify="flex-end" wrap="nowrap">
                        <Button size="compact-xs" variant="light" leftSection={<Eye size={13} />}
                          onClick={() => setPreviewName(row.name)}>Aperçu</Button>
                        <Button size="compact-xs" variant="default" leftSection={<TestTube2 size={13} />}
                          disabled={!row.available} loading={testing === row.name}
                          onClick={() => printTest(row)}>Test 1–2</Button>
                        {row.source === 'network_ipp' && (
                          <Button size="compact-xs" variant="subtle" color="red" px={7}
                            aria-label={`Supprimer ${row.name}`} onClick={() => deleteNetwork(row)}>
                            <Trash2 size={14} />
                          </Button>
                        )}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Alert color="gray" icon={<Info size={16} />}>
            Aucune file locale (CUPS ou connecteur) ni imprimante IPP détectée.
          </Alert>
        )}
      </Card>

      <Card withBorder>
        <Group gap="xs" mb="xs"><Network size={17} /><Text fw={700}>Ajouter une imprimante réseau</Text></Group>
        <Group gap="xs" align="flex-end">
          <TextInput size="xs" label="Nom" placeholder="Laser salle des profs" value={netName}
            onChange={(e) => setNetName(e.currentTarget.value)} />
          <TextInput size="xs" label="Adresse IPP" placeholder="ipp://192.168.1.50/ipp/print"
            value={netUri} onChange={(e) => setNetUri(e.currentTarget.value)} style={{ flex: 1 }} />
          <Button size="xs" onClick={registerNetwork} disabled={!netName.trim() || !netUri.trim()}>
            Ajouter
          </Button>
        </Group>
      </Card>

      <Card withBorder>
        <Title order={4}>Comment déterminer l’ordre de mon imprimante et de mon ADF ?</Title>
        <Text size="sm" c="dimmed" mt={4} mb="md">
          Faites ce réglage une fois par couple imprimante–scanner. MathPrint imposera ensuite
          la taille réelle, la collation et l’ordre de sortie à chaque impression.
        </Text>
        <Stack gap="sm">
          <Group align="flex-start" wrap="nowrap">
            <Badge circle size="lg">1</Badge>
            <Box>
              <Text size="sm" fw={600}>Imprimez les deux feuilles de test</Text>
              <Text size="sm">Cliquez sur « Test 1–2 ». Les feuilles portent HAUT, BAS et leur grand numéro.</Text>
            </Box>
          </Group>
          <Group align="flex-start" wrap="nowrap">
            <Badge circle size="lg">2</Badge>
            <Box>
              <Text size="sm" fw={600}>Observez la pile sans la retourner</Text>
              <Text size="sm">
                Notez d’abord si l’imprimante commence par la page 1 ou la page 2,
                puis observez séparément comment elle les empile sur le bac.
              </Text>
            </Box>
          </Group>
          <Group align="flex-start" wrap="nowrap">
            <Badge circle size="lg">3</Badge>
            <Box>
              <Text size="sm" fw={600}>Testez le chargeur ADF avec la pile 1, 2, 3</Text>
              <Text size="sm">Après le scan, si la pile physique ressort 3, 2, 1, cochez « ADF inverse la pile ».</Text>
            </Box>
          </Group>
          <Group align="flex-start" wrap="nowrap">
            <Badge circle size="lg">4</Badge>
            <Box>
              <Text size="sm" fw={600}>Contrôlez le résultat avec « Aperçu »</Text>
              <Text size="sm">Les boutons « Inverser » de la modale mettent immédiatement à jour le tableau.</Text>
            </Box>
          </Group>
        </Stack>
        <Alert mt="md" color="blue" variant="light" icon={<Info size={16} />}>
          Les fichiers PDF/JPEG de l’ADF sont traités par compteur ou horodatage naturel
          (scan-2 avant scan-10), jamais selon la liste des élèves. Les QR identifient les copies,
          mais ne changent pas leur ordre. Les corrections suivent donc exactement la séquence scannée.
        </Alert>
      </Card>

      <Modal opened={Boolean(preview)} onClose={() => setPreviewName(null)} size="lg"
        title={`Chemin des feuilles${preview ? ` — ${preview.name}` : ''}`}>
        {preview && (
          <Stack>
            <Text size="sm" c="dimmed">
              Suivez la pile de haut en bas. Elle commence toujours dans l’ordre 1 → 2 → 3 ;
              chaque équipement peut ensuite la conserver ou l’inverser.
            </Text>
            <Group gap="md">
              <Checkbox label="Recto-verso automatique" checked={preview.duplex}
                onChange={(e) => setPreference(preview, 'duplex', e.currentTarget.checked)} />
            </Group>
            <Timeline active={preview.duplex ? 3 : 4} bulletSize={34} lineWidth={3}>
              <Timeline.Item bullet={<ClipboardList size={17} />} title="Relevé des copies">
                <Text size="xs" c="dimmed" mb="xs">Point de départ connu</Text>
                <PilePicture reversed={false} caption="Copies relevées : 1, puis 2, puis 3" />
              </Timeline.Item>
              <Timeline.Item bullet={<ScanLine size={17} />} title="Passage dans l’ADF">
                <Text size="xs" c="dimmed" mb="xs">
                  {preview.adf_reverse_order ? 'L’ADF inverse physiquement la pile.' : 'L’ADF conserve la pile.'}
                </Text>
                <PilePicture reversed={adfReversed} caption="Pile physique à la sortie de l’ADF"
                  inverting={saving === `${preview.name}:adf_reverse_order`}
                  onInvert={() => setPreference(preview, 'adf_reverse_order', !preview.adf_reverse_order)} />
              </Timeline.Item>
              <Timeline.Item bullet={<Printer size={17} />} title="Imprimante — prélèvement du lot">
                <Text size="xs" c="dimmed" mb="xs">
                  Ce réglage détermine quelle copie du fichier l’imprimante traite en premier.
                </Text>
                <PilePicture reversed={pickupReversed}
                  caption={`Le premier sujet à être imprimé est le n°${pickupReversed ? 3 : 1}.`}
                  inverting={saving === `${preview.name}:pickup_reverse_order`}
                  onInvert={() => setPreference(preview, 'pickup_reverse_order', !preview.pickup_reverse_order)} />
              </Timeline.Item>
              <Timeline.Item bullet={<Printer size={17} />}
                title={preview.duplex ? 'Imprimante — pose sur le bac de réception'
                  : '1er passage Recto — pose sur le bac de réception'}>
                <Text size="xs" c="dimmed" mb="xs">
                  Cette deuxième propriété est indépendante du prélèvement : le bac peut inverser une nouvelle fois la pile.
                </Text>
                <PilePicture reversed={printerPileReversed}
                  caption={`Pile sur le bac : ${printerPileReversed ? '3, 2, 1' : '1, 2, 3'}.`}
                  inverting={saving === `${preview.name}:output_reverse_order`}
                  onInvert={() => setPreference(preview, 'output_reverse_order', !preview.output_reverse_order)} />
              </Timeline.Item>
              {!preview.duplex && (
                <Timeline.Item bullet={<Printer size={17} />} title="Imprimante — 2e passage Verso">
                  <Text size="xs" c="dimmed" mb="xs">
                    Replacez la pile dans le bac pour imprimer les versos.
                  </Text>
                  <PilePicture reversed={printerPileReversed} caption="Pile après les versos" muted />
                </Timeline.Item>
              )}
            </Timeline>
            {preview.pickup_reverse_order && preview.output_reverse_order && (
              <Alert color="orange" icon={<Info size={16} />}>
                Double inversion de l’imprimante : elle commence par le sujet n°3,
                puis le bac inverse encore la pile. Le résultat redevient donc 1 → 2 → 3.
              </Alert>
            )}
            <Alert color="blue" icon={<Info size={16} />}>
              {preview.duplex
                ? 'Le recto-verso est réalisé automatiquement en un seul passage.'
                : 'Le deuxième passage Verso n’est proposé que pour une imprimante sans recto-verso automatique.'}
            </Alert>
          </Stack>
        )}
      </Modal>
    </Stack>
  )
}
