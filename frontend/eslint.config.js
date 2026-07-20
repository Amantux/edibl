import js from '@eslint/js'
import vue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...vue.configs['flat/essential'],
  {
    files: ['**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 2023, sourceType: 'module',
      globals: { window: 'readonly', document: 'readonly', localStorage: 'readonly',
        navigator: 'readonly', fetch: 'readonly', setTimeout: 'readonly', console: 'readonly' },
    },
    rules: { 'vue/multi-word-component-names': 'off', 'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }] },
  },
]
