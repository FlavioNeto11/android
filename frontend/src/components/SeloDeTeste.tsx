import { FlaskConical } from 'lucide-react';
import { Badge } from './Badge';

export const ROTULO_PERSONA_DE_TESTE = 'teste';
export const EXPLICACAO_PERSONA_DE_TESTE =
  'Persona de teste: existe só para provar o painel e as rotinas, não é uma pessoa do parque. Fica escondida da lista de Personas, a menos que você peça para mostrá-las.';

/** 31.315 (adendo v1.139, `personas.teste`): o selo da persona de teste. Nada sem a marca verdadeira (ou em backend anterior). */
export function SeloDeTeste({ teste }: { teste: boolean | null | undefined }) {
  if (teste !== true) return null;
  return <Badge tone="warning" size="sm" icon={FlaskConical} title={EXPLICACAO_PERSONA_DE_TESTE}>{ROTULO_PERSONA_DE_TESTE}</Badge>;
}
