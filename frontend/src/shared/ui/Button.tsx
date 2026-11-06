import type { ButtonHTMLAttributes } from 'react';

import styles from './Button.module.css';

type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'type'>;

export function Button(props: ButtonProps) {
  return <button type="button" className={styles.button} {...props} />;
}
