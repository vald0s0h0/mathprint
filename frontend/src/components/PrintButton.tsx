// Impression d'un document via les imprimantes CUPS locales ou IPP réseau (§11.5).
import { Alert, Badge, Button, Popover, Select, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Printer } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

type Printers = {
  local: { name: string; display_name?: string; device_name?: string; app_default: boolean; duplex: boolean }[]
  network: { name: string; display_name?: string; app_default: boolean; duplex: boolean }[]
  printing_available: boolean
}

type PrintResult = { lp_output: string; queued?: boolean; job_id?: string }

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
  const [pendingJob, setPendingJob] = useState<{
    id: string; side: 'all' | 'recto' | 'verso'; printer: string
  } | null>(null)

  function rectoSessionKey(printerName: string) {
    return `mathprint:recto:${assessmentId}:${printerName}`
  }

  function rememberRecto(printerName: string) {
    window.sessionStorage.setItem(rectoSessionKey(printerName), 'submitted')
    setRectoDone(true)
  }

  function clearRecto(printerName: string) {
    window.sessionStorage.removeItem(rectoSessionKey(printerName))
    setRectoDone(false)
  }

  useEffect(() => {
    if (opened && !printers) {
      api.get<Printers>('/api/printers').then((p) => {
        setPrinters(p)
        const all = [...p.local, ...p.network]
        const def = all.find((x) => x.app_default) ?? all[0]
        if (def) {
          setPrinter(def.name)
          setRectoDone(window.sessionStorage.getItem(rectoSessionKey(def.name)) === 'submitted')
        }
      })
    }
  }, [opened, printers])

  useEffect(() => {
    if (!pendingJob) return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const job = await api.get<{ status: string; error?: string }>(
          `/api/printers/jobs/${encodeURIComponent(pendingJob.id)}`)
        if (cancelled || ['queued', 'claimed'].includes(job.status)) return
        window.clearInterval(timer)
        setPendingJob(null)
        if (job.status !== 'submitted') {
          notifications.show({ color: 'red', message: job.error ||
            `Impression ${job.status === 'uncertain' ? 'à vérifier dans la file système' : 'échouée'}` })
          return
        }
        const label = pendingJob.side === 'recto' ? 'Rectos envoyés à la file système'
          : pendingJob.side === 'verso' ? 'Versos envoyés à la file système'
            : 'Document envoyé à la file système'
        notifications.show({ color: 'green', message: label })
        if (pendingJob.side === 'recto') rememberRecto(pendingJob.printer)
        else {
          clearRecto(pendingJob.printer)
          setOpened(false)
        }
      } catch (e) {
        window.clearInterval(timer)
        setPendingJob(null)
        notifications.show({ color: 'red', message: (e as Error).message })
      }
    }, 1500)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [pendingJob])

  async function doPrint(passSide: 'all' | 'recto' | 'verso' = 'all') {
    if (!printer) return
    setBusySide(passSide)
    try {
      const r = await api.post<PrintResult>('/api/printers/print', {
        assessment_id: assessmentId, file, printer,
        duplex: file === 'subject_batch.pdf' && assessmentDuplex && selectedPrinter?.duplex,
        pass_side: passSide,
      })
      if (r.queued && r.job_id) {
        setPendingJob({ id: r.job_id, side: passSide, printer })
        notifications.show({ color: 'blue', message: r.lp_output })
        return
      }
      const passLabel = passSide === 'recto' ? 'Rectos envoyés' : passSide === 'verso' ? 'Versos envoyés' : 'Envoyé'
      notifications.show({ color: 'green', message: `${passLabel} : ${r.lp_output || printer}` })
      if (passSide === 'recto') rememberRecto(printer)
      else {
        clearRecto(printer)
        setOpened(false)
      }
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusySide(null)
    }
  }

  const options = printers
    ? [...printers.local.map((p) => ({ value: p.name,
         label: `${p.display_name || p.name}${p.device_name ? ` — ${p.device_name}` : ''}${p.app_default ? ' (défaut MathPrint)' : ''}` })),
       ...printers.network.map((p) => ({ value: p.name,
         label: `${p.display_name || p.name} (réseau)${p.app_default ? ' · défaut MathPrint' : ''}` }))]
    : []

  function selectPrinter(name: string | null) {
    setPrinter(name)
    setRectoDone(Boolean(name && window.sessionStorage.getItem(rectoSessionKey(name)) === 'submitted'))
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
            onChange={selectPrinter} disabled={!!pendingJob} comboboxProps={{ withinPortal: false }} />
          {file === 'subject_batch.pdf' && assessmentDuplex && selectedPrinter?.duplex && (
            <Badge variant="light" color="green">Recto-verso automatique</Badge>
          )}
          <Text size="xs" c="dimmed">
            Taille réelle, collation et ordre physique imposés selon le profil de l’imprimante.
          </Text>
          {pendingJob && (
            <Alert color="blue" p="xs">
              En attente du connecteur local — ne relancez pas ce travail.
            </Alert>
          )}
          {manualDuplex ? (
            <Stack gap={6}>
              <Alert color="orange" p="xs">
                Cette imprimante n’a pas de recto-verso automatique. Lancez les deux passes séparément.
              </Alert>
              <Button size="xs" variant={rectoDone ? 'light' : 'filled'}
                onClick={() => doPrint('recto')} loading={busySide === 'recto'} disabled={!printer || !!pendingJob}>
                Imprimer la 1re passe — Recto
              </Button>
              <Text size="xs" c="dimmed" ta="center">
                Replacez la pile imprimée dans le bac, sans en modifier l’ordre.
              </Text>
              <Button size="xs" color="violet" onClick={() => doPrint('verso')}
                loading={busySide === 'verso'} disabled={!printer || !!pendingJob || !rectoDone}>
                Imprimer la 2e passe — Verso
              </Button>
            </Stack>
          ) : (
            <Button size="xs" onClick={() => doPrint('all')} loading={busySide === 'all'} disabled={!printer || !!pendingJob}>
              Lancer l'impression
            </Button>
          )}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}
