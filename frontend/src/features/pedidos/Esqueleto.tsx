import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import styles from './Pedidos.module.css';

/** Um cartão de pedido ou de aviso ainda sem dados: chips, título e uma linha, no mesmo formato do cartão real. */
function LinhaEsqueleto() {
  return (
    <li className={styles.esqueletoLinha} aria-hidden>
      <div className={styles.esqueletoChips}><Skeleton width={72} height={18} radius={9} /><Skeleton width={64} height={18} radius={9} /></div>
      <Skeleton width="55%" height={16} />
      <Skeleton width="80%" height={12} />
    </li>
  );
}

/** A região de carregamento de uma lista de cartões (lista, avisos, ocorrências): três cartões-esqueleto. */
export function EsqueletoDaLista({ label, quantos = 3 }: { label: string; quantos?: number }) {
  return (
    <LoadingRegion label={label}>
      <ul className={styles.lista}>{Array.from({ length: quantos }, (_, i) => <LinhaEsqueleto key={i} />)}</ul>
    </LoadingRegion>
  );
}
