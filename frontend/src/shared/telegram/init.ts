import { init, isTMA, retrieveRawInitData } from '@tma.js/sdk-react';

import { env } from '../config';
import { mountBackButton } from './backButton';
import { bindTheme } from './theme';

export class NotInTelegramError extends Error {
  constructor() {
    super('The app is not running inside Telegram');
    this.name = 'NotInTelegramError';
  }
}

let initialized = false;

export function isInsideTelegram(): boolean {
  return isTMA();
}

// The order matters: the SDK is initialized first, then components are mounted and bound.
export function initTelegram(): void {
  if (initialized || !isInsideTelegram()) {
    return;
  }
  init();
  bindTheme();
  mountBackButton();
  initialized = true;
}

/** The raw `initData` string for the backend, which validates it. Never decide anything from it here. */
export function getRawInitData(): string {
  if (env.devInitData !== undefined) {
    return env.devInitData;
  }
  let raw: string | undefined;
  try {
    raw = isInsideTelegram() ? retrieveRawInitData() : undefined;
  } catch {
    raw = undefined;
  }
  if (raw === undefined || raw === '') {
    throw new NotInTelegramError();
  }
  return raw;
}
