import { toast } from './toast'

const API_URL = import.meta.env.VITE_API_URL || '/api'

/** Raw bytes of an API path (e.g. an encrypted chat attachment), with the bearer. */
export async function apiBytes(path) {
  const saved = localStorage.getItem('token')
  const response = await fetch(`${API_URL}${path}`, { headers: saved ? { Authorization: `Bearer ${saved}` } : {} })
  if (!response.ok) {
    let detail = 'Could not download.'
    try {
      const data = await response.json()
      if (typeof data.detail === 'string') detail = data.detail
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return response.arrayBuffer()
}

/** Authenticated file download (a link cannot carry the bearer token): saves the response as `filename`. */
export async function downloadFile(path, filename) {
  const saved = localStorage.getItem('token')
  const response = await fetch(`${API_URL}${path}`, { headers: saved ? { Authorization: `Bearer ${saved}` } : {} })
  if (!response.ok) {
    toast.error('Could not export the file.')
    return
  }
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
  toast.success('File exported')
}

/** Absolute URL of an API path (for links the browser opens itself, e.g. file downloads). */
export function apiUrl(path) {
  return `${API_URL}${path}`
}

export function money(cents) {
  return (Number(cents || 0) / 100).toLocaleString('pt-BR', {
    style: 'currency',
    currency: 'BRL',
  })
}

export function toCents(value) {
  const number = Number(String(value).replace(',', '.'))
  if (Number.isNaN(number)) return 0
  return Math.round(number * 100)
}

export function fromCents(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}

const DEFAULT_MESSAGE = { POST: 'Saved', PUT: 'Saved', PATCH: 'Saved', DELETE: 'Deleted' }

/** Fired after every successful mutation so widgets (e.g. the bell) can refresh. */
export const MUTATION_EVENT = 'sante:mutation'

/**
 * JSON request helper (a `FormData` body is sent as multipart, for uploads).
 *
 * Every result is reported with a toast: errors always (unless `silent`),
 * successes for mutating requests (`message` customizes the text; `false`
 * suppresses it). Reads are silent on success to avoid noise.
 */
export async function api(path, { method = 'GET', body, token, message, silent = false } = {}) {
  const multipart = typeof FormData !== 'undefined' && body instanceof FormData
  const headers = multipart ? {} : { 'Content-Type': 'application/json' }
  const saved = token || localStorage.getItem('token')
  if (saved) headers.Authorization = `Bearer ${saved}`
  let response
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : multipart ? body : JSON.stringify(body),
    })
  } catch (err) {
    if (!silent) toast.error('Could not reach the server. Check your connection.')
    throw err
  }
  const text = await response.text()
  const data = text ? JSON.parse(text) : null
  if (response.status === 401 && path !== '/auth/token') {
    // Signed out by the server (expired token, access revoked): back to the
    // login page, which shows why.
    localStorage.removeItem('token')
    if (data && typeof data.detail === 'string') sessionStorage.setItem('signedOutReason', data.detail)
    window.location.assign('/login')
  }
  if (!response.ok) {
    const detail = data && data.detail
    const text = typeof detail === 'string'
      ? detail
      : Array.isArray(detail) && detail.length
        ? detail.map((item) => item.msg || JSON.stringify(item)).join('; ')
        : 'Could not complete the request.'
    if (!silent) toast.error(text)
    throw new Error(text)
  }
  if (method !== 'GET') {
    if (!silent && message !== false) toast.success(message || DEFAULT_MESSAGE[method] || 'Done')
    window.dispatchEvent(new Event(MUTATION_EVENT))
  }
  return data
}
