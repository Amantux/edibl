const TOKEN_KEY = 'edibl_token'

function apiBase() {
  let p = window.location.pathname
  if (!p.endsWith('/')) p = p.slice(0, p.lastIndexOf('/') + 1)
  return p + 'api/v1'
}
export function apiUrl(path) { return apiBase() + path }
export function getToken() { return localStorage.getItem(TOKEN_KEY) }
export function setToken(t) { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) }

async function request(method, path, body) {
  const headers = {}
  const token = getToken()
  if (token) headers['Authorization'] = token
  let payload
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  const res = await fetch(apiUrl(path), { method, headers, body: payload })
  if (res.status === 401) { setToken(null); if (!location.hash.includes('/login')) location.hash = '#/login' }
  const ct = res.headers.get('content-type') || ''
  const data = ct.includes('application/json') ? await res.json() : await res.text()
  if (!res.ok) throw new Error((data && data.error) || res.statusText)
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b),
  put: (p, b) => request('PUT', p, b),
  del: (p) => request('DELETE', p),
}
