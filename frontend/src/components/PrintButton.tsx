// Impression d'un document via les imprimantes CUPS locales ou IPP réseau (§11.5).
import {
  Alert, Badge, Box, Button, Group, Popover, type PopoverProps, Select, Stack, Text, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Printer } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import OverlayOffsetModal, { overlayOffsetLabel } from './OverlayOffsetModal'
import OverlayQueuePanel, {
  defaultStartPosition, type OverlayQueue, ResumeWarning,
} from './OverlayQueuePanel'

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
  overlay_offset_x_mm: number
  overlay_offset_y_mm: number
}

type PrintResult = { lp_output: string; queued?: boolean; job_id?: string }
const preferredDestinationKey = 'mathprint:preferred-print-destination'

// La file peut dépasser l'espace libre autour du bouton : le panneau défile alors
// en restant dans l'écran, sans jamais masquer le choix de l'imprimante.
const overlayPopoverMiddlewares: PopoverProps['middlewares'] = {
  flip: true, shift: true, inline: false,
  size: {
    padding: 8,
    apply({ availableHeight, elements }) {
      elements.floating.style.maxHeight = `${Math.max(240, availableHeight)}px`
      elements.floating.style.overflowY = 'auto'
    },
  },
}

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
  assessmentId, file, label = 'Imprimer', size = 'xs', assessmentDuplex = false, wave = 0,
  open, onOpenChange, hiddenTrigger = false,
}: {
  assessmentId: string; file: string; label?: string; size?: string
  assessmentDuplex?: boolean
  // Enchaînement scanné à imprimer (0 = premier dépôt, sinon copies tardives).
  wave?: number
  // pilotage depuis l'extérieur (ex. « Réimprimer » dans un menu ⋮) : le bouton est alors
  // masqué et seule la fenêtre d'impression s'ouvre
  open?: boolean; onOpenChange?: (open: boolean) => void; hiddenTrigger?: boolean
}) {
  const isOverlay = file === 'correction_overlay.pdf'
  const [printers, setPrinters] = useState<Printers | null>(null)
  const [opened, setOpened] = useState(false)
  useEffect(() => { if (open !== undefined) setOpened(open) }, [open])
  useEffect(() => { onOpenChange?.(opened) }, [opened]) // eslint-disable-line react-hooks/exhaustive-deps
  const [hovered, setHovered] = useState(false)
  const [queue, setQueue] = useState<OverlayQueue | null>(null)
  const [queueTick, setQueueTick] = useState(0)
  // Élève désigné par le professeur ; sinon, la position proposée d'office.
  const [chosenStart, setChosenStart] = useState<number | null>(null)
  const [offsetOpen, setOffsetOpen] = useState(false)
  const [printersTick, setPrintersTick] = useState(0)
  const [printer, setPrinter] = useState<string | null>(null)
  const [busySide, setBusySide] = useState<'all' | 'recto' | 'verso' | null>(null)
  const [rectoDone, setRectoDone] = useState(false)
  const [pendingJob, setPendingJob] = useState<{
    id: string; side: 'all' | 'recto' | 'verso'; printer: string; sample?: boolean
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

  // Le survol de l'overlay annonce la première copie : il lui faut l'imprimante.
  const printersWanted = opened || (isOverlay && hovered)
  useEffect(() => {
    if (!printersWanted) return
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
  }, [printersWanted, printersTick])

  useEffect(() => {
    if (!isOverlay || !printersWanted || !printer) return
    let active = true
    const query = new URLSearchParams({ assessment_id: assessmentId, printer, wave: String(wave) })
    api.get<OverlayQueue>(`/api/printers/overlay-queue?${query}`).then((next) => {
      if (!active) return
      setQueue(next)
      // Un envoi encore en cours reste suivi, même après un rechargement de page.
      const last = next.last_print
      if (last?.job_id && ['queued', 'claimed'].includes(last.status)) {
        setPendingJob((current) => current ?? { id: last.job_id!, side: last.pass_side, printer })
      }
    }).catch((error) => {
      if (active && opened) notifications.show({ color: 'red', message: (error as Error).message })
    })
    return () => { active = false }
  }, [isOverlay, printersWanted, printer, assessmentId, queueTick, wave])

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
        setQueueTick((tick) => tick + 1)
        if (job.status !== 'submitted') {
          notifications.show({ color: 'red', message: job.error ||
            `Impression ${job.status === 'uncertain' ? 'à vérifier dans la file système' : 'échouée'}` })
          return
        }
        if (pendingJob.sample) {
          notifications.show({ color: 'green', message: 'Essai envoyé à la file système' })
          return
        }
        const label = pendingJob.side === 'recto' ? 'Rectos envoyés à la file système'
          : pendingJob.side === 'verso' ? 'Versos envoyés à la file système'
            : 'Document envoyé à la file système'
        notifications.show({ color: 'green', message: label })
        if (pendingJob.side === 'recto') rememberRecto(pendingJob.printer)
        else {
          clearRecto(pendingJob.printer)
          // L'overlay reste affiché : c'est là que se décide une reprise.
          if (!isOverlay) setOpened(false)
        }
      } catch (e) {
        window.clearInterval(timer)
        setPendingJob(null)
        notifications.show({ color: 'red', message: (e as Error).message })
      }
    }, 1500)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [pendingJob])

  async function printSample() {
    if (!printer || !selectedPrinter?.available) throw new Error('Imprimante indisponible')
    const entry = nextEntry ?? (queue?.entries[0])
    if (!entry) return
    const r = await api.post<PrintResult>('/api/printers/print', {
      assessment_id: assessmentId, file, printer, sample: true, wave,
      start_position: entry.position, expected_student: entry.student,
    })
    if (r.queued && r.job_id) setPendingJob({ id: r.job_id, side: 'all', printer, sample: true })
    notifications.show({ color: 'blue', message: `Essai : correction de ${entry.student}` })
  }

  async function doPrint(passSide: 'all' | 'recto' | 'verso' = 'all') {
    if (!printer || !selectedPrinter?.available) return
    setBusySide(passSide)
    try {
      window.localStorage.setItem(preferredDestinationKey, printer)
      const r = await api.post<PrintResult>('/api/printers/print', {
        assessment_id: assessmentId, file, printer,
        duplex: file === 'subject_batch.pdf' && assessmentDuplex && selectedPrinter?.duplex,
        pass_side: passSide,
        ...(isOverlay && nextEntry ? {
          start_position: nextEntry.position, expected_student: nextEntry.student, wave,
        } : {}),
      })
      if (isOverlay) {
        setChosenStart(null)
        setQueueTick((tick) => tick + 1)
      }
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
        if (!isOverlay) setOpened(false)
      }
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
      if (isOverlay) setQueueTick((tick) => tick + 1)
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
  const twoSided = isOverlay ? Boolean(queue?.assessment_duplex) : file === 'subject_batch.pdf' && assessmentDuplex
  const manualDuplex = twoSided && selectedPrinter != null && !selectedPrinter.duplex

  const queueUsable = isOverlay && queue != null && !queue.blocked && queue.entries.length > 0
  const start = queueUsable ? chosenStart ?? defaultStartPosition(queue, manualDuplex) : null
  const nextEntry = queueUsable && start ? queue.entries[start - 1] : undefined
  const lastPass = queue?.last_print
  // Une reprise de verso reste possible depuis un autre navigateur.
  const versoAllowed = rectoDone || Boolean(lastPass && lastPass.pass_side !== 'all'
    && ['submitted', 'uncertain'].includes(lastPass.status))
  const printDisabled = !printer || !selectedPrinter?.available || !!pendingJob
    || (isOverlay && !nextEntry)
  const offsetText = selectedPrinter
    ? overlayOffsetLabel(selectedPrinter.overlay_offset_x_mm, selectedPrinter.overlay_offset_y_mm) : ''
  const remaining = nextEntry && queue ? queue.total - nextEntry.position + 1 : 0
  // Enchaîner le verso après un recto abouti n'est pas une reprise.
  const resuming = Boolean(nextEntry) && (nextEntry!.position > 1
    || Boolean(lastPass && !(manualDuplex && lastPass.pass_side === 'recto')))

  function hoverLabel() {
    if (!printers) return 'Recherche de l’imprimante…'
    if (!printer) return 'Cliquez pour choisir une imprimante : l’ordre des copies en dépend.'
    if (!queue) return 'Lecture de la file d’attente…'
    if (queue.blocked) return queue.blocked
    if (nextEntry) {
      return nextEntry.position === 1
        ? `Première overlay imprimée : ${nextEntry.student}`
        : `Prochaine overlay imprimée : ${nextEntry.student} (feuille ${nextEntry.position} sur ${queue.total})`
    }
    return 'Déjà envoyée à l’imprimante. Cliquez pour choisir à partir de quel élève reprendre.'
  }

  return (
    <>
    <Popover opened={opened} onChange={setOpened} position="bottom-end" withArrow={!isOverlay}
      closeOnClickOutside={!offsetOpen} closeOnEscape={!offsetOpen}
      middlewares={isOverlay ? overlayPopoverMiddlewares : undefined}
      width={isOverlay ? 'min(420px, calc(100vw - 32px))' : 360}>
      <Popover.Target>
        <Tooltip multiline w={280} withArrow disabled={!isOverlay || opened} label={hoverLabel()}>
          <Button size={size as never} variant="light" leftSection={<Printer size={14} />}
            style={hiddenTrigger ? { position: 'absolute', width: 0, height: 0, padding: 0, border: 0, opacity: 0, overflow: 'hidden', pointerEvents: 'none' } : undefined}
            tabIndex={hiddenTrigger ? -1 : undefined} aria-hidden={hiddenTrigger || undefined}
            onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
            onClick={() => setOpened((o) => !o)}>
            {label}
          </Button>
        </Tooltip>
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
          {twoSided && selectedPrinter?.duplex && (
            <Badge variant="light" color="green">Recto-verso automatique</Badge>
          )}
          {isOverlay && selectedPrinter && (
            <Group justify="space-between" gap="xs" wrap="nowrap">
              <Text size="xs">
                Position de l’overlay :{' '}
                <b>{offsetText || 'normale'}</b>
              </Text>
              <Button size="compact-xs" variant="subtle" onClick={() => setOffsetOpen(true)}>
                Régler
              </Button>
            </Group>
          )}
          {isOverlay && queue?.blocked && (
            <Alert color="orange" p="xs">{queue.blocked}</Alert>
          )}
          {queueUsable && (
            <OverlayQueuePanel queue={queue} start={start} onSelect={setChosenStart}
              disabled={!!pendingJob} verso={manualDuplex && versoAllowed} />
          )}
          {!isOverlay && (
            <Text size="xs" c="dimmed">
              Taille réelle, collation et ordre physique imposés selon le profil de l’imprimante.
            </Text>
          )}
          {pendingJob && (
            <Alert color="blue" p="xs">
              En attente du connecteur local — ne relancez pas ce travail.
            </Alert>
          )}
          {manualDuplex && (
            <Alert color="orange" p="xs">
              Cette imprimante n’a pas de recto-verso automatique. Lancez les deux passes séparément.
            </Alert>
          )}
          {/* L'action reste visible quand la file fait défiler le panneau. */}
          <Box style={isOverlay ? {
            position: 'sticky', bottom: 0, zIndex: 1, paddingBlock: 6,
            background: 'var(--mantine-color-body)',
          } : undefined}>
          {resuming && <Box mb={6}><ResumeWarning /></Box>}
          {manualDuplex ? (
            <Stack gap={6}>
              <Button size="xs" variant={rectoDone ? 'light' : 'filled'}
                onClick={() => doPrint('recto')} loading={busySide === 'recto'} disabled={printDisabled}>
                Imprimer la 1re passe — Recto
              </Button>
              {!isOverlay && (
                <Text size="xs" c="dimmed" ta="center">
                  Replacez la pile imprimée dans le bac, sans en modifier l’ordre.
                </Text>
              )}
              <Button size="xs" color="violet" onClick={() => doPrint('verso')}
                loading={busySide === 'verso'} disabled={printDisabled || !(isOverlay ? versoAllowed : rectoDone)}>
                Imprimer la 2e passe — Verso
              </Button>
            </Stack>
          ) : (
            <Button fullWidth size="xs" h="auto" py={6} onClick={() => doPrint('all')} loading={busySide === 'all'} disabled={printDisabled}
              styles={{ label: { whiteSpace: 'normal', textAlign: 'center' } }}>
              {!isOverlay ? 'Lancer l\'impression'
                : nextEntry ? `Imprimer ${remaining} feuille${remaining > 1 ? 's' : ''} à partir de ${nextEntry.student}`
                  : 'Choisissez d’abord un élève dans la file'}
            </Button>
          )}
          </Box>
        </Stack>
      </Popover.Dropdown>
    </Popover>
    {isOverlay && selectedPrinter && (
      <OverlayOffsetModal opened={offsetOpen} onClose={() => setOffsetOpen(false)}
        printerName={selectedPrinter.name}
        printerLabel={selectedPrinter.display_name || selectedPrinter.name}
        offsetX={selectedPrinter.overlay_offset_x_mm} offsetY={selectedPrinter.overlay_offset_y_mm}
        onSaved={() => setPrintersTick((tick) => tick + 1)}
        onPrintSample={queueUsable && selectedPrinter.available && !pendingJob ? printSample : undefined}
        sampleStudent={(nextEntry ?? queue?.entries[0])?.student} />
    )}
    </>
  )
}
