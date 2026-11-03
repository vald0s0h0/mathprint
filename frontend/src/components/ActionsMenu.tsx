// Menu « ⋮ » d'une carte (Sujets, Corrections) : les commandes secondaires, à
// côté du bouton de l'action attendue.
import { ActionIcon, Menu } from '@mantine/core'
import { EllipsisVertical } from 'lucide-react'
import type { ReactNode } from 'react'

export default function ActionsMenu({ children }: { children: ReactNode }) {
  return (
    <Menu shadow="md" position="bottom-end" withinPortal>
      <Menu.Target>
        <ActionIcon variant="subtle" color="gray" size="lg" aria-label="Autres actions">
          <EllipsisVertical size={16} />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown>{children}</Menu.Dropdown>
    </Menu>
  )
}
