import type { Directive, DirectiveBinding } from 'vue'

interface InertSnapshot {
  inert: boolean
  ariaHidden: string | null
}

interface ModalState {
  close: () => void
  dialog: HTMLElement
  root: HTMLElement
  previousActive: HTMLElement | null
  addedTabindex: boolean
}

const states = new WeakMap<HTMLElement, ModalState>()
const stack: ModalState[] = []
const managedBackground = new Map<HTMLElement, InertSnapshot>()
let previousBodyOverflow = ''
let sessionPreviousActive: HTMLElement | null = null
let sessionRoot: HTMLElement | null = null
let listening = false
let finalizeVersion = 0
let finalizePending = false

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

function modalRoot(dialog: HTMLElement) {
  return dialog.closest<HTMLElement>('[data-v-app]')
    ?? dialog.closest<HTMLElement>('#app')
    ?? dialog.parentElement
    ?? document.body
}

function focusableElements(dialog: HTMLElement) {
  return Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true',
  )
}

function focusInitial(dialog: HTMLElement) {
  const initial = dialog.querySelector<HTMLElement>(
    '[data-modal-initial-focus], button[aria-label^="关闭"], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]',
  )
  ;(initial ?? dialog).focus()
}

function restoreManagedBackground() {
  for (const [element, snapshot] of managedBackground) {
    element.inert = snapshot.inert
    if (snapshot.ariaHidden === null) element.removeAttribute('aria-hidden')
    else element.setAttribute('aria-hidden', snapshot.ariaHidden)
  }
}

function manageInert(element: HTMLElement) {
  if (!managedBackground.has(element)) {
    managedBackground.set(element, {
      inert: Boolean(element.inert),
      ariaHidden: element.getAttribute('aria-hidden'),
    })
  }
  element.inert = true
  element.setAttribute('aria-hidden', 'true')
}

function recomputeBackground() {
  restoreManagedBackground()
  const top = stack.at(-1)
  if (!top) return

  const overlay = top.dialog.closest<HTMLElement>('.preview-backdrop') ?? top.dialog
  let current: HTMLElement | null = overlay
  while (current && current !== document.body) {
    const parent: HTMLElement | null = current.parentElement
    if (!parent) break
    for (const sibling of Array.from(parent.children)) {
      if (sibling instanceof HTMLElement && sibling !== current) manageInert(sibling)
    }
    current = parent
  }
}

function stopSession(restoreFocus: boolean) {
  finalizePending = false
  finalizeVersion += 1
  restoreManagedBackground()
  managedBackground.clear()
  document.body.style.overflow = previousBodyOverflow
  if (listening) {
    window.removeEventListener('keydown', handleKeydown)
    listening = false
  }
  if (restoreFocus && sessionPreviousActive?.isConnected) sessionPreviousActive.focus()
  sessionPreviousActive = null
  sessionRoot = null
}

function handleKeydown(event: KeyboardEvent) {
  const state = stack.at(-1)
  if (!state) return
  const { dialog } = state

  if (event.key === 'Escape') {
    event.preventDefault()
    state.close()
    return
  }
  if (event.key !== 'Tab') return

  const focusable = focusableElements(dialog)
  if (!focusable.length) {
    event.preventDefault()
    dialog.focus()
    return
  }
  const first = focusable[0]
  const last = focusable.at(-1)
  if (!first || !last) return

  if (!dialog.contains(document.activeElement)) {
    event.preventDefault()
    first.focus()
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function mountModal(dialog: HTMLElement, binding: DirectiveBinding<() => void>) {
  if (typeof binding.value !== 'function') {
    throw new TypeError('v-modal-dialog requires a close callback')
  }

  const root = modalRoot(dialog)
  if (!stack.length && finalizePending && sessionRoot !== root) stopSession(false)

  const addedTabindex = !dialog.hasAttribute('tabindex')
  if (addedTabindex) dialog.setAttribute('tabindex', '-1')
  const state: ModalState = {
    close: binding.value,
    dialog,
    root,
    previousActive: document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
    addedTabindex,
  }
  states.set(dialog, state)

  if (!stack.length) {
    if (finalizePending) {
      finalizePending = false
      finalizeVersion += 1
    } else {
      sessionRoot = root
      sessionPreviousActive = state.previousActive
      previousBodyOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
    }
  }
  stack.push(state)
  recomputeBackground()
  if (!listening) {
    window.addEventListener('keydown', handleKeydown)
    listening = true
  }

  queueMicrotask(() => {
    if (states.has(dialog) && stack.at(-1) === state) focusInitial(dialog)
  })
}

function updateModal(dialog: HTMLElement, binding: DirectiveBinding<() => void>) {
  const state = states.get(dialog)
  if (state && typeof binding.value === 'function') state.close = binding.value
}

function unmountModal(dialog: HTMLElement) {
  const state = states.get(dialog)
  if (!state) return
  states.delete(dialog)
  const wasTop = stack.at(-1) === state
  const index = stack.indexOf(state)
  if (index >= 0) stack.splice(index, 1)
  if (state.addedTabindex) dialog.removeAttribute('tabindex')
  recomputeBackground()

  const top = stack.at(-1)
  if (top) {
    if (wasTop) {
      queueMicrotask(() => {
        if (stack.at(-1) !== top) return
        if (state.previousActive?.isConnected && top.dialog.contains(state.previousActive)) {
          state.previousActive.focus()
        } else {
          focusInitial(top.dialog)
        }
      })
    }
    return
  }

  finalizePending = true
  const version = ++finalizeVersion
  queueMicrotask(() => {
    if (finalizePending && finalizeVersion === version && !stack.length) stopSession(true)
  })
}

export const modalDialog: Directive<HTMLElement, () => void> = {
  mounted: mountModal,
  updated: updateModal,
  unmounted: unmountModal,
}
