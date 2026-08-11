// Impression d'un document via les imprimantes CUPS locales ou IPP réseau (§11.5).
import { Alert, Badge, Button, Popover, Select, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Printer } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

type Printers = {
  local: { name: string; app_default: boolean; duplex: boolean }[]
  network: { name: string; app_default: boolean; duplex: boolean }[]
  printing_available: boolean
}

export default function PrintButton({
  assessmentId, file, label = 'Imprimer', size = 'xs', assessmentDuplex = false,
}: {
  assessmentId: string; file: string; label?: string; size?: string
  assessmentDuplex?: boolean
}) {
  const [printers, setPrinters] = useState<Printers | null>(null)
  const [opened, setOpened] = useState(false)
  const [printer, setPrinter] = useState<string | null>(null)
  const [busySide, setBusySide] = useState<'all' | 'recto' | 'verso' | null>(null)
  const [rectoDone, setRectoDone] = useState(false)

  useEffect(() => {
    if (opened && !printers) {
      api.get<Printers>('/api/printers').then((p) => {
        setPrinters(p)
        const all = [...p.local, ...p.network]
        const def = all.find((x) => x.app_default) ?? all[0]
        if (def) {
          setPrinter(def.name)
        }
      })
    }
  }, [opened, printers])

  async function doPrint(passSide: 'all' | 'recto' | 'verso' = 'all') {
    if (!printer) return
    setBusySide(passSide)
    try {
      const r = await api.post<{ lp_output: string }>('/api/printers/print', {
        assessment_id: assessmentId, file, printer,
        duplex: file === 'subject_batch.pdf' && assessmentDuplex && selectedPrinter?.duplex,
        pass_side: passSide,
      })
      const passLabel = passSide === 'recto' ? 'Rectos envoyés' : passSide === 'verso' ? 'Versos envoyés' : 'Envoyé'
      notifications.show({ color: 'green', message: `${passLabel} : ${r.lp_output || printer}` })
      if (passSide === 'recto') setRectoDone(true)
      else {
        setOpened(false)
        setRectoDone(false)
      }
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusySide(null)
    }
  }

  const options = printers
    ? [...printers.local.map((p) => ({ value: p.name, label: `${p.name}${p.app_default ? ' (défaut MathPrint)' : ''}` })),
       ...printers.network.map((p) => ({ value: p.name,
         label: `${p.name} (réseau)${p.app_default ? ' · défaut MathPrint' : ''}` }))]
    : []

  function selectPrinter(name: string | null) {
    setPrinter(name)
    setRectoDone(false)
  }

  const selectedPrinter = printers
    ? [...printers.local, ...printers.network].find((p) => p.name === printer)
    : undefined
  const manualDuplex = file === 'subject_batch.pdf' && assessmentDuplex
    && selectedPrinter != null && !selectedPrinter.duplex

  return (
    <Popover opened={opened} onChange={setOpened} width={360} position="bottom-end" withArrow>
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
          <Select size="xs" label="Imprimante" data={options} value={printer}
            onChange={selectPrinter} comboboxProps={{ withinPortal: false }} />
          {file === 'subject_batch.pdf' && assessmentDuplex && selectedPrinter?.duplex && (
            <Badge variant="light" color="green">Recto-verso automatique</Badge>
          )}
          <Text size="xs" c="dimmed">
            Taille réelle, collation et ordre physique imposés selon le profil de l’imprimante.
          </Text>
          {manualDuplex ? (
            <Stack gap={6}>
              <Alert color="orange" p="xs">
                Cette imprimante n’a pas de recto-verso automatique. Lancez les deux passes séparément.
              </Alert>
              <Button size="xs" variant={rectoDone ? 'light' : 'filled'}
                onClick={() => doPrint('recto')} loading={busySide === 'recto'} disabled={!printer}>
                Imprimer la 1re passe — Recto
              </Button>
              <Text size="xs" c="dimmed" ta="center">
                Replacez la pile imprimée dans le bac, sans en modifier l’ordre.
              </Text>
              <Button size="xs" color="violet" onClick={() => doPrint('verso')}
                loading={busySide === 'verso'} disabled={!printer}>
                Imprimer la 2e passe — Verso
              </Button>
            </Stack>
          ) : (
            <Button size="xs" onClick={() => doPrint('all')} loading={busySide === 'all'} disabled={!printer}>
              Lancer l'impression
            </Button>
          )}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}
