/**
 * 31.128 (adendo v1.86): a pós-condição `text_visible` que já aparece na tela em que a etapa começa deixaria a etapa passar
 * sem agir. O backend avisa na prévia (`pos_condicoes_ja_valem` ao lado de `warnings`) e recusa o salvar (400
 * `pos_condicao_ja_vale`, com a mesma lista no corpo): o painel mostra o aviso DENTRO da etapa citada, com as até três
 * sugestões (textos da tela seguinte) como botões que trocam o valor da pós-condição. Antes só havia a frase solta na
 * lista de avisos e na dica do "Salvar".
 */
import { TriangleAlert } from 'lucide-react';
import type { PosCondicaoQueJaVale, SugestaoPronta } from '../../api/types';
import { Button } from '../../components/Button';
import styles from './Training.module.css';

export type { PosCondicaoQueJaVale };

/** No máximo três sugestões por item (contrato); mais que isso é descartado, e o resto do item vale. */
const MAXIMO_DE_SUGESTOES = 3;

/**
 * 31.142 (adendo v1.91): as sugestões prontas dizem o `kind` e o `value` que o botão aplica (`element_present` com `desc==Back`
 * quando o texto era o de um elemento, `text_visible` nos outros). Backend anterior só manda `sugestoes` (texto): vira
 * `text_visible`, como o painel sempre fez.
 */
function lerProntas(bruto: unknown, sugestoes: string[]): SugestaoPronta[] {
  const prontas: SugestaoPronta[] = [];
  if (Array.isArray(bruto)) {
    for (const x of bruto) {
      if (!x || typeof x !== 'object') continue;
      const o = x as Record<string, unknown>;
      if (typeof o.kind !== 'string' || !o.kind || typeof o.value !== 'string' || !o.value.trim() || typeof o.texto !== 'string' || !o.texto.trim()) continue;
      prontas.push({ kind: o.kind, value: o.value, texto: o.texto });
    }
  }
  return prontas.length ? prontas.slice(0, MAXIMO_DE_SUGESTOES) : sugestoes.map((s) => ({ kind: 'text_visible', value: s, texto: s }));
}

/**
 * O leitor da lista, tolerante como os outros: item sem etapa ou sem valor cai fora, sugestão que não é texto ou é vazia
 * também, e sem a lista (backend anterior) volta vazio. Fica isolado aqui para o formato mudar num lugar só.
 */
export function lerPosCondicoes(bruto: unknown): PosCondicaoQueJaVale[] {
  if (!Array.isArray(bruto)) return [];
  const saida: PosCondicaoQueJaVale[] = [];
  for (const x of bruto) {
    if (!x || typeof x !== 'object') continue;
    const o = x as Record<string, unknown>;
    if (typeof o.etapa !== 'string' || !o.etapa || typeof o.valor !== 'string') continue;
    const sugestoes = (Array.isArray(o.sugestoes) ? o.sugestoes : [])
      .filter((s): s is string => typeof s === 'string' && s.trim() !== '')
      .slice(0, MAXIMO_DE_SUGESTOES);
    saida.push({ etapa: o.etapa, valor: o.valor, sugestoes, sugestoes_prontas: lerProntas(o.sugestoes_prontas, sugestoes), message: typeof o.message === 'string' ? o.message : '' });
  }
  return saida;
}

/** O motivo do "Salvar": curto, com o nome da etapa; o texto completo fica dentro da etapa. `null` sem ocorrência. */
export function motivoDaPosCondicao(itens: readonly PosCondicaoQueJaVale[], tituloDe: (key: string) => string | null): string | null {
  const nomes = itens.map((i) => tituloDe(i.etapa)).filter((t): t is string => t !== null);
  if (!nomes.length) return null;
  return nomes.length === 1
    ? `Troque o que a etapa “${nomes[0]}” confere: o texto já aparece na tela em que ela começa.`
    : `Troque o que as etapas ${nomes.map((n) => `“${n}”`).join(', ')} conferem: o texto já aparece na tela em que elas começam.`;
}

export function AvisoDaPosCondicao({ item, onUsar }: { item: PosCondicaoQueJaVale; onUsar: (pronta: SugestaoPronta) => void }) {
  return (
    <div className={styles.posJaVale} role="alert" aria-label="A conferência da etapa já vale na tela de partida">
      <p>
        <TriangleAlert size={14} aria-hidden /> O texto <strong>“{item.valor}”</strong> já aparece na tela em que esta etapa começa: ela
        passaria sem agir. Troque o que ela confere por um texto que só aparece depois da etapa.
      </p>
      {item.sugestoes_prontas.length ? (
        <div className={styles.posJaValeSugestoes}>
          <span className={styles.muted}>Da tela seguinte:</span>
          {item.sugestoes_prontas.map((s) => (
            <Button key={`${s.kind}:${s.value}`} size="sm" variant="outline" onClick={() => onUsar(s)} label={`Usar “${s.texto}” como o texto que a etapa confere`}>
              Usar “{s.texto}”
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
