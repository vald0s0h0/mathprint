import { ActionIcon, Alert, Box, Button, Group, Loader, SegmentedControl, Stack, Text, Tooltip } from '@mantine/core'
import { Crop, Eraser, Hand, Save, Trash2, ZoomIn, ZoomOut } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuthenticatedImageUrl } from './AuthImg'

export type ImageRect = { x0: number; y0: number; x1: number; y1: number }
export type FigureBox = ImageRect & {
  page_index?: number; img_w?: number; img_h?: number; masks?: ImageRect[]
}
type Point = { x: number; y: number }
type Handle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'
type Drag =
  | { kind: 'new'; origin: Point }
  | { kind: 'move-crop'; origin: Point; initial: ImageRect }
  | { kind: 'resize'; origin: Point; initial: ImageRect; handle: Handle }
  | { kind: 'pan'; client: Point; initial: Point }

const MIN_CROP = 20
const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))
const ordered = (a: Point, b: Point): ImageRect => ({
  x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y),
  x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y),
})
const sameRects = (a: ImageRect[], b: ImageRect[]) =>
  a.length === b.length && a.every((r, i) =>
    r.x0 === b[i].x0 && r.y0 === b[i].y0 && r.x1 === b[i].x1 && r.y1 === b[i].y1)

export default function FigureEditor({ exerciseId, figureBox, busy, onApply }: {
  exerciseId: string; figureBox: FigureBox; busy?: boolean
  onApply: (crop: ImageRect, masks: ImageRect[]) => Promise<void>
}) {
  const { url, err } = useAuthenticatedImageUrl(`/api/indigo/exercises/${exerciseId}/figure/source.png`)
  const viewportRef = useRef<HTMLDivElement>(null)
  const [natural, setNatural] = useState({ w: figureBox.img_w || 1, h: figureBox.img_h || 1 })
  const [tool, setTool] = useState<'crop' | 'mask' | 'pan'>('crop')
  const [crop, setCrop] = useState<ImageRect>(figureBox)
  const [masks, setMasks] = useState<ImageRect[]>(figureBox.masks || [])
  const [selectedMask, setSelectedMask] = useState<number | null>(null)
  const [maskStart, setMaskStart] = useState<Point | null>(null)
  const [cursor, setCursor] = useState<Point | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [scale, setScale] = useState(1)
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 })

  const fitCrop = useCallback((box: ImageRect, w: number, h: number) => {
    const vp = viewportRef.current
    if (!vp || w <= 1 || h <= 1) return
    const next = clamp(Math.min((vp.clientWidth - 80) / Math.max(MIN_CROP, box.x1 - box.x0),
      (vp.clientHeight - 80) / Math.max(MIN_CROP, box.y1 - box.y0)), 0.2, 5)
    setScale(next)
    setPan({
      x: vp.clientWidth / 2 - ((box.x0 + box.x1) / 2) * next,
      y: vp.clientHeight / 2 - ((box.y0 + box.y1) / 2) * next,
    })
  }, [])

  useEffect(() => {
    const nextMasks = figureBox.masks || []
    setCrop(figureBox); setMasks(nextMasks); setSelectedMask(null)
    setMaskStart(null); setCursor(null); setDrag(null)
    fitCrop(figureBox, natural.w, natural.h)
  }, [figureBox.x0, figureBox.y0, figureBox.x1, figureBox.y1,
    figureBox.masks, natural.w, natural.h, fitCrop])

  const sourcePoint = (clientX: number, clientY: number): Point => {
    const r = viewportRef.current!.getBoundingClientRect()
    return {
      x: clamp(Math.round((clientX - r.left - pan.x) / scale), 0, natural.w),
      y: clamp(Math.round((clientY - r.top - pan.y) / scale), 0, natural.h),
    }
  }
  const startDrag = (e: React.PointerEvent<HTMLDivElement>, next: Drag) => {
    e.preventDefault(); e.stopPropagation()
    setDrag(next); e.currentTarget.setPointerCapture(e.pointerId)
  }
  const backgroundDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (tool === 'mask') return
    if (tool === 'pan') startDrag(e, { kind: 'pan', client: { x: e.clientX, y: e.clientY }, initial: pan })
    else startDrag(e, { kind: 'new', origin: sourcePoint(e.clientX, e.clientY) })
  }
  const resizeCrop = (initial: ImageRect, handle: Handle, p: Point): ImageRect => {
    let { x0, y0, x1, y1 } = initial
    if (handle.includes('w')) x0 = Math.min(p.x, x1 - MIN_CROP)
    if (handle.includes('e')) x1 = Math.max(p.x, x0 + MIN_CROP)
    if (handle.includes('n')) y0 = Math.min(p.y, y1 - MIN_CROP)
    if (handle.includes('s')) y1 = Math.max(p.y, y0 + MIN_CROP)
    return { x0: clamp(x0, 0, natural.w), y0: clamp(y0, 0, natural.h),
      x1: clamp(x1, 0, natural.w), y1: clamp(y1, 0, natural.h) }
  }
  const pointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const p = sourcePoint(e.clientX, e.clientY)
    if (!drag) { if (tool === 'mask' && maskStart) setCursor(p); return }
    if (drag.kind === 'new') setCrop(ordered(drag.origin, p))
    if (drag.kind === 'resize') setCrop(resizeCrop(drag.initial, drag.handle, p))
    if (drag.kind === 'move-crop') {
      const dx = p.x - drag.origin.x, dy = p.y - drag.origin.y
      const w = drag.initial.x1 - drag.initial.x0, h = drag.initial.y1 - drag.initial.y0
      const x0 = clamp(drag.initial.x0 + dx, 0, natural.w - w)
      const y0 = clamp(drag.initial.y0 + dy, 0, natural.h - h)
      setCrop({ x0, y0, x1: x0 + w, y1: y0 + h })
    }
    if (drag.kind === 'pan') setPan({ x: drag.initial.x + e.clientX - drag.client.x,
      y: drag.initial.y + e.clientY - drag.client.y })
  }
  const pointerUp = () => {
    if (crop.x1 - crop.x0 < MIN_CROP || crop.y1 - crop.y0 < MIN_CROP) setCrop(figureBox)
    setDrag(null)
  }
  const maskClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (tool !== 'mask') return
    const p = sourcePoint(e.clientX, e.clientY)
    if (!maskStart) { setMaskStart(p); setCursor(p); setSelectedMask(null); return }
    const next = ordered(maskStart, p)
    if (next.x1 - next.x0 >= 2 && next.y1 - next.y0 >= 2) {
      setMasks((old) => [...old, next]); setSelectedMask(masks.length)
    }
    setMaskStart(null); setCursor(null)
  }
  const zoomAt = (factor: number, client?: Point) => {
    const vp = viewportRef.current
    if (!vp) return
    const r = vp.getBoundingClientRect()
    const anchor = client || { x: r.left + vp.clientWidth / 2, y: r.top + vp.clientHeight / 2 }
    const source = { x: (anchor.x - r.left - pan.x) / scale, y: (anchor.y - r.top - pan.y) / scale }
    const next = clamp(scale * factor, 0.2, 6)
    setScale(next)
    setPan({ x: anchor.x - r.left - source.x * next, y: anchor.y - r.top - source.y * next })
  }
  const wheel = (e: React.WheelEvent<HTMLDivElement>) => {
    e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, { x: e.clientX, y: e.clientY })
  }
  const maskPreview = maskStart && cursor ? ordered(maskStart, cursor) : null
  const changed = crop.x0 !== figureBox.x0 || crop.y0 !== figureBox.y0
    || crop.x1 !== figureBox.x1 || crop.y1 !== figureBox.y1
    || !sameRects(masks, figureBox.masks || [])
  const handlePositions: Record<Handle, React.CSSProperties> = {
    nw: { left: 0, top: 0 }, n: { left: '50%', top: 0 }, ne: { left: '100%', top: 0 },
    e: { left: '100%', top: '50%' }, se: { left: '100%', top: '100%' },
    s: { left: '50%', top: '100%' }, sw: { left: 0, top: '100%' }, w: { left: 0, top: '50%' },
  }

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="flex-end">
        <Box>
          <Text size="sm" fw={650}>Éditeur de l’image</Text>
          <Text size="xs" c="dimmed">
            {tool === 'crop' ? 'Déplace le cadre ou utilise ses poignées. Glisse hors du cadre pour en dessiner un nouveau.'
              : tool === 'pan' ? 'Fais glisser la page pour atteindre une autre zone.'
                : maskStart ? 'Clique sur l’angle opposé du texte à cacher.'
                  : 'Clique sur les deux angles opposés du texte à cacher.'}
          </Text>
        </Box>
        <Group gap={6}>
          <SegmentedControl size="xs" value={tool} onChange={(v) => {
            setTool(v as typeof tool); setMaskStart(null); setCursor(null); setDrag(null)
          }} data={[
            { value: 'crop', label: 'Cadrage' }, { value: 'mask', label: 'Caches' },
            { value: 'pan', label: 'Déplacer la vue' },
          ]} />
          <Tooltip label="Dézoomer"><ActionIcon variant="default" onClick={() => zoomAt(1 / 1.25)}><ZoomOut size={16} /></ActionIcon></Tooltip>
          <Text size="xs" w={42} ta="center">{Math.round(scale * 100)} %</Text>
          <Tooltip label="Zoomer"><ActionIcon variant="default" onClick={() => zoomAt(1.25)}><ZoomIn size={16} /></ActionIcon></Tooltip>
          <Button size="compact-xs" variant="default" onClick={() => fitCrop(crop, natural.w, natural.h)}>Recentrer</Button>
        </Group>
      </Group>

      {err && <Alert color="red">La page originale du PDF est indisponible.</Alert>}
      {!url && !err && <Box h={480} style={{ display: 'grid', placeItems: 'center' }}><Loader size="sm" /></Box>}
      {url && (
        <Box ref={viewportRef} h={480} onPointerDown={backgroundDown} onPointerMove={pointerMove}
          onPointerUp={pointerUp} onPointerCancel={pointerUp} onClick={maskClick} onWheel={wheel}
          style={{ position: 'relative', overflow: 'hidden', background: 'var(--mantine-color-dark-7)',
            borderRadius: 6, touchAction: 'none', cursor: tool === 'pan' ? 'grab' : 'crosshair', userSelect: 'none' }}>
          <Box style={{ position: 'absolute', left: 0, top: 0, width: natural.w, height: natural.h,
            transformOrigin: '0 0', transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})` }}>
            <img src={url} alt="Page originale du manuel" draggable={false}
              onLoad={(e) => {
                const dims = { w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight }
                setNatural(dims); requestAnimationFrame(() => fitCrop(crop, dims.w, dims.h))
              }} style={{ display: 'block', width: natural.w, height: natural.h, pointerEvents: 'none' }} />
            <Box onPointerDown={(e) => tool === 'crop' && startDrag(e, {
              kind: 'move-crop', origin: sourcePoint(e.clientX, e.clientY), initial: crop,
            })} style={{ position: 'absolute', left: crop.x0, top: crop.y0,
              width: crop.x1 - crop.x0, height: crop.y1 - crop.y0,
              border: `${2 / scale}px solid var(--mantine-color-blue-5)`,
              boxShadow: `0 0 0 ${9999 / scale}px rgba(0,0,0,.42)`, boxSizing: 'border-box',
              pointerEvents: tool === 'crop' ? 'auto' : 'none', cursor: 'move' }}>
              {(Object.keys(handlePositions) as Handle[]).map((handle) =>
                <Box key={handle} onPointerDown={(e) => tool === 'crop' && startDrag(e, {
                  kind: 'resize', origin: sourcePoint(e.clientX, e.clientY), initial: crop, handle,
                })} style={{ position: 'absolute', ...handlePositions[handle],
                  width: 12 / scale, height: 12 / scale, borderRadius: 2 / scale,
                  background: '#fff', border: `${2 / scale}px solid var(--mantine-color-blue-6)`,
                  transform: 'translate(-50%, -50%)', pointerEvents: 'auto', cursor: `${handle}-resize` }} />)}
            </Box>
            {masks.map((r, i) => <Box key={i} onClick={(e) => {
              if (tool !== 'mask') return; e.stopPropagation(); setSelectedMask(i); setMaskStart(null)
            }} style={{ position: 'absolute', left: r.x0, top: r.y0, width: r.x1-r.x0, height: r.y1-r.y0,
              background: '#fff', border: `${(selectedMask === i ? 3 : 1) / scale}px ${selectedMask === i ? 'solid #fa5252' : 'dashed #868e96'}`,
              boxSizing: 'border-box', pointerEvents: tool === 'mask' ? 'auto' : 'none', cursor: 'pointer' }} />)}
            {tool === 'mask' && maskPreview && <Box style={{ position: 'absolute', left: maskPreview.x0,
              top: maskPreview.y0, width: maskPreview.x1-maskPreview.x0, height: maskPreview.y1-maskPreview.y0,
              background: 'rgba(255,255,255,.8)', border: `${2/scale}px dashed #fa5252`, pointerEvents: 'none' }} />}
          </Box>
        </Box>
      )}

      <Group justify="space-between">
        <Group gap={8}>
          {tool === 'crop' ? <Crop size={14} /> : tool === 'mask' ? <Eraser size={14} /> : <Hand size={14} />}
          <Text size="xs" c="dimmed">{masks.length} cache{masks.length !== 1 ? 's' : ''}</Text>
          {selectedMask !== null && <Button size="compact-xs" color="red" variant="light"
            leftSection={<Trash2 size={12} />} onClick={() => {
              setMasks((old) => old.filter((_, i) => i !== selectedMask)); setSelectedMask(null)
            }}>Supprimer le cache sélectionné</Button>}
        </Group>
        <Button size="xs" leftSection={<Save size={14} />} disabled={!changed || !url}
          loading={busy} onClick={() => onApply(crop, masks)}>Appliquer à l’image</Button>
      </Group>
    </Stack>
  )
}
