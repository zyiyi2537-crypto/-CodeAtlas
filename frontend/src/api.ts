import axios from 'axios'
import { useAuth } from '@/auth'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/code-kb',
  timeout: 20_000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const requestUrl: string = error.config?.url ?? ''
    const isSessionProbe = requestUrl.includes('/auth/me')
    if (axios.isAxiosError(error) && error.response?.status === 401 && !isSessionProbe) {
      const { state } = useAuth()
      state.user = null
      state.csrfToken = ''
      const { router } = await import('@/router')
      if (router.currentRoute.value.name !== 'login') {
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
