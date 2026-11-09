import type { ReactNode } from 'react';

import styles from './Screen.module.css';

interface ScreenProps {
  children: ReactNode;
  centered?: boolean;
}

export function Screen({ children, centered = false }: ScreenProps) {
  return <main className={centered ? styles.centered : styles.screen}>{children}</main>;
}
