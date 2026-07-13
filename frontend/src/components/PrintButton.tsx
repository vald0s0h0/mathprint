// Impression d'un document via les imprimantes CUPS locales ou IPP réseau (§11.5).
import { Button, Checkbox, Group, NumberInput, Popover, Select, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Printer } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

type Printers = {
  local: { name: string; default?: boolean }[]
  network: { name: string }[]
  printing_available: boolean
}

export default function PrintButton({
  assessmentId, file, label = 'Imprimer', size = 'xs',
}: { assessmentId: string; file: string; label?: string; size?: string }) {
  const [printers, setPrinters] = useState<Printers | null>(null)
  const [opened, setOpened] = useState(false)
  const [printer, setPrinter] = useState<string | null>(null)
  const [copies, setCopies] = useState(1)
  const [duplex, setDuplex] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (opened && !printers) {
      api.get<Printers>('/api/printers').then((p) => {
        setPrinters(p)
        const def = p.local.find((x) => x.default) ?? p.local[0] ?? p.network[0]
        if (def) setPrinter(def.name)
      })
    }
  }, [opened, printers])

  async function doPrint() {
    if (!printer) return
    setBusy(true)
    try {
      const r = await api.post<{ lp_output: string }>('/api/printers/print', {
        assessment_id: assessmentId, file, printer, copies, duplex,
      })
      notifications.show({ color: 'green', message: `Envoyé : ${r.lp_output || printer}` })
      setOpened(false)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const options = printers
    ? [...printers.local.map((p) => ({ value: p.name, label: `${p.name}${p.default ? ' (défaut)' : ''}` })),
       ...printers.network.map((p) => ({ value: p.name, label: `${p.name} (réseau)` }))]
    : []

  return (
    <Popover opened={opened} onChange={setOpened} width={300} position="bottom-end" withArrow>
      <Popover.Target>
        <Button size={size as never} variant="light" leftSection={<Printer size={14} />}
          onClick={() => setOpened((o) => !o)}>
          {label}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          {printers && !printers.printing_available && (
            <Text size="xs" c="orange">
              Aucune imprimante détectée — télécharger le PDF et imprimer à 100 %.
            </Text>
          )}
          <Select size="xs" label="Imprimante" data={options} value={printer} onChange={setPrinter} />
          <Group grow>
            <NumberInput size="xs" label="Copies" min={1} max={50} value={copies}
              onChange={(v) => setCopies(Number(v) || 1)} />
            <Checkbox mt={22} size="xs" label="Recto/verso" checked={duplex}
              onChange={(e) => setDuplex(e.target.checked)} />
          </Group>
          <Text size="xs" c="dimmed">Taille réelle 100 % imposée (print-scaling=none).</Text>
          <Button size="xs" onClick={doPrint} loading={busy} disabled={!printer}>
            Lancer l'impression
          </Button>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}
