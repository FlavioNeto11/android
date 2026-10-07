import { Beaker, CirclePlay } from 'lucide-react';
import { formatDateTime, formatQuando } from '../lib/time';
import { Badge } from './Badge';

export const ROTULO_NASCIDO_DE_PROVA = 'Nascido de uma prova';
export const EXPLICACAO_NASCIDO_DE_PROVA =
  'Nasceu de uma prova (uma sessão de treino feita para testar o painel ou a plataforma), não de uso real. Costuma ficar desligado de propósito.';

/** 31.131 (adendo v1.87): o fluxo ou a sessão de treino que a marca `nascido_de_prova` identifica. Nada aparece sem a marca (e em backend anterior). */
export function SeloNascidoDeProva({ nascido }: { nascido: boolean | null | undefined }) {
  if (nascido !== true) return null;
  return <Badge tone="neutral" size="sm" icon={Beaker} title={EXPLICACAO_NASCIDO_DE_PROVA}>{ROTULO_NASCIDO_DE_PROVA}</Badge>;
}

/**
 * 31.168 (adendo v1.97): o fluxo de prova que uma pessoa religou para uso real. `desde` é a data da linha "religado para uso real"
 * da trilha; sem ela (ou em backend anterior) nada aparece, e um valor que não é data não vira selo.
 */
export function SeloEmUsoReal({ desde }: { desde: string | null | undefined }) {
  if (!desde || formatDateTime(desde) === '—') return null;
  return (
    <Badge tone="success" size="sm" icon={CirclePlay} title={`Nasceu de uma prova e uma pessoa o religou para uso real em ${formatDateTime(desde)}.`}>
      Em uso real desde {formatQuando(desde)}
    </Badge>
  );
}
