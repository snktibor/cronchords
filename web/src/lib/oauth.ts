// Authorization code flow with PKCE, shared by Spotify and Google. Both
// providers redirect back to this page with ?code=...&state=...

export type Provider = 'spotify' | 'youtube'

export interface AuthorizationResult {
  provider: Provider
  code: string
  verifier: string
}

export interface TokenResponse {
  access_token: string
  expires_in: number
  refresh_token?: string
}

interface PendingAuthorization {
  provider: Provider
  state: string
  verifier: string
}

const PENDING_KEY = 'cronchords:pending-authorization'

export class TokenError extends Error {
  code: string

  constructor(code: string, description?: string) {
    super(description ? `${code}: ${description}` : code)
    this.code = code
  }
}

/** This page's address. It has to be registered as a redirect URI at Spotify and Google. */
export function redirectUri(): string {
  return window.location.origin + window.location.pathname.replace(/index\.html$/, '')
}

export async function authorize(
  provider: Provider,
  endpoint: string,
  params: Record<string, string>,
): Promise<void> {
  const verifier = randomString(64)
  const state = randomString(16)
  const pending: PendingAuthorization = { provider, state, verifier }
  sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending))

  const url = new URL(endpoint)
  url.search = new URLSearchParams({
    ...params,
    response_type: 'code',
    redirect_uri: redirectUri(),
    state,
    code_challenge_method: 'S256',
    code_challenge: await challengeFor(verifier),
  }).toString()
  window.location.assign(url)
}

/** Reads the provider's answer from the address bar, then removes it from there. */
export function takeAuthorizationResult(): AuthorizationResult | null {
  const params = new URLSearchParams(window.location.search)
  const state = params.get('state')
  if (!state) return null
  window.history.replaceState(null, '', redirectUri())

  const stored = sessionStorage.getItem(PENDING_KEY)
  sessionStorage.removeItem(PENDING_KEY)
  const pending = stored ? (JSON.parse(stored) as PendingAuthorization) : null
  if (!pending || pending.state !== state) {
    throw new Error('The authorization response does not match the request. Please try again.')
  }
  const code = params.get('code')
  if (!code) {
    throw new Error(`Authorization was not granted (${params.get('error') ?? 'unknown error'}).`)
  }
  return { provider: pending.provider, code, verifier: pending.verifier }
}

export async function requestToken(
  endpoint: string,
  body: Record<string, string>,
): Promise<TokenResponse> {
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(body),
  })
  const data = await response.json()
  if (!response.ok) throw new TokenError(data.error ?? `HTTP ${response.status}`, data.error_description)
  return data
}

// Access tokens live for an hour; keeping them for the browser session saves
// a refresh (and, for Spotify, a refresh token rotation) on every page load.

export function rememberAccessToken(provider: Provider, token: TokenResponse): void {
  const expiresAt = Date.now() + (token.expires_in - 60) * 1000
  sessionStorage.setItem(tokenKey(provider), JSON.stringify({ value: token.access_token, expiresAt }))
}

export function rememberedAccessToken(provider: Provider): string | null {
  const stored = sessionStorage.getItem(tokenKey(provider))
  if (!stored) return null
  const { value, expiresAt } = JSON.parse(stored) as { value: string; expiresAt: number }
  return expiresAt > Date.now() ? value : null
}

export function forgetAccessToken(provider: Provider): void {
  sessionStorage.removeItem(tokenKey(provider))
}

function tokenKey(provider: Provider): string {
  return `cronchords:access-token:${provider}`
}

function randomString(length: number): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
  const bytes = crypto.getRandomValues(new Uint8Array(length))
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join('')
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}
