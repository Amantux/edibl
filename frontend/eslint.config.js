import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...vue.configs['flat/essential'],
  {
    files: ['**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 2023, sourceType: 'module',
      // The standard browser set, not a hand-kept list. The hand-kept one had
      // drifted: confirm, btoa, crypto, Image and URL are all used in src/ and
      // none were declared. eslint 9 tolerated that; eslint 10 does not.
      globals: { ...globals.browser, __APP_VERSION__: 'readonly' },
    },
    rules: { 'vue/multi-word-component-names': 'off', 'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }] },
  },
]
