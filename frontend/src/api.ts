import axios, { type InternalAxiosRequestConfig } from 'axios'
import { clearSession } from '@/auth'
import { sessionSignal, sessionVersion } from '@/sessionScope'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/code-kb',
  timeout: 20_000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

const requests = new WeakMap<InternalAxiosRequestConfig, { version: number; dispose: () => void }>()

api.interceptors.request.use((config) => {
  const controller = new AbortController()
  const scopeSignal = sessionSignal()
  const callerSignal = config.signal
  const abort = () => controller.abort()
  scopeSignal.addEventListener('abort', abort, { once: true })
  callerSignal?.addEventListener?.('abort', abort, { once: true })
  if (scopeSignal.aborted || callerSignal?.aborted) abort()
  requests.set(config, {
    version: sessionVersion.value,
    dispose: () => {
      scopeSignal.removeEventListener('abort', abort)
      callerSignal?.removeEventListener?.('abort', abort)
    },
  })
  config.signal = controller.signal
  return config
})

function finishRequest(config?: InternalAxiosRequestConfig): boolean {
  if (!config) return false
  const request = requests.get(config)
  request?.dispose()
  requests.delete(config)
  return request !== undefined && request.version !== sessionVersion.value
}

api.interceptors.response.use(
  (response) => {
    if (finishRequest(response.config)) {
      throw new axios.CanceledError('Session changed', response.config)
    }
    return response
  },
  async (error) => {
    // A response from a previous identity must never log out the current user.
    if (finishRequest(error.config)) {
      throw new axios.CanceledError('Session changed', error.config)
    }
    const requestUrl: string = error.config?.url ?? ''
    const isSessionProbe = requestUrl.includes('/auth/me')
    const isLoginAttempt = requestUrl.includes('/auth/login')
    if (axios.isAxiosError(error) && error.response?.status === 401 && !isSessionProbe && !isLoginAttempt) {
      clearSession()
      const version = sessionVersion.value
      const { router } = await import('@/router')
      if (version === sessionVersion.value && router.currentRoute.value.name !== 'login') {
        await router.push({
          name: 'login',
          query: { redirect: router.currentRoute.value.fullPath },
        })
      }
    }
    return Promise.reject(error)
  },
)

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
  }
  return error instanceof Error ? error.message : '请求失败'
}
