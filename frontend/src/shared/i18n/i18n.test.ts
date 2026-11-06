import { describe, expect, it } from 'vitest';

import { t } from './index';

describe('t', () => {
  it('returns the text of a key', () => {
    expect(t('common.retry')).toBe('Повторить');
  });

  it('fills placeholders', () => {
    expect(t('home.greeting', { name: 'Ада' })).toBe('Привет, Ада!');
  });

  it('keeps a placeholder that has no value', () => {
    expect(t('home.greeting', {})).toBe('Привет, {name}!');
  });
});
