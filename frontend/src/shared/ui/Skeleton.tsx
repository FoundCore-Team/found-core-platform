import styles from './Skeleton.module.css';

interface SkeletonProps {
  variant: 'title' | 'line';
}

export function Skeleton({ variant }: SkeletonProps) {
  return <div className={variant === 'title' ? styles.title : styles.line} aria-hidden="true" />;
}
