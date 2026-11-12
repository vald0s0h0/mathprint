// Une ligne = un lot de scan d'une correction. Le lot principal et les lots déposés
// après la validation s'affichent à l'identique : seule la taille (pages) les distingue.
// Chaque lot a son pipeline, son aperçu et sa prochaine action ; ce qui est propre à
// la correction entière (titre, absents) reste dans l'en-tête de la carte.
import { useState } from 'react'
import { Badge, Button, Group, Menu, Text, Tooltip } from '@mantine/core'
import { Eye, PencilLine, RefreshCw, Printer, ScanLine, Send, Trash2, Undo2 } from 'lucide-react'
import ActionsMenu from './ActionsMenu'
import { LateConflictButton } from './LateJobs'
import PrintButton from './PrintButton'
import SegmentBar, { STEP_COUNT, type Segment } from './SegmentBar'

export type LotStage =
  'awaiting' | 'processing' | 'error' | 'ocr_review' | 'review' | 'conflict' | 'ready'

export type Lot = {
  wave: number
  stage: LotStage
  segments: Segment[]
  pages: number
  pendingOcr: number
  pendingReviews: number
  conflicts: number
  overlayReady: boolean
  printed: boolean
  distributed: boolean
  stale: boolean
  rebuilding: boolean
  /** Lecture locale sur le Mac (mode cascade) : avancement et présence du worker. */
  ocrLocal?: { decided: number; total: number; worker_online: boolean; waiting_hours?: number; timeout_hours?: number; overdue?: boolean } | null
  /** Élèves du lot (nommés à côté du pipeline s'il y en a moins de 4). */
  students: string[]
  /** Dépôt et pages du lot (infobulle du badge). */
  info: string
}

export type LotHandlers = {
  batchId: string
  assessmentId: string
  /** Réponses DeepSeek indisponibles (lot principal seulement). */
  pendingLlm?: number
  onPreview: () => void
  onOcr: () => void
  onCorrect: (scope: 'flagged' | 'all') => void
  onRetry: () => void
  /** Lot en attente du Mac depuis trop longtemps : le relire avec Mathpix. */
  onOcrFallback?: () => void
  onRetryLlm?: () => void
  onRefine: () => void
  onRegenerate: () => void
  onRecalc?: () => void
  onReset?: () => void
  onDistributed: (value: boolean) => void
  onChanged: () => void
}

// largeur de « 35 copies »
const LOT_W = 108

export default function ScanLotRow({ lot, h }: { lot: Lot; h: LotHandlers }) {
  const ready = lot.stage === 'ready'
  const [reprintOpen, setReprintOpen] = useState(false)
  // « Affiner » en bouton, à gauche de l'action principale, quand l'impression de l'overlay est
  // attendue ; l'aperçu est alors dans le menu ⋮ (comme dans les autres états)
  const printExpected = ready && lot.overlayReady && !lot.stale && !lot.printed
  const n = lot.students.length
  // « Distribué » n'est à faire qu'une fois l'overlay à jour et imprimé
  const distributable = ready && lot.overlayReady && !lot.stale && lot.printed && !lot.distributed
  // Rend 4 cellules de la grille de la carte (voir Corrections) : taille du lot,
  // pipeline (avec « Distribué »), élèves, actions — alignées d'une ligne à l'autre.
  return (
    <>
      <Tooltip label={lot.info} disabled={!lot.info} multiline maw={320}>
        <Badge size="lg" variant="light" color="indigo" radius="sm" tt="none" w={LOT_W} mr={16}
          data-testid={`scan-lot-${lot.wave}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
          {lot.stage === 'awaiting' ? '—' : `${n} copie${n > 1 ? 's' : ''}`}
        </Badge>
      </Tooltip>

      {lot.stage === 'awaiting' ? (
        <Text size="xs" c="dimmed" style={{ gridColumn: `2 / ${STEP_COUNT + 3}` }}>
          En attente de scan — déposez-le dans le <b>bac à sable</b> en haut de page.
        </Text>
      ) : (
        <>
          <SegmentBar segments={lot.segments} extra={{
            phase: 'distribute', label: 'Distribué',
            state: lot.distributed ? 'green' : distributable ? 'orange' : 'gray',
            // cliquable une fois l'overlay imprimé : on coche / décoche la distribution
            onClick: distributable || lot.distributed
              ? () => h.onDistributed(!lot.distributed) : undefined,
            hint: distributable || lot.distributed ? 'Cliquer pour marquer comme distribué aux élèves' : undefined,
          }} />
          <Text size="xs" c="dimmed" title={lot.students.join(', ')}
            style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0, marginLeft: 16 }}>
            {n > 0 && n < 4 ? lot.students.join(', ') : ''}
          </Text>
        </>
      )}

      {lot.stage !== 'awaiting' ? (
        <Group gap="xs" wrap="nowrap" justify="flex-end" ml={16}>
          {/* moment d'imprimer l'overlay : « Affiner » (relire / corriger) avant d'imprimer */}
          {printExpected && (
            <Button size="xs" variant="subtle" leftSection={<PencilLine size={14} />} onClick={h.onRefine}>
              Affiner
            </Button>
          )}

          {lot.stage === 'conflict' && (
            <LateConflictButton batchId={h.batchId} count={lot.conflicts} onResolved={h.onChanged} />
          )}
          {lot.stage === 'processing' && lot.ocrLocal && (
            <Tooltip label={lot.ocrLocal.worker_online ? 'Les moteurs locaux lisent les écritures'
              : 'Le worker n\'a pas sondé le serveur depuis plus de 2 minutes'} multiline maw={280}>
              <Button size="xs" variant="light" color={lot.ocrLocal.worker_online ? 'blue' : 'gray'}
                loading={lot.ocrLocal.worker_online} disabled data-testid="ocr-local-status">
                {lot.ocrLocal.worker_online
                  ? `Lecture en cours sur le Mac (${lot.ocrLocal.decided}/${lot.ocrLocal.total})`
                  : 'Mac hors ligne, la lecture reprendra à son retour'}
              </Button>
            </Tooltip>
          )}
          {lot.stage === 'processing' && lot.ocrLocal?.overdue && h.onOcrFallback && (
            <Tooltip multiline maw={300} label={`En attente du Mac depuis ${Math.round(lot.ocrLocal.waiting_hours ?? 0)} h : relire ce lot avec Mathpix, comme avant la lecture locale`}>
              <Button size="xs" color="orange" variant="light" leftSection={<RefreshCw size={14} />}
                onClick={h.onOcrFallback} data-testid="ocr-fallback">Lire avec Mathpix</Button>
            </Tooltip>
          )}
          {lot.stage === 'processing' && !lot.ocrLocal && (
            <Button size="xs" variant="light" loading disabled>Correction en cours…</Button>
          )}
          {lot.stage === 'ocr_review' && (
            <Button size="xs" color="blue" leftSection={<ScanLine size={14} />} onClick={h.onOcr}>
              {lot.pendingOcr > 0 ? `OCRiser (${lot.pendingOcr})` : 'OCRiser : revoir et lancer la correction'}
            </Button>
          )}
          {lot.stage === 'error' && (
            <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />} onClick={h.onRetry}>
              Relancer
            </Button>
          )}
          {lot.stage === 'review' && (
            <Button size="xs" color="orange" leftSection={<ScanLine size={14} />}
              onClick={() => h.onCorrect('flagged')}>
              Corriger les copies ({lot.pendingReviews})
            </Button>
          )}
          {ready && lot.overlayReady && lot.stale && (
            <Tooltip label="Des corrections affinées ne sont pas encore sur l'overlay : régénérez-le avant d'imprimer">
              <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />}
                loading={lot.rebuilding} onClick={h.onRegenerate}>
                Régénérer l'overlay
              </Button>
            </Tooltip>
          )}
          {ready && lot.overlayReady && !lot.stale && !lot.printed && (
            <PrintButton assessmentId={h.assessmentId} file="correction_overlay.pdf"
              wave={lot.wave} label="Imprimer l'overlay" />
          )}
          {ready && !lot.overlayReady && (
            <Button size="xs" variant="light" loading disabled>Génération de l'overlay…</Button>
          )}
          {/* Étape « Distribué » à faire : son bouton, comme les autres étapes */}
          {distributable && (
            <Button size="xs" variant="light" leftSection={<Send size={14} />}
              onClick={() => h.onDistributed(true)}>
              Marquer distribué
            </Button>
          )}

          {ready && lot.overlayReady && !lot.stale && lot.printed && (
            <PrintButton assessmentId={h.assessmentId} file="correction_overlay.pdf"
              wave={lot.wave} label="Imprimer l'overlay" hiddenTrigger
              open={reprintOpen} onOpenChange={setReprintOpen} />
          )}
          <ActionsMenu>
            {lot.overlayReady && (
              <Menu.Item leftSection={<Eye size={14} />} onClick={h.onPreview}>Aperçu</Menu.Item>
            )}
            {ready && lot.overlayReady && !lot.stale && lot.printed && (
              <Menu.Item leftSection={<Printer size={14} />} onClick={() => setReprintOpen(true)}>
                Imprimer l'overlay
              </Menu.Item>
            )}
            {ready && lot.overlayReady && !lot.distributed && !printExpected && (
              <Tooltip position="left" label="Relire chaque copie avec son overlay et corriger une note en modifiant la réponse de l'élève">
                <Menu.Item leftSection={<PencilLine size={14} />} onClick={h.onRefine}>
                  Affiner
                </Menu.Item>
              </Tooltip>
            )}
            {ready && lot.distributed && (
              <Menu.Item leftSection={<Undo2 size={14} />} onClick={() => h.onDistributed(false)}>
                Annuler la distribution
              </Menu.Item>
            )}
            {(ready || lot.stage === 'error') && (
              <Menu.Item leftSection={<ScanLine size={14} />} onClick={() => h.onCorrect('all')}>
                Corriger
              </Menu.Item>
            )}
            {ready && h.onRecalc && (
              <Tooltip position="left" label="Recalculer les notes et régénérer les copies corrigées après un ajustement">
                <Menu.Item leftSection={<RefreshCw size={14} />} onClick={h.onRecalc}>
                  Recalculer les notes
                </Menu.Item>
              </Tooltip>
            )}
            {lot.stage === 'processing' && (
              <Tooltip position="left" label="Si la correction semble bloquée, relancez-la">
                <Menu.Item leftSection={<RefreshCw size={14} />} onClick={h.onRetry}>
                  Relancer
                </Menu.Item>
              </Tooltip>
            )}
            {lot.stage === 'review' && (h.pendingLlm ?? 0) > 0 && h.onRetryLlm && (
              <Tooltip position="left" label="Retenter uniquement les verdicts DeepSeek indisponibles, sans refaire l’OCR">
                <Menu.Item leftSection={<RefreshCw size={14} />} onClick={h.onRetryLlm}>
                  Relancer l’IA ({h.pendingLlm})
                </Menu.Item>
              </Tooltip>
            )}
            {h.onReset && lot.stage !== 'processing' && (
              <Tooltip position="left" label="Effacer cette correction et re-scanner depuis zéro">
                <Menu.Item color="red" leftSection={<Trash2 size={14} />} onClick={h.onReset}>
                  Effacer
                </Menu.Item>
              </Tooltip>
            )}
          </ActionsMenu>
        </Group>
      ) : <div />}
    </>
  )
}
