import { Accordion, Box, Group, Stack, Table, Text } from '@mantine/core'
import type { CSSProperties, ReactNode } from 'react'

export type CompetencyHierarchyChapter<T> = {
  key: string
  code: string
  name: string
  rows: T[]
}

export type CompetencyHierarchyDomain<T> = {
  key: string
  code: string
  name: string
  chapters: CompetencyHierarchyChapter<T>[]
}

export type CompetencyHierarchyColumn<T> = {
  key: string
  label: ReactNode
  width?: CSSProperties['width']
  align?: 'left' | 'center' | 'right'
  render: (row: T, chapter: CompetencyHierarchyChapter<T>) => ReactNode
}

type Props<T> = {
  domains: CompetencyHierarchyDomain<T>[]
  getRowKey: (row: T) => string
  getShortId: (row: T) => string
  getLabel: (row: T) => string
  columns?: CompetencyHierarchyColumn<T>[]
  columnGroupLabel?: ReactNode
  selectedKey?: string | null
  onRowClick?: (row: T) => void
  chapterAside?: (chapter: CompetencyHierarchyChapter<T>) => ReactNode
  showColumnHeaders?: boolean
}

/**
 * Présentation unique domaine > chapitre > compétence.
 *
 * Le bleu identifie seulement le niveau domaine (repliable). Le chapitre est
 * isolé par un bandeau gris ; les retraits suffisent à rendre la filiation
 * lisible sans ajouter de filets verticaux.
 */
export default function CompetencyHierarchy<T>({
  domains, getRowKey, getShortId, getLabel, columns = [], columnGroupLabel,
  selectedKey, onRowClick, chapterAside, showColumnHeaders = true,
}: Props<T>) {
  const accordionKey = domains.map((domain) => domain.key).join('|')

  return (
    <Accordion key={accordionKey} multiple variant="separated" radius="md"
      defaultValue={domains.map((domain) => domain.key)} styles={{ item: { marginBottom: 16 } }}>
      {domains.map((domain) => {
        const count = domain.chapters.reduce((total, chapter) => total + chapter.rows.length, 0)
        return (
          <Accordion.Item key={domain.key} value={domain.key} style={{
            overflow: 'hidden',
            borderLeft: '4px solid var(--mantine-color-blue-5)',
          }}>
            <Accordion.Control style={{ background: 'var(--mantine-color-blue-light)' }}>
              <Group gap="sm" wrap="nowrap">
                <Text fw={750} size="sm">
                  {domain.code ? `${domain.code} — ` : ''}{domain.name || 'Domaine'}
                </Text>
                <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                  {count} compétence{count > 1 ? 's' : ''}
                </Text>
              </Group>
            </Accordion.Control>

            <Accordion.Panel>
              <Stack gap={24} pt={4} pb={6}>
                {domain.chapters.map((chapter) => (
                  <Box key={chapter.key} style={{
                    paddingLeft: 12,
                  }}>
                    <Group justify="space-between" gap="sm" wrap="wrap" mb={8} px={10} py={7}
                      style={{
                        background: 'var(--mantine-color-gray-light)',
                        borderRadius: 'var(--mantine-radius-sm)',
                      }}>
                      <Group gap={7} wrap="nowrap">
                        <Text size="xs" fw={750} c="gray.8">
                          {chapter.code ? `${chapter.code} — ` : ''}{chapter.name || 'Chapitre'}
                        </Text>
                        <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                          {chapter.rows.length} compétence{chapter.rows.length > 1 ? 's' : ''}
                        </Text>
                      </Group>
                      {chapterAside?.(chapter)}
                    </Group>

                    <Table highlightOnHover verticalSpacing={5} horizontalSpacing="sm" fz="sm"
                      style={{ width: '100%', tableLayout: 'fixed' }}>
                      <colgroup>
                        <col />
                        {columns.map((column) => (
                          <col key={column.key} style={{ width: column.width }} />
                        ))}
                      </colgroup>
                      {columns.length > 0 && showColumnHeaders && (
                        <Table.Thead>
                          {columnGroupLabel ? (
                            <>
                              <Table.Tr>
                                <Table.Th rowSpan={2}>Compétence</Table.Th>
                                <Table.Th colSpan={columns.length} ta="center"
                                  style={{ borderLeft: '1px solid var(--mantine-color-gray-3)' }}>
                                  {columnGroupLabel}
                                </Table.Th>
                              </Table.Tr>
                              <Table.Tr>
                                {columns.map((column, index) => (
                                  <Table.Th key={column.key} w={column.width} ta={column.align ?? 'left'}
                                    style={index === 0
                                      ? { borderLeft: '1px solid var(--mantine-color-gray-3)' }
                                      : undefined}>
                                    {column.label}
                                  </Table.Th>
                                ))}
                              </Table.Tr>
                            </>
                          ) : (
                            <Table.Tr>
                              <Table.Th>Compétence</Table.Th>
                              {columns.map((column, index) => (
                                <Table.Th key={column.key} w={column.width} ta={column.align ?? 'left'}
                                  style={index === 0
                                    ? { borderLeft: '1px solid var(--mantine-color-gray-3)' }
                                    : undefined}>
                                  {column.label}
                                </Table.Th>
                              ))}
                            </Table.Tr>
                          )}
                        </Table.Thead>
                      )}
                      <Table.Tbody>
                        {chapter.rows.map((row) => {
                          const rowKey = getRowKey(row)
                          const clickable = Boolean(onRowClick)
                          return (
                            <Table.Tr key={rowKey}
                              bg={selectedKey === rowKey ? 'var(--mantine-color-blue-light)' : undefined}
                              style={{ cursor: clickable ? 'pointer' : undefined }}
                              tabIndex={clickable ? 0 : undefined}
                              onClick={() => onRowClick?.(row)}
                              onKeyDown={(event) => {
                                if (clickable && (event.key === 'Enter' || event.key === ' ')) {
                                  event.preventDefault()
                                  onRowClick?.(row)
                                }
                              }}>
                              <Table.Td>
                                <Box py={2} pl={10}>
                                  <Text span size="xs" c="dimmed" fw={650} mr={7}>
                                    {getShortId(row)}
                                  </Text>
                                  <Text span size="sm" fw={500}>{getLabel(row)}</Text>
                                </Box>
                              </Table.Td>
                              {columns.map((column, index) => (
                                <Table.Td key={column.key} ta={column.align ?? 'left'}
                                  style={index === 0
                                    ? { borderLeft: '1px solid var(--mantine-color-gray-3)' }
                                    : undefined}>
                                  {column.render(row, chapter)}
                                </Table.Td>
                              ))}
                            </Table.Tr>
                          )
                        })}
                      </Table.Tbody>
                    </Table>
                  </Box>
                ))}
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        )
      })}
    </Accordion>
  )
}
