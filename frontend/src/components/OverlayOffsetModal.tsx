// Réglage de la position de l'overlay, pour une imprimante donnée.
// Le professeur reproduit à l'écran ce qu'il voit sur sa feuille : un écart de
// 5 mm vers la droite se lit directement, sans avoir à raisonner sur le sens de
// la compensation. L'application en déduit le déplacement inverse de l'overlay.
import { ActionIcon, Alert, Box, Button, Group, Modal, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

const STEP_MM = 0.5
const LIMIT_MM = 20
// Cadre de l'aperçu : assez serré pour voir un écart de 0,5 mm, avec une marge
// autour de la copie pour que la correction reste visible quand elle s'en écarte.
const VIEW_BOX = '-12 -8 84 54'
const INK = '#C62828'

function mm(value: number) {
  return String(Math.abs(value)).replace('.', ',')
}

function direction(x: number, y: number, words: { left: string; right: string; up: string; down: string }) {
  const parts = []
  if (x) parts.push(`${mm(x)} mm ${x < 0 ? words.left : words.right}`)
  if (y) parts.push(`${mm(y)} mm ${y > 0 ? words.up : words.down}`)
  return parts.join(' et ')
}

/** Déplacement enregistré, dans la langue du professeur. */
export function overlayOffsetLabel(x: number, y: number) {
  return direction(x, y, {
    left: 'plus à gauche', right: 'plus à droite', up: 'plus haut', down: 'plus bas',
  })
}

function PaperPreview({ x, y }: { x: number; y: number }) {
  const copy = '#8A94A0'
  return (
    <svg viewBox={VIEW_BOX} width="100%" role="img"
      aria-label="Aperçu : la copie en gris reste fixe, la correction en rouge se déplace"
      style={{ display: 'block', background: '#fff', borderRadius: 4, border: '1px solid #D5DAE0' }}>
      <g fill="none" stroke={copy} strokeWidth={0.35}>
        <rect x={5} y={4} width={20} height={12} rx={0.6} />
        <rect x={5} y={20} width={54} height={14} rx={0.8} />
        <line x1={9} y1={30} x2={46} y2={30} strokeDasharray="0.8 0.8" />
      </g>
      <text x={7} y={7} fontSize={2} fill={copy} fontWeight={700}>NOTE</text>
      <text x={9} y={25} fontSize={2.4} fill={copy}>Exercice 1 : calcule 12 × 3</text>
      {/* L'axe vertical de l'écran est inversé par rapport à celui de la page. */}
      <g transform={`translate(${x} ${-y})`} style={{ transition: 'transform 120ms' }}>
        <text x={15} y={13} fontSize={4.6} fill={INK} fontWeight={700} textAnchor="middle">14/20</text>
        <path d="M57.6 29.2 l1 1.1 l2 -2.6" fill="none" stroke={INK} strokeWidth={0.45} strokeLinecap="round" />
        <text x={59} y={37} fontSize={2.8} fill={INK} fontWeight={700} textAnchor="end">2/2</text>
      </g>
    </svg>
  )
}

export default function OverlayOffsetModal({
  opened, onClose, printerName, printerLabel, offsetX, offsetY, onSaved, onPrintSample, sampleStudent,
}: {
  opened: boolean
  onClose: () => void
  printerName: string
  printerLabel: string
  offsetX: number
  offsetY: number
  onSaved: () => void
  /** Fourni seulement depuis l'impression d'un sujet : l'essai a besoin d'une copie. */
  onPrintSample?: () => Promise<void>
  sampleStudent?: string
}) {
  const [current, setCurrent] = useState({ x: offsetX, y: offsetY })
  // Écart observé sur la feuille, avec le réglage actuel déjà appliqué.
  const [seen, setSeen] = useState({ x: 0, y: 0 })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (opened) {
      setCurrent({ x: offsetX, y: offsetY })
      setSeen({ x: 0, y: 0 })
    }
  }, [opened, offsetX, offsetY])

  function move(dx: number, dy: number) {
    setSeen((value) => {
      const clamp = (next: number, base: number) =>
        Math.max(base - LIMIT_MM, Math.min(base + LIMIT_MM, next))
      return { x: clamp(value.x + dx, current.x), y: clamp(value.y + dy, current.y) }
    })
  }

  const next = { x: current.x - seen.x, y: current.y - seen.y }
  const adjusted = seen.x !== 0 || seen.y !== 0
  const nextLabel = overlayOffsetLabel(next.x, next.y)
  const seenLabel = direction(seen.x, seen.y, {
    left: 'trop à gauche', right: 'trop à droite', up: 'trop haut', down: 'trop bas',
  })

  async function save(target: { x: number; y: number }) {
    await api.patch('/api/printers/preferences', {
      name: printerName, overlay_offset_x_mm: target.x, overlay_offset_y_mm: target.y,
    })
    setCurrent(target)
    setSeen({ x: 0, y: 0 })
    onSaved()
  }

  async function run(action: () => Promise<void>) {
    setBusy(true)
    try {
      await action()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const arrow = (label: string, icon: React.ReactNode, dx: number, dy: number, area: string) => (
    <ActionIcon variant="light" size="lg" aria-label={label} title={label}
      style={{ gridArea: area }} onClick={() => move(dx, dy)}>
      {icon}
    </ActionIcon>
  )

  return (
    <Modal opened={opened} onClose={onClose} zIndex={400} size={480}
      title={`Position de l’overlay — ${printerLabel}`}>
      <Stack gap="sm">
        <Alert color="blue" p="xs">
          <Text size="sm">
            <b>La copie est déjà imprimée : elle ne bouge pas.</b> C’est l’overlay, pas encore
            imprimée, que l’on déplace.
          </Text>
        </Alert>

        <Box>
          <Text size="sm" fw={600}>Prenez une feuille où l’overlay est déjà imprimée</Text>
          <Text size="sm" c="dimmed">
            Avec les flèches, placez le rouge comme sur votre feuille. Chaque clic : 0,5 mm.
          </Text>
        </Box>

        <Group align="center" wrap="nowrap" gap="sm">
          <Box style={{ flex: 1, minWidth: 0 }}>
            <PaperPreview x={seen.x} y={seen.y} />
            <Text size="xs" c="dimmed" mt={4}>
              <Text span c="gray.6" fw={700}>Gris</Text> : la copie, fixe.{' '}
              <Text span c="red.8" fw={700}>Rouge</Text> : la correction.
            </Text>
          </Box>
          <Box style={{
            display: 'grid', gap: 4, flexShrink: 0,
            gridTemplateAreas: '". up ." "left . right" ". down ."',
          }}>
            {arrow('La correction est trop haut sur ma feuille', <ArrowUp size={18} />, 0, STEP_MM, 'up')}
            {arrow('La correction est trop à gauche sur ma feuille', <ArrowLeft size={18} />, -STEP_MM, 0, 'left')}
            {arrow('La correction est trop à droite sur ma feuille', <ArrowRight size={18} />, STEP_MM, 0, 'right')}
            {arrow('La correction est trop bas sur ma feuille', <ArrowDown size={18} />, 0, -STEP_MM, 'down')}
          </Box>
        </Group>

        <Box p="xs" style={{ borderRadius: 4, background: 'var(--mantine-color-default-hover)' }}>
          {adjusted && (
            <Text size="sm">Sur votre feuille : la correction est <b>{seenLabel}</b>.</Text>
          )}
          <Text size="sm" mt={adjusted ? 4 : 0} data-testid="offset-result">
            {nextLabel
              ? <>L’overlay sera imprimée <b>{nextLabel}</b>.</>
              : <>L’overlay sera imprimée <b>à sa position normale</b>.</>}
          </Text>
        </Box>

        {onPrintSample && sampleStudent && (
          <Text size="xs" c="dimmed">
            Pour vérifier sans risque : mettez une feuille blanche dans le bac et imprimez un essai.
            Posez-le sur la copie de {sampleStudent} et regardez par transparence devant une fenêtre.
          </Text>
        )}

        <Group justify="space-between" gap="xs">
          {current.x !== 0 || current.y !== 0 ? (
            <Button size="xs" variant="subtle" color="gray" loading={busy}
              onClick={() => run(() => save({ x: 0, y: 0 }))}>
              Revenir à la position normale
            </Button>
          ) : <span />}
          <Group gap="xs">
            {onPrintSample && sampleStudent && (
              <Button size="xs" variant="light" loading={busy} onClick={() => run(async () => {
                if (adjusted) await save(next)
                await onPrintSample()
              })}>
                Imprimer un essai
              </Button>
            )}
            <Button size="xs" disabled={!adjusted} loading={busy}
              onClick={() => run(() => save(next))}>
              Enregistrer
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  )
}
