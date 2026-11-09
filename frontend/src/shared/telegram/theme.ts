import { themeParams } from '@tma.js/sdk-react';

// Creates the --tg-theme-* CSS variables and keeps them in sync with the Telegram theme.
export function bindTheme(): void {
  themeParams.mount.ifAvailable();
  themeParams.bindCssVars.ifAvailable();
}
