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
    let msg = res.statusText
    try {
      const data = await res.json()
      msg = data.detail || JSON.stringify(data)
    } catch { /* garder statusText */ }
    throw new ApiError(res.status, msg)
  }
  return res.json()
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body),
  patch: <T>(url: string, body?: unknown) => request<T>('PATCH', url, body),
  del: <T>(url: string) => request<T>('DELETE', url),
}
