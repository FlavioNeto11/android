import { FlaskConical } from 'lucide-react';
import type { EnsinadoEmProva } from '../api/types';
import { ROTULO_EM_PROVA, explicacaoEmProva } from '../lib/emProva';
import { Badge } from './Badge';

/** 30.81: o fluxo ensinado que ainda espera a prova. Nada aparece quando o backend não manda o campo. */
export function SeloEmProva({ ensinado }: { ensinado: EnsinadoEmProva | null | undefined }) {
  if (!ensinado) return null;
  return (
    <Badge tone="warning" size="sm" icon={FlaskConical} title={explicacaoEmProva(ensinado)}>{ROTULO_EM_PROVA}</Badge>
  );
}
