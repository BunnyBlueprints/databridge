/**
 * Centralized frontend environment configuration.
 * Reads Vite build-time environment variables with safe fallbacks.
 */

function normalizeBaseUrl(rawUrl: unknown): string {
  if (typeof rawUrl !== 'string') return ''
  return rawUrl.trim().replace(/\/+$/, '')
}

export const env = {
  apiBaseUrl: normalizeBaseUrl(import.meta.env?.VITE_API_BASE_URL),
} as const

/**
 * Resolves an API path against the configured API base URL.
 * Falls back to a relative same-origin path when VITE_API_BASE_URL is unset.
 */
export function buildApiUrl(path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return env.apiBaseUrl ? `${env.apiBaseUrl}${normalizedPath}` : normalizedPath
}
