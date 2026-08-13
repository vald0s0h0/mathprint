import { invoke } from '@tauri-apps/api/core'
import { disable as disableAutostart, enable as enableAutostart } from '@tauri-apps/plugin-autostart'
import { relaunch } from '@tauri-apps/plugin-process'
import { check } from '@tauri-apps/plugin-updater'
import { FormEvent, useEffect, useState } from 'react'

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
}

const empty: ConnectorState = {
  connected: false, email: '', server_url: '', device_name: '',
  status: 'Déconnecté', printer_count: 0, jobs: [],
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
          if (!snapshot.current_job) break
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
    </main>
  )
}
