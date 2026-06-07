/** Sessions + GitHub OAuth — Web Crypto port of src/pandm/server/auth.py.
 * Token format matches the Python server: base64url(json).base64url(hmac). */

export const SESSION_COOKIE = 'pandm_session'
export const STATE_COOKIE = 'pandm_oauth_state'
export const SESSION_TTL = 30 * 24 * 3600

const enc = new TextEncoder()

const b64url = (bytes: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_')

function b64urlDecode(s: string): Uint8Array {
  const raw = atob(s.replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, [
    'sign',
    'verify',
  ])
}

export async function sign(secret: string, payload: Record<string, unknown>): Promise<string> {
  const body = b64url(enc.encode(JSON.stringify(payload)))
  const sig = await crypto.subtle.sign('HMAC', await hmacKey(secret), enc.encode(body))
  return `${body}.${b64url(sig)}`
}

export async function verify(secret: string, token: string | undefined): Promise<Record<string, any> | null> {
  if (!token || !token.includes('.')) return null
  const [body, sig] = token.split('.', 2)
  try {
    const ok = await crypto.subtle.verify('HMAC', await hmacKey(secret), b64urlDecode(sig), enc.encode(body))
    if (!ok) return null
    const payload = JSON.parse(new TextDecoder().decode(b64urlDecode(body)))
    if ((payload.exp ?? 0) < Date.now() / 1000) return null
    return payload
  } catch {
    return null
  }
}

export function sessionCookie(token: string, secure: boolean, maxAge = SESSION_TTL): string {
  return `${SESSION_COOKIE}=${token}; Max-Age=${maxAge}; Path=/; HttpOnly; SameSite=Lax${secure ? '; Secure' : ''}`
}

export function readCookie(header: string | undefined, name: string): string | undefined {
  if (!header) return undefined
  for (const part of header.split(/;\s*/)) {
    const eq = part.indexOf('=')
    if (eq > 0 && part.slice(0, eq) === name) return part.slice(eq + 1)
  }
  return undefined
}

// ------------------------------------------------------------ GitHub OAuth

export const GITHUB_AUTHORIZE = 'https://github.com/login/oauth/authorize'
export const GITHUB_TOKEN = 'https://github.com/login/oauth/access_token'
export const GITHUB_USER_API = 'https://api.github.com/user'

export interface GithubProfile {
  id: number
  login: string
  name: string | null
  avatar_url: string | null
}

export async function exchangeGithubCode(clientId: string, clientSecret: string, code: string): Promise<GithubProfile | null> {
  const tokenResp = await fetch(GITHUB_TOKEN, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ client_id: clientId, client_secret: clientSecret, code }),
  })
  if (!tokenResp.ok) return null
  const accessToken = ((await tokenResp.json()) as { access_token?: string }).access_token
  if (!accessToken) return null
  const profileResp = await fetch(GITHUB_USER_API, {
    headers: { Authorization: `Bearer ${accessToken}`, 'User-Agent': 'pandm', Accept: 'application/json' },
  })
  if (!profileResp.ok) return null
  return (await profileResp.json()) as GithubProfile
}
