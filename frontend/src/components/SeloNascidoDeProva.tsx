import { Beaker } from 'lucide-react';
import { Badge } from './Badge';

export const ROTULO_NASCIDO_DE_PROVA = 'Nascido de uma prova';
export const EXPLICACAO_NASCIDO_DE_PROVA =
  'Nasceu de uma prova (uma sessão de treino feita para testar o painel ou a plataforma), não de uso real. Costuma ficar desligado de propósito.';

/** 31.131 (adendo v1.87): o fluxo ou a sessão de treino que a marca `nascido_de_prova` identifica. Nada aparece sem a marca (e em backend anterior). */
export function SeloNascidoDeProva({ nascido }: { nascido: boolean | null | undefined }) {
  if (nascido !== true) return null;
  return <Badge tone="neutral" size="sm" icon={Beaker} title={EXPLICACAO_NASCIDO_DE_PROVA}>{ROTULO_NASCIDO_DE_PROVA}</Badge>;
}
