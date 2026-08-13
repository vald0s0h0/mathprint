import { invoke } from '@tauri-apps/api/core'
import { disable as disableAutostart, enable as enableAutostart } from '@tauri-apps/plugin-autostart'
import { relaunch } from '@tauri-apps/plugin-process'
import { check } from '@tauri-apps/plugin-updater'
import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useState } from 'react'

type Job = {
  id: string
  title: string
  printer: string
  pass_side: string
  status: string
  error?: string | null
}

type ConnectorState = {
  connected: boolean
  email: string
  server_url: string
  device_name: string
  status: string
  printer_count: number
  current_job?: string | null
  jobs: Job[]
  pronote_shortcut: string
  pronote_shortcut_active: boolean
  pronote_running: boolean
  pronote_status: string
  pronote_last_count?: number | null
}

const empty: ConnectorState = {
  connected: false, email: '', server_url: '', device_name: '',
  status: 'Déconnecté', printer_count: 0, jobs: [],
  pronote_shortcut: 'CmdOrCtrl+Alt+Shift+N', pronote_shortcut_active: false,
  pronote_running: false, pronote_status: '',
}

const isMac = navigator.userAgent.includes('Mac')

function shortcutLabel(shortcut: string) {
  const parts = shortcut.split('+')
  if (isMac) {
    const labels: Record<string, string> = {
      CmdOrCtrl: '⌘', Alt: '⌥', Shift: '⇧',
    }
    return parts.map((part) => labels[part] || part).join(' ')
  }
  const labels: Record<string, string> = {
    CmdOrCtrl: 'Ctrl', Alt: 'Alt', Shift: 'Maj',
  }
  return parts.map((part) => labels[part] || part).join(' + ')
}

function shortcutKey(event: ReactKeyboardEvent<HTMLButtonElement>) {
  const primaryPressed = isMac ? event.metaKey : event.ctrlKey
  if (!primaryPressed || !event.altKey || !event.shiftKey) return null
  if (/^Key[A-Z]$/.test(event.code)) return event.code.slice(3)
  if (/^Digit[0-9]$/.test(event.code)) return event.code.slice(5)
  if (/^F(?:[1-9]|1[0-2])$/.test(event.code)) return event.code
  return null
}

function jobLabel(status: string) {
  const labels: Record<string, string> = {
    queued: 'En attente', claimed: 'Préparation', submitted: 'File système',
    failed: 'Échec', uncertain: 'À vérifier', cancelled: 'Annulé',
  }
  return labels[status] || status
}

export default function App() {
  const [state, setState] = useState<ConnectorState>(empty)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [updating, setUpdating] = useState(false)
  const [capturingShortcut, setCapturingShortcut] = useState(false)
  const [shortcutBusy, setShortcutBusy] = useState(false)
  const [shortcutError, setShortcutError] = useState('')

  async function refresh() {
    try {
      const next = await invoke<ConnectorState>('connector_state')
      setState(next)
      if (!email && next.email) setEmail(next.email)
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => {
    refresh()
    let cancelled = false
    const updateTimer = window.setTimeout(async () => {
      let workerPaused = false
      try {
        const available = await check({ timeout: 15_000 })
        if (!available || cancelled) return
        setUpdating(true)

        // Une mise à jour ne doit jamais interrompre un document déjà confié
        // au spouleur. On attend silencieusement que le travail courant finisse.
        while (!cancelled) {
          const snapshot = await invoke<ConnectorState>('connector_state')
          if (!snapshot.current_job && !snapshot.pronote_running) break
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
        }
        if (cancelled) return

        await invoke('pause_worker', { paused: true })
        workerPaused = true
        await available.downloadAndInstall()
        await relaunch()
      } catch (e) {
        // La connexion et l'impression restent disponibles. Le connecteur
        // réessaiera automatiquement au prochain lancement.
        console.warn('Mise à jour automatique différée', e)
        if (workerPaused) {
          try { await invoke('pause_worker', { paused: false }) } catch { /* relance suivante */ }
        }
        setUpdating(false)
      }
    }, 1500)
    const timer = window.setInterval(refresh, 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.clearTimeout(updateTimer)
    }
  }, [])

  async function login(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      const next = await invoke<ConnectorState>('login', {
        email, password,
      })
      setPassword('')
      setState(next)
      try { await enableAutostart() } catch { /* reste utilisable sans autostart */ }
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function logout() {
    setBusy(true); setError('')
    try {
      setState(await invoke<ConnectorState>('logout'))
      setPassword('')
      try { await disableAutostart() } catch { /* la déconnexion serveur reste acquise */ }
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function captureShortcut(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (!capturingShortcut || event.repeat) return
    event.preventDefault()
    event.stopPropagation()
    if (event.key === 'Escape') {
      setCapturingShortcut(false)
      setShortcutError('')
      return
    }
    const key = shortcutKey(event)
    if (!key) {
      setShortcutError(`Maintenez ${isMac ? '⌘ + ⌥ + ⇧' : 'Ctrl + Alt + Maj'} puis appuyez sur une lettre, un chiffre ou F1–F12.`)
      return
    }
    const shortcut = `CmdOrCtrl+Alt+Shift+${key}`
    setShortcutBusy(true)
    setShortcutError('')
    try {
      const next = await invoke<ConnectorState>('set_pronote_shortcut', { shortcut })
      setState(next)
      setCapturingShortcut(false)
    } catch (e) {
      setShortcutError(String(e))
    } finally {
      setShortcutBusy(false)
    }
  }

  return (
    <main>
      <header>
        <div className="mark">M</div>
        <div><h1>MathPrint Connector</h1><p>Impression locale sécurisée</p></div>
      </header>

      {updating && <div className="update">
        <span>Mise à jour automatique en cours…</span>
      </div>}

      {!state.connected ? (
        <form onSubmit={login}>
          <label>Adresse e-mail
            <input type="email" required autoComplete="username"
              value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>Mot de passe
            <input type="password" required autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <div className="error">{error}</div>}
          <button disabled={busy}>{busy ? 'Connexion…' : 'Connecter ce poste'}</button>
          <p className="hint">Le mot de passe sert uniquement à cette connexion et n’est jamais conservé.</p>
        </form>
      ) : (
        <section>
          <div className="connected">
            <span className="dot" /><div><strong>{state.status}</strong><small>{state.email}</small></div>
          </div>
          <dl>
            <div><dt>Poste</dt><dd>{state.device_name}</dd></div>
            <div><dt>Imprimantes détectées</dt><dd>{state.printer_count}</dd></div>
          </dl>
          {state.current_job && <div className="current">Impression : {state.current_job}</div>}
          {state.jobs.length > 0 && <div className="jobs">
            <h2>Travaux récents</h2>
            {state.jobs.slice(0, 6).map((job) => <article key={job.id}>
              <div><strong>{job.title}</strong><small>{job.printer}</small></div>
              <span className={`status ${job.status}`}>{jobLabel(job.status)}</span>
            </article>)}
          </div>}
          {error && <div className="error">{error}</div>}
          <button className="secondary" onClick={logout} disabled={busy}>Déconnecter ce poste</button>
          <p className="hint">Les alertes papier, bourrage et consommables restent affichées par la file d’impression du système.</p>
        </section>
      )}

      <section className="pronote-card">
        <div className="pronote-title">
          <div><h2>Saisie ProNote</h2><p>Collez une colonne complète sans quitter ProNote.</p></div>
          <span className={`shortcut-state ${state.pronote_shortcut_active ? 'active' : ''}`}>
            {state.pronote_shortcut_active ? 'Actif' : 'À configurer'}
          </span>
        </div>
        <ol>
          <li>Dans MathPrint, copiez la colonne depuis l’onglet Notes.</li>
          <li>Dans ProNote, sélectionnez la première cellule de la colonne.</li>
          <li>Lancez le raccourci et ne touchez plus au clavier pendant la saisie.</li>
        </ol>
        <div className="shortcut-row">
          <span>Raccourci</span>
          <kbd>{shortcutLabel(state.pronote_shortcut)}</kbd>
        </div>
        <button
          type="button"
          className={`shortcut-button ${capturingShortcut ? 'capturing' : ''}`}
          disabled={shortcutBusy || state.pronote_running}
          onClick={() => { setCapturingShortcut((value) => !value); setShortcutError('') }}
          onKeyDown={captureShortcut}
        >
          {shortcutBusy ? 'Activation…' : capturingShortcut
            ? `Tapez ${isMac ? '⌘ + ⌥ + ⇧ + une touche' : 'Ctrl + Alt + Maj + une touche'}`
            : 'Modifier le raccourci'}
        </button>
        {shortcutError && <div className="error">{shortcutError}</div>}
        {state.pronote_status && <p className={`pronote-status ${state.pronote_running ? 'running' : ''}`} aria-live="polite">
          {state.pronote_status}
        </p>}
        <p className="hint">
          Les lignes vides font avancer d’une cellule sans décaler la classe. Les absences sont saisies exactement comme « Abs ».
          {isMac && ' macOS demandera l’autorisation Accessibilité lors de la première utilisation.'}
        </p>
      </section>
    </main>
  )
}
