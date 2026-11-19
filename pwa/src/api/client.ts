export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

interface RequestOptions {
  method?: HttpMethod
  body?: unknown
  token?: string | null
  accept?: string
  auth?: boolean
  signal?: AbortSignal
}

type SessionExpiredHandler = () => void

let onSessionExpired: SessionExpiredHandler | null = null

export function setSessionExpiredHandler(handler: SessionExpiredHandler | null) {
  onSessionExpired = handler
}

function getStoredAuthHeader(): string | null {
  try {
    const raw = localStorage.getItem('hollic_pwa_session')
    if (!raw) return null
    const session = JSON.parse(raw) as { token_type?: string; token?: string }
    if (!session.token) return null
    return `${session.token_type || 'Bearer'} ${session.token}`
  } catch {
    return null
  }
}

function extractMessage(body: unknown, fallback: string): string {
  if (!body) return fallback
  if (typeof body === 'string') return body
  if (typeof body === 'object') {
    const obj = body as Record<string, unknown>
    if (typeof obj.message === 'string') return obj.message
    if (typeof obj.detail === 'string') return obj.detail
    if (Array.isArray(obj.detail) && obj.detail[0] && typeof obj.detail[0] === 'object') {
      const first = obj.detail[0] as { msg?: string }
      if (first.msg) return first.msg
    }
    if (obj.errors && typeof obj.errors === 'object') {
      const first = Object.values(obj.errors as Record<string, string[]>)[0]
      if (Array.isArray(first) && first[0]) return String(first[0])
    }
    if (typeof obj.email === 'string') return obj.email
  }
  return fallback
}

export async function apiRequest<T = unknown>(
  url: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = 'GET',
    body,
    token,
    accept = 'application/json',
    auth = true,
    signal,
  } = options

  const headers: Record<string, string> = {
    Accept: accept,
  }

  if (body !== undefined && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }

  if (auth) {
    const authHeader = token ?? getStoredAuthHeader()
    if (authHeader) headers.Authorization = authHeader
  }

  const response = await fetch(url, {
    method,
    headers,
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
    signal,
  })

  if (response.status === 401 && auth) {
    onSessionExpired?.()
    throw new ApiError(401, 'Your session has expired. Please sign in again.')
  }

  if (response.status === 204) {
    return undefined as T
  }

  const contentType = response.headers.get('content-type') || ''
  const rawText = await response.text()
  const payload = parseResponseBody(rawText, contentType)

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractMessage(payload, `Erro ${response.status}`),
      payload,
    )
  }

  return payload as T
}

/** PHP may prepend deprecation HTML before JSON; extract the JSON payload safely. */
function parseResponseBody(rawText: string, contentType: string): unknown {
  const trimmed = rawText.trim()
  if (!trimmed) return null

  const looksJson =
    contentType.includes('application/json') ||
    trimmed.startsWith('{') ||
    trimmed.startsWith('[') ||
    trimmed.includes('{')

  if (!looksJson) return rawText

  try {
    return JSON.parse(trimmed)
  } catch {
    const start = trimmed.indexOf('{')
    const startArr = trimmed.indexOf('[')
    let idx = -1
    if (start >= 0 && startArr >= 0) idx = Math.min(start, startArr)
    else idx = Math.max(start, startArr)

    if (idx >= 0) {
      const candidate = trimmed.slice(idx)
      try {
        return JSON.parse(candidate)
      } catch {
        // fall through
      }
    }
  }

  return rawText
}

export const api = {
  get: <T>(url: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(url, { ...options, method: 'GET' }),
  post: <T>(url: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(url, { ...options, method: 'POST', body }),
  put: <T>(url: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(url, { ...options, method: 'PUT', body }),
  patch: <T>(url: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(url, { ...options, method: 'PATCH', body }),
  delete: <T>(url: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(url, { ...options, method: 'DELETE' }),
}
