import { QueryClient } from '@tanstack/vue-query'
import { readonly, ref } from 'vue'

export const queryClient = new QueryClient()

const version = ref(0)
let controller = new AbortController()

export const sessionVersion = readonly(version)

export function sessionSignal(): AbortSignal {
  return controller.signal
}

export function resetSessionScope() {
  const previousController = controller
  controller = new AbortController()
  version.value += 1
  previousController.abort()
  queryClient.clear()
}
