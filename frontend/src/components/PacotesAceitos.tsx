import { cx } from '../lib/format';
import styles from './PacotesAceitos.module.css';

/**
 * 31.129 (adendo v1.84): os pacotes, além do app da etapa, em que a tela também comprova a conclusão (a busca do
 * Configurações é de outro pacote). Quem preenche é o ensino, com os pacotes vistos na demonstração. Sem o campo (ou vazio),
 * não desenha nada. O pacote é um nome técnico de app, não dado de pessoa: aparece em fonte monoespaçada.
 */
export function PacotesAceitos({ pacotes, className }: { pacotes?: readonly string[] | null; className?: string }) {
  const lista = (pacotes ?? []).filter((p) => typeof p === 'string' && p.trim() !== '');
  if (lista.length === 0) return null;
  return (
    <p className={cx(styles.linha, className)}>
      Também aceita concluir em:{' '}
      {lista.map((p, i) => (
        <span key={p}>{i > 0 ? ', ' : ''}<span className="mono">{p}</span></span>
      ))}
    </p>
  );
}
