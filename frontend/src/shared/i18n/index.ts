import { ru, type MessageKey } from './ru';

export type { MessageKey };

export function t(key: MessageKey, params?: Readonly<Record<string, string | number>>): string {
  const template: string = ru[key];
  if (params === undefined) {
    return template;
  }
  return template.replace(/\{(\w+)\}/g, (placeholder: string, name: string) => {
    const value = params[name];
    return value === undefined ? placeholder : String(value);
  });
}
