import { t } from '../../../shared/i18n';
import { Screen } from '../../../shared/ui';
import styles from './Screens.module.css';

interface HomePlaceholderProps {
  name: string;
}

export function HomePlaceholder({ name }: HomePlaceholderProps) {
  return (
    <Screen>
      <h1 className={styles.title}>{t('home.greeting', { name })}</h1>
    </Screen>
  );
}
