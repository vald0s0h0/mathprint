// Impression d'un document via les imprimantes CUPS locales ou IPP réseau (§11.5).
import { Alert, Badge, Button, Popover, Select, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Printer } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

type Printers = {
  local: PrinterDestination[]
  network: PrinterDestination[]
  printing_available: boolean
  online_connector_count: number
}

type PrinterDestination = {
  name: string
  display_name?: string
  device_name?: string
  connector_id?: string
  source: 'cups_local' | 'connector_local' | 'network_ipp'
  status: string
  available: boolean
  system_default?: boolean
  app_default: boolean
  duplex: boolean
}

type PrintResult = { lp_output: string; queued?: boolean; job_id?: string }
const preferredDestinationKey = 'mathprint:preferred-print-destination'

function chooseDestination(printers: Printers) {
  const all = [...printers.local, ...printers.network]
  const available = all.filter((destination) => destination.available)
  const remembered = window.localStorage.getItem(preferredDestinationKey)
  if (remembered) {
    // Une destination hors ligne reste le choix du professeur : ne jamais
    // basculer silencieusement le travail vers l'autre ordinateur.
    return all.find((destination) => destination.name === remembered) ?? null
  }

  const explicitDefault = available.find((destination) => destination.app_default)
  if (explicitDefault) return explicitDefault

  const connectorDestinations = available.filter(
    (destination) => destination.source === 'connector_local' && destination.connector_id,
  )
  const onlineConnectors = new Set(connectorDestinations.map(
    (destination) => destination.connector_id,
  ))
  if (onlineConnectors.size === 1) {
    return connectorDestinations.find((destination) => destination.system_default)
      ?? connectorDestinations[0]
  }
  if (onlineConnectors.size > 1) return null

  return available.find((destination) => destination.system_default)
    ?? (available.length === 1 ? available[0] : null)
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
    if (!opened) return
    let active = true
    async function loadPrinters() {
      const next = await api.get<Printers>('/api/printers')
      if (!active) return
      setPrinters(next)
      setPrinter((current) => {
        // Après une sélection, conserver son identifiant exact même si le
        // poste disparaît. L'utilisateur décidera lui-même du remplacement.
        if (current) return current
        return chooseDestination(next)?.name ?? null
      })
    }
    loadPrinters().catch((error) => {
      if (active) notifications.show({ color: 'red', message: (error as Error).message })
    })
    const timer = window.setInterval(() => {
      loadPrinters().catch(() => { /* la prochaine actualisation réessaiera */ })
    }, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [opened])

  useEffect(() => {
    setRectoDone(Boolean(printer
      && window.sessionStorage.getItem(rectoSessionKey(printer)) === 'submitted'))
  }, [printer, assessmentId])

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
    if (!printer || !selectedPrinter?.available) return
    setBusySide(passSide)
    try {
      window.localStorage.setItem(preferredDestinationKey, printer)
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
    ? [...printers.local.map((p) => ({
         value: p.name,
         disabled: !p.available,
         label: p.source === 'connector_local'
           ? `${p.device_name || 'Poste'} — ${p.display_name || p.name}${p.system_default ? ' · défaut du poste' : ''}${!p.available ? ' · hors ligne' : ''}`
           : `${p.display_name || p.name}${p.system_default ? ' · défaut système' : ''}`,
       })),
       ...printers.network.map((p) => ({
         value: p.name,
         disabled: !p.available,
         label: `${p.display_name || p.name} — réseau${p.app_default ? ' · préférée' : ''}`,
       }))]
    : []

  function selectPrinter(name: string | null) {
    setPrinter(name)
    if (name) window.localStorage.setItem(preferredDestinationKey, name)
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
          {printers && printers.online_connector_count > 1 && !printer && (
            <Alert color="blue" p="xs">
              Plusieurs ordinateurs sont connectés. Choisissez une destination ; ce choix sera mémorisé sur ce navigateur.
            </Alert>
          )}
          {printers && printer && !selectedPrinter?.available && (
            <Alert color="orange" p="xs">
              La destination choisie est hors ligne ou n’est plus déclarée. Aucun travail ne sera envoyé ailleurs automatiquement.
            </Alert>
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
                onClick={() => doPrint('recto')} loading={busySide === 'recto'} disabled={!printer || !selectedPrinter?.available || !!pendingJob}>
                Imprimer la 1re passe — Recto
              </Button>
              <Text size="xs" c="dimmed" ta="center">
                Replacez la pile imprimée dans le bac, sans en modifier l’ordre.
              </Text>
              <Button size="xs" color="violet" onClick={() => doPrint('verso')}
                loading={busySide === 'verso'} disabled={!printer || !selectedPrinter?.available || !!pendingJob || !rectoDone}>
                Imprimer la 2e passe — Verso
              </Button>
            </Stack>
          ) : (
            <Button size="xs" onClick={() => doPrint('all')} loading={busySide === 'all'} disabled={!printer || !selectedPrinter?.available || !!pendingJob}>
              Lancer l'impression
            </Button>
          )}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  )
}
