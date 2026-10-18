import js from '@eslint/js'
import globals from 'globals'
import vue from 'eslint-plugin-vue'
import tsParser from '@typescript-eslint/parser'
import tsPlugin from '@typescript-eslint/eslint-plugin'

export default [
  {
    ignores: ['dist/**', 'node_modules/**'],
  },
  js.configs.recommended,
  ...vue.configs['flat/essential'],
  {
    files: ['scripts/**/*.mjs', 'tests/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['**/*.{js,ts,vue}'],
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      // View names match the supplied Vue project and route names.
      'vue/multi-word-component-names': 'off',
      'vue/no-v-html': 'off',
    },
  },
  {
    files: ['**/*.{ts,vue}'],
    plugins: { '@typescript-eslint': tsPlugin },
    rules: {
      'no-undef': 'off',
      'no-unused-vars': 'off',
      'no-redeclare': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/no-redeclare': 'error',
    },
  },
  {
    files: ['**/*.ts'],
    languageOptions: { parser: tsParser },
  },
  {
    files: ['**/*.vue'],
    languageOptions: { parserOptions: { parser: tsParser } },
  },
]
