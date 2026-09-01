// @vitest-environment jsdom

import { nextTick, ref } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import { modalDialog } from '@/modalDialog'

const wrappers: VueWrapper[] = []

function trackedMount(component: Parameters<typeof mount>[0]) {
  const wrapper = mount(component, { attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}

const Harness = {
  directives: { modalDialog },
  setup() {
    const open = ref(false)
    return { open, close: () => { open.value = false } }
  },
  template: `
    <div>
      <button data-testid="trigger" type="button" @click="open = true">打开</button>
      <div data-testid="background"><button type="button">背景按钮</button></div>
      <div v-if="open" class="preview-backdrop">
        <section v-modal-dialog="close" role="dialog" aria-modal="true" aria-label="测试弹窗">
          <button data-testid="close" type="button" aria-label="关闭" @click="close">关闭</button>
          <input data-testid="field" />
          <button data-testid="last" type="button">保存</button>
        </section>
      </div>
    </div>
  `,
}

const StackedHarness = {
  directives: { modalDialog },
  setup() {
    const first = ref(false)
    const second = ref(false)
    return {
      first,
      second,
      closeFirst: () => { first.value = false },
      closeSecond: () => { second.value = false },
    }
  },
  template: `
    <div>
      <button data-testid="stack-trigger" type="button" @click="first = true">打开第一层</button>
      <div v-if="first" data-testid="first-backdrop" class="preview-backdrop">
        <section v-modal-dialog="closeFirst" role="dialog" aria-modal="true" aria-label="第一层">
          <button data-testid="open-second" type="button" @click="second = true">打开第二层</button>
        </section>
      </div>
      <div v-if="second" data-testid="second-backdrop" class="preview-backdrop">
        <section v-modal-dialog="closeSecond" role="dialog" aria-modal="true" aria-label="第二层">
          <button type="button" aria-label="关闭第二层" @click="closeSecond">关闭第二层</button>
        </section>
      </div>
    </div>
  `,
}

const ReplacementHarness = {
  directives: { modalDialog },
  setup() {
    const first = ref(false)
    const second = ref(false)
    return {
      first,
      second,
      replace: () => {
        first.value = false
        second.value = true
      },
      closeFirst: () => { first.value = false },
      closeSecond: () => { second.value = false },
    }
  },
  template: `
    <div>
      <button data-testid="replace-trigger" type="button" @click="first = true">打开创建弹窗</button>
      <div v-if="first" class="preview-backdrop">
        <section v-modal-dialog="closeFirst" role="dialog" aria-modal="true" aria-label="创建">
          <button data-testid="replace-dialog" type="button" @click="replace">创建并显示密钥</button>
        </section>
      </div>
      <div v-if="second" class="preview-backdrop">
        <section v-modal-dialog="closeSecond" role="dialog" aria-modal="true" aria-label="密钥">
          <button type="button" aria-label="关闭密钥" @click="closeSecond">关闭</button>
        </section>
      </div>
    </div>
  `,
}

const NestedHarness = {
  directives: { modalDialog },
  setup() {
    const first = ref(false)
    const second = ref(false)
    return {
      first,
      second,
      closeFirst: () => { first.value = false },
      closeSecond: () => { second.value = false },
    }
  },
  template: `
    <div>
      <button data-testid="nested-trigger" type="button" @click="first = true">打开外层</button>
      <div v-if="first" data-testid="outer-backdrop" class="preview-backdrop">
        <section v-modal-dialog="closeFirst" role="dialog" aria-modal="true" aria-label="外层">
          <button data-testid="open-nested" type="button" @click="second = true">打开内层</button>
          <div v-if="second" data-testid="inner-backdrop" class="preview-backdrop">
            <section v-modal-dialog="closeSecond" role="dialog" aria-modal="true" aria-label="内层">
              <button type="button" aria-label="关闭内层" @click="closeSecond">关闭内层</button>
            </section>
          </div>
        </section>
      </div>
    </div>
  `,
}

const TripleHarness = {
  directives: { modalDialog },
  setup() {
    const first = ref(false)
    const second = ref(false)
    const third = ref(false)
    return {
      first,
      second,
      third,
      closeFirst: () => { first.value = false },
      closeSecond: () => { second.value = false },
      closeThird: () => { third.value = false },
    }
  },
  template: `
    <div>
      <button data-testid="triple-trigger" type="button" @click="first = true">打开第一层</button>
      <div v-if="first" data-testid="triple-first" class="preview-backdrop">
        <section v-modal-dialog="closeFirst" role="dialog" aria-modal="true" aria-label="第一层">
          <button data-testid="triple-open-second" type="button" @click="second = true">打开第二层</button>
        </section>
      </div>
      <div v-if="second" data-testid="triple-second" class="preview-backdrop">
        <section v-modal-dialog="closeSecond" role="dialog" aria-modal="true" aria-label="第二层">
          <button data-testid="triple-open-third" type="button" @click="third = true">打开第三层</button>
        </section>
      </div>
      <div v-if="third" data-testid="triple-third" class="preview-backdrop">
        <section v-modal-dialog="closeThird" role="dialog" aria-modal="true" aria-label="第三层">
          <button type="button" aria-label="关闭第三层" @click="closeThird">关闭第三层</button>
        </section>
      </div>
    </div>
  `,
}

const DisconnectedFocusHarness = {
  directives: { modalDialog },
  setup() {
    const first = ref(false)
    const second = ref(false)
    const showTrigger = ref(true)
    return {
      first,
      second,
      showTrigger,
      closeFirst: () => { first.value = false },
      closeSecond: () => { second.value = false },
    }
  },
  template: `
    <div>
      <button data-testid="disconnect-trigger" type="button" @click="first = true">打开第一层</button>
      <div v-if="first" class="preview-backdrop">
        <section v-modal-dialog="closeFirst" role="dialog" aria-modal="true" aria-label="第一层">
          <button data-testid="lower-fallback" type="button">下层回退</button>
          <button v-if="showTrigger" data-testid="disconnect-open-second" type="button" @click="second = true">打开第二层</button>
        </section>
      </div>
      <div v-if="second" class="preview-backdrop">
        <section v-modal-dialog="closeSecond" role="dialog" aria-modal="true" aria-label="第二层">
          <button type="button" aria-label="关闭第二层" @click="closeSecond">关闭第二层</button>
        </section>
      </div>
    </div>
  `,
}

const CallbackHarness = {
  directives: { modalDialog },
  setup() {
    const open = ref(false)
    const useSecond = ref(false)
    const closedBy = ref('')
    const closeFirst = () => { closedBy.value = 'first'; open.value = false }
    const closeSecond = () => { closedBy.value = 'second'; open.value = false }
    return { open, useSecond, closedBy, closeFirst, closeSecond }
  },
  template: `
    <div>
      <button data-testid="callback-trigger" type="button" @click="open = true">打开</button>
      <div v-if="open" class="preview-backdrop">
        <section v-modal-dialog="useSecond ? closeSecond : closeFirst" role="dialog" aria-modal="true" aria-label="回调更新">
          <button type="button" aria-label="关闭回调弹窗">关闭</button>
        </section>
      </div>
    </div>
  `,
}

afterEach(async () => {
  while (wrappers.length) wrappers.pop()?.unmount()
  await Promise.resolve()
  document.body.innerHTML = ''
  document.body.style.overflow = ''
})

async function settleFocus() {
  await nextTick()
  await Promise.resolve()
}

describe('modalDialog directive', () => {
  it('locks and inerts the background, traps focus, closes with Escape, and restores focus', async () => {
    const wrapper = trackedMount(Harness)
    const trigger = wrapper.get('[data-testid="trigger"]')
    ;(trigger.element as HTMLButtonElement).focus()
    await trigger.trigger('click')
    await settleFocus()

    const close = wrapper.get('[data-testid="close"]').element as HTMLButtonElement
    const last = wrapper.get('[data-testid="last"]').element as HTMLButtonElement
    const background = wrapper.get('[data-testid="background"]').element as HTMLElement
    expect(document.activeElement).toBe(close)
    expect(document.body.style.overflow).toBe('hidden')
    expect(background.inert).toBe(true)
    expect(background.getAttribute('aria-hidden')).toBe('true')

    close.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }))
    expect(document.activeElement).toBe(last)
    last.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(close)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleFocus()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.body.style.overflow).toBe('')
    expect(background.inert).toBe(false)
    expect(background.hasAttribute('aria-hidden')).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
  })

  it('keeps the background locked while stacked dialogs close from the top', async () => {
    const wrapper = trackedMount(StackedHarness)
    const trigger = wrapper.get('[data-testid="stack-trigger"]').element as HTMLButtonElement
    trigger.focus()
    trigger.click()
    await settleFocus()
    ;(wrapper.get('[data-testid="open-second"]').element as HTMLButtonElement).click()
    await settleFocus()

    expect(wrapper.findAll('[role="dialog"]')).toHaveLength(2)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleFocus()
    expect(wrapper.findAll('[role="dialog"]')).toHaveLength(1)
    expect(wrapper.get('[role="dialog"]').attributes('aria-label')).toBe('第一层')
    expect(document.body.style.overflow).toBe('hidden')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleFocus()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.body.style.overflow).toBe('')
    expect(document.activeElement).toBe(trigger)
  })

  it('does not inert a nested top dialog or its ancestor path', async () => {
    const wrapper = trackedMount(NestedHarness)
    ;(wrapper.get('[data-testid="nested-trigger"]').element as HTMLButtonElement).click()
    await settleFocus()
    ;(wrapper.get('[data-testid="open-nested"]').element as HTMLButtonElement).click()
    await settleFocus()

    const top = wrapper.findAll('[role="dialog"]').at(-1)
    expect(top?.attributes('aria-label')).toBe('内层')
    expect(top?.element.closest('[inert]')).toBeNull()
    expect(top?.element.contains(document.activeElement)).toBe(true)
    expect((wrapper.get('[data-testid="open-nested"]').element as HTMLElement).inert).toBe(true)
  })

  it('keeps lower layers covered when the middle of a three-dialog stack is removed', async () => {
    const wrapper = trackedMount(TripleHarness)
    ;(wrapper.get('[data-testid="triple-trigger"]').element as HTMLButtonElement).click()
    await settleFocus()
    ;(wrapper.get('[data-testid="triple-open-second"]').element as HTMLButtonElement).click()
    await settleFocus()
    ;(wrapper.get('[data-testid="triple-open-third"]').element as HTMLButtonElement).click()
    await settleFocus()

    ;(wrapper.vm as unknown as { closeSecond: () => void }).closeSecond()
    await settleFocus()
    const firstBackdrop = wrapper.get('[data-testid="triple-first"]').element as HTMLElement
    const thirdBackdrop = wrapper.get('[data-testid="triple-third"]').element as HTMLElement
    expect(firstBackdrop.inert).toBe(true)
    expect(Boolean(thirdBackdrop.inert)).toBe(false)
    const top = wrapper.get('[aria-label="第三层"]')
    expect(top.element.contains(document.activeElement)).toBe(true)
  })

  it('keeps focus in the top dialog when a covered dialog is removed', async () => {
    const wrapper = trackedMount(StackedHarness)
    const trigger = wrapper.get('[data-testid="stack-trigger"]').element as HTMLButtonElement
    trigger.focus()
    trigger.click()
    await settleFocus()
    ;(wrapper.get('[data-testid="open-second"]').element as HTMLButtonElement).click()
    await settleFocus()

    ;(wrapper.vm as unknown as { closeFirst: () => void }).closeFirst()
    await settleFocus()
    const topDialog = wrapper.get('[role="dialog"]')
    expect(topDialog.attributes('aria-label')).toBe('第二层')
    expect(topDialog.element.contains(document.activeElement)).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('falls back inside the remaining dialog when the saved focus target was removed', async () => {
    const wrapper = trackedMount(DisconnectedFocusHarness)
    ;(wrapper.get('[data-testid="disconnect-trigger"]').element as HTMLButtonElement).click()
    await settleFocus()
    ;(wrapper.get('[data-testid="disconnect-open-second"]').element as HTMLButtonElement).click()
    await settleFocus()
    ;(wrapper.vm as unknown as { showTrigger: boolean }).showTrigger = false
    await settleFocus()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleFocus()
    const remaining = wrapper.get('[role="dialog"]')
    expect(remaining.attributes('aria-label')).toBe('第一层')
    expect(remaining.element.contains(document.activeElement)).toBe(true)
  })

  it('preserves the original trigger across a same-tick dialog replacement', async () => {
    const wrapper = trackedMount(ReplacementHarness)
    const trigger = wrapper.get('[data-testid="replace-trigger"]').element as HTMLButtonElement
    trigger.focus()
    trigger.click()
    await settleFocus()
    ;(wrapper.get('[data-testid="replace-dialog"]').element as HTMLButtonElement).click()
    await settleFocus()

    expect(wrapper.get('[role="dialog"]').attributes('aria-label')).toBe('密钥')
    expect(document.body.style.overflow).toBe('hidden')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleFocus()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.body.style.overflow).toBe('')
    expect(document.activeElement).toBe(trigger)
  })

  it('starts a fresh session for a same-tick modal in a different Vue root', async () => {
    const first = trackedMount(Harness)
    const second = trackedMount(Harness)
    const firstTrigger = first.get('[data-testid="trigger"]').element as HTMLButtonElement
    const secondTrigger = second.get('[data-testid="trigger"]').element as HTMLButtonElement
    firstTrigger.focus()
    firstTrigger.click()
    await settleFocus()

    ;(first.vm as unknown as { close: () => void }).close()
    secondTrigger.focus()
    secondTrigger.click()
    await settleFocus()
    const secondBackground = second.get('[data-testid="background"]').element as HTMLElement
    expect(secondBackground.inert).toBe(true)
    expect(second.get('[role="dialog"]').element.contains(document.activeElement)).toBe(true)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleFocus()
    expect(document.activeElement).toBe(secondTrigger)
    const firstBackground = first.get('[data-testid="background"]').element as HTMLElement
    expect(firstBackground.inert).toBe(false)
  })

  it('uses the latest close callback after directive updates', async () => {
    const wrapper = trackedMount(CallbackHarness)
    ;(wrapper.get('[data-testid="callback-trigger"]').element as HTMLButtonElement).click()
    await settleFocus()
    ;(wrapper.vm as unknown as { useSecond: boolean }).useSecond = true
    await settleFocus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settleFocus()
    expect((wrapper.vm as unknown as { closedBy: string }).closedBy).toBe('second')
  })
})
