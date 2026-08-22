import js from '@eslint/js'
import typescriptEslint from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**', '**/*.vue.js'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  ...typescriptEslint(),
  {
    files: ['src/**/*.{ts,vue}'],
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/attributes-order': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
    },
  },
]
