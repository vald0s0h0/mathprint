// Raccourcis de correction manuelle. `scope="personal"` : les miens (roue
// crantée) ; `scope="default"` : la valeur par défaut de l'instance (admin),
// reprise par quiconque n'a pas choisi les siens.
import { Group, TextInput } from '@mantine/core'
import SettingsSection from './SettingsSection'
import { notifications } from '@mantine/notifications'
import { useEffect, useState } from 'react'
import { api } from '../api'

export type Shortcuts = { full: string; two_thirds: string; one_third: string; zero: string }
export const DEFAULT_SHORTCUTS: Shortcuts = { full: 'f', two_thirds: 'd', one_third: 's', zero: 'q' }

const FIELDS: { key: keyof Shortcuts; label: string }[] = [
  { key: 'full', label: 'Tous les points' },
  { key: 'two_thirds', label: '2⁄3 des points' },
  { key: 'one_third', label: '1⁄3 des points' },
  { key: 'zero', label: '0 point' },
]

export default function ShortcutsSettings({ scope }: { scope: 'personal' | 'default' }) {
  const [values, setValues] = useState<Shortcuts | null>(null)

  useEffect(() => {
    const load = scope === 'personal'
      ? api.get<{ correction_shortcuts: Partial<Shortcuts> }>('/api/auth/me/preferences')
        .then((p) => p.correction_shortcuts)
      : api.get<Record<string, any>>('/api/settings/system').then((s) => s.correction_shortcuts ?? {})
    load.then((v) => setValues({ ...DEFAULT_SHORTCUTS, ...v })).catch(() => setValues(DEFAULT_SHORTCUTS))
  }, [scope])

  async function save(field: keyof Shortcuts, raw: string) {
    const key = (raw || '').trim().slice(0, 1).toLowerCase()
    if (!key || !values || values[field] === key) return
    const next = { ...values, [field]: key }
    try {
      if (scope === 'personal') await api.put('/api/auth/me/preferences/correction_shortcuts', { value: next })
      else await api.post('/api/settings/system', { key: 'correction_shortcuts', value: next })
      setValues(next)
      notifications.show({ color: 'green', message: 'Raccourci enregistré' })
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  return (
    <SettingsSection style={scope === 'personal' ? { maxWidth: 640 } : undefined}
      title={scope === 'personal' ? 'Mes raccourcis de correction' : 'Raccourcis de correction par défaut'}
      description={<>
        Une touche attribue une fraction des points de l'exercice dans la modale
        « Corriger manuellement » (les touches s'affichent sur les boutons). Une seule
        lettre par action.
        {scope === 'default' && ' Chaque utilisateur peut choisir les siens dans ses paramètres (roue crantée).'}
      </>}>
      {values && (
        <Group grow>
          {FIELDS.map((f) => (
            <TextInput key={`${f.key}-${values[f.key]}`} label={f.label} maxLength={1}
              defaultValue={values[f.key]} onBlur={(e) => save(f.key, e.currentTarget.value)} />
          ))}
        </Group>
      )}
    </SettingsSection>
  )
}
