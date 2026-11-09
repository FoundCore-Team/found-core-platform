import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  backButton,
  getRawInitData,
  haptic,
  initTelegram,
  isInsideTelegram,
  NotInTelegramError,
  openChat,
} from './index';
import { createMockInitData, installMockTelegramEnv } from './mockEnv';

afterEach(() => {
  // The mocked launch parameters live in sessionStorage and would leak into the next test.
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe('outside Telegram', () => {
  it('knows it is not inside Telegram', () => {
    expect(isInsideTelegram()).toBe(false);
  });

  it('has no init data', () => {
    expect(() => getRawInitData()).toThrow(NotInTelegramError);
  });

  it('does nothing in the version-gated wrappers', () => {
    expect(() => {
      initTelegram();
      backButton.show();
      backButton.hide();
      backButton.onClick(() => undefined)();
      haptic.impact();
      haptic.notification('success');
      haptic.selection();
    }).not.toThrow();
  });

  it('opens a chat through a plain link', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);

    expect(openChat('seed_ada')).toBe(true);
    expect(open).toHaveBeenCalledWith('https://t.me/seed_ada', '_blank', 'noopener,noreferrer');
  });

  it.each([['a b'], ['ab'], ['../etc/passwd'], ['name?x=1'], ['']])(
    'refuses the invalid username %j',
    (username) => {
      const open = vi.spyOn(window, 'open').mockImplementation(() => null);

      expect(openChat(username)).toBe(false);
      expect(open).not.toHaveBeenCalled();
    },
  );
});

describe('inside Telegram (mocked environment)', () => {
  const rawInitData = createMockInitData({ id: 1, first_name: 'Ada' });

  beforeEach(() => {
    installMockTelegramEnv(rawInitData);
  });

  it('knows it is inside Telegram and returns the raw init data', () => {
    expect(isInsideTelegram()).toBe(true);
    expect(getRawInitData()).toBe(rawInitData);
  });

  it('initializes safely twice and binds the theme CSS variables', () => {
    initTelegram();
    initTelegram();

    expect(document.documentElement.style.getPropertyValue('--tg-theme-bg-color')).toBe('#ffffff');
  });
});
