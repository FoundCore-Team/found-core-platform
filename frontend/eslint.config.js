import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import prettier from 'eslint-config-prettier';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const tmaImport = {
  group: ['@tma.js/*'],
  message: 'Only src/shared/telegram may import the Telegram SDK.',
};
const appImport = {
  group: ['**/app/**'],
  message: 'features and shared must not import from app.',
};
const featuresImport = {
  group: ['**/features/**'],
  message: 'shared must not import from features.',
};

const noFetch = [
  { name: 'fetch', message: 'Use the typed client from src/shared/api.' },
  { name: 'XMLHttpRequest', message: 'Use the typed client from src/shared/api.' },
];
const noWindowFetch = {
  object: 'window',
  property: 'fetch',
  message: 'Use the typed client from src/shared/api.',
};
const noWindowTelegram = {
  object: 'window',
  property: 'Telegram',
  message: 'Only src/shared/telegram may touch window.Telegram.',
};

export default defineConfig([
  globalIgnores(['dist', 'coverage', 'src/shared/api/schema.d.ts']),

  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/consistent-type-imports': 'error',
    },
  },
  {
    files: ['eslint.config.js'],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['vite.config.ts'],
    languageOptions: { globals: globals.node },
  },

  {
    files: ['src/**/*.{ts,tsx}', 'tests/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
    },
  },
  {
    files: ['src/**/*.tsx'],
    extends: [jsxA11y.flatConfigs.recommended],
  },

  // Architecture boundaries from frontend/docs/rules.md, checked by the machine instead of review.
  {
    files: ['src/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-globals': ['error', ...noFetch],
      'no-restricted-properties': ['error', noWindowFetch, noWindowTelegram],
      'no-restricted-imports': ['error', { patterns: [tmaImport] }],
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'Text from other users is rendered as plain text only.',
        },
        {
          selector: 'TSEnumDeclaration',
          message: 'Use a string union or an as const object.',
        },
        {
          selector: 'ExportDefaultDeclaration',
          message: 'Use named exports.',
        },
      ],
    },
  },
  {
    files: ['src/shared/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [tmaImport, appImport, featuresImport] }],
    },
  },
  {
    files: ['src/shared/api/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-globals': 'off',
      'no-restricted-properties': ['error', noWindowTelegram],
    },
  },
  {
    files: ['src/shared/telegram/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [appImport, featuresImport] }],
      'no-restricted-properties': ['error', noWindowFetch],
    },
  },
  {
    files: ['src/features/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [tmaImport, appImport] }],
    },
  },

  prettier,
]);
