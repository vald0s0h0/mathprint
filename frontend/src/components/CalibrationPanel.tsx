// Assistant de calibration imprimante/scanner. Il appartient à l'Atelier ; sans
// Atelier (édition autonome), le prof l'a dans ses paramètres.
import { Button, Card, FileButton, Group, Table, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useEffect, useState } from 'react'
import { api, getToken } from '../api'

type Calibration = {
  id: string; printer: string | null; scale_x: number; scale_y: number
  rotation_deg: number; offset_x_mm: number; offset_y_mm: number; validated_at: string | null
}

export default function CalibrationPanel() {
  const [calibrations, setCalibrations] = useState<Calibration[]>([])

  function refresh() {
    api.get<Calibration[]>('/api/system/calibration/profiles').then(setCalibrations).catch(() => {})
  }
  useEffect(refresh, [])

  async function downloadPage() {
    const res = await fetch('/api/system/calibration/page', {
      method: 'POST', headers: { Authorization: `Bearer ${getToken()}` },
    })
    if (!res.ok) {
      notifications.show({ color: 'red', message: `Page test indisponible (${res.status})` })
      return
    }
    const a = document.createElement('a')
    a.href = URL.createObjectURL(await res.blob())
    a.download = 'calibration_page.pdf'
    a.click()
  }

  async function uploadScan(file: File | null) {
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
    <Card withBorder>
      <Text fw={600}>Assistant de calibration imprimante/scanner</Text>
      <Text size="sm" c="dimmed" mt="xs">
        1. Télécharger la page test → 2. L'imprimer à 100 % → 3. La scanner →
        4. Déposer le scan : offsets, échelle et rotation sont mesurés sur les 4 marqueurs.
      </Text>
      <Group mt="sm">
        <Button size="xs" onClick={downloadPage}>Télécharger la page test</Button>
        <FileButton onChange={uploadScan} accept="application/pdf,image/*">
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
  )
}
