import { openTelegramLink } from '@tma.js/sdk-react';

// Telegram usernames are 5 to 32 letters, digits and underscores.
const USERNAME_PATTERN = /^[A-Za-z0-9_]{5,32}$/;

/**
 * Opens a chat with the user. Returns false when the username is not a valid Telegram username.
 * Telegram closes the Mini App when it opens a chat link, so call this only on a user action.
 */
export function openChat(username: string): boolean {
  if (!USERNAME_PATTERN.test(username)) {
    return false;
  }
  const url = `https://t.me/${username}`;
  const result = openTelegramLink.ifAvailable(url);
  if (!result.ok) {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
  return true;
}
