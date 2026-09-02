// Client API : le navigateur n'a aucune logique de notation, l'API est l'autorité (§9.1).

export function getToken(): string | null {
  return localStorage.getItem('mathprint_token')
}

export function setToken(t: string | null) {
  if (t) localStorage.setItem('mathprint_token', t)
  else localStorage.removeItem('mathprint_token')
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  let payload: BodyInit | undefined
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const res = await fetch(url, { method, headers, body: payload })
  if (res.status === 401) {
    setToken(null)
    window.location.href = '/login'
    throw new ApiError(401, 'Session expirée')
  }
  if (!res.ok) {
    let msg = ''
    try {
      const data = await res.json()
      const detail = data?.detail
      if (typeof detail === 'string') msg = detail
      // 422 FastAPI : detail est une LISTE d'objets {loc, msg, type}
      else if (Array.isArray(detail)) msg = detail.map((d) => d?.msg || JSON.stringify(d)).join(' ; ')
      else if (detail != null) msg = JSON.stringify(detail)
      else if (data != null && Object.keys(data).length) msg = JSON.stringify(data)
    } catch { /* corps non-JSON : proxy, page HTML, ou vide */ }
    // res.statusText est VIDE en HTTP/2 : sans repli, la notif s'affichait sans
    // texte (« cadre vide »). On garantit toujours un message lisible.
    if (!msg) msg = res.statusText || `Erreur ${res.status}`
    throw new ApiError(res.status, msg)
  }
  return res.json()
}

// Téléchargement d'un fichier servi par une route PROTÉGÉE. Un simple
// <a href> ou window.open ne porte pas l'en-tête Authorization et se prend un
// 401 (même raison que components/AuthImg) : on récupère les octets en fetch
// authentifié, puis on déclenche l'enregistrement via une URL d'objet.
export async function download(url: string, fallbackName: string): Promise<void> {
  const token = getToken()
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    let msg = ''
    try { msg = (await res.json())?.detail || '' } catch { /* corps non-JSON */ }
    throw new ApiError(res.status, msg || res.statusText || `Erreur ${res.status}`)
  }
  // le serveur nomme le fichier (Content-Disposition) ; on ne devine qu'à défaut
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = /filename="?([^";]+)"?/.exec(disposition)
  const blobUrl = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = blobUrl
  a.download = match?.[1] || fallbackName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(blobUrl)
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body),
  put: <T>(url: string, body?: unknown) => request<T>('PUT', url, body),
  patch: <T>(url: string, body?: unknown) => request<T>('PATCH', url, body),
  del: <T>(url: string) => request<T>('DELETE', url),
}
