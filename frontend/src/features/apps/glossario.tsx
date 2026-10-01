/**
 * Vocabulário da loja de aplicativos, explicado (revisão de UX, tarefa 06). Os selos "promovida", "pendente", "fora do
 * catálogo" eram jargão sem definição em lugar nenhum. Cada termo tem uma frase curta: aparece como dica acessível
 * (`Termo`: abre por foco de teclado e por hover, some com Esc) e, por inteiro, na legenda da tela (`LegendaDaLoja`),
 * que serve também onde o selo está dentro de um botão e não pode ter gatilho próprio.
 */
import type { ReactNode } from 'react';
import { Tooltip } from '../../components/Tooltip';
import { Disclosure } from '../../components/Disclosure';
import styles from './Apps.module.css';

export type TermoDaLoja =
  | 'promovida' | 'sem-promovida' | 'nunca-provada' | 'em-prova' | 'substituida' | 'quarentena'
  | 'pendente' | 'fora-do-catalogo' | 'atualizacao' | 'catalogo-de-acoes' | 'ia-livre';

export const GLOSSARIO_DA_LOJA: Record<TermoDaLoja, { rotulo: string; definicao: string }> = {
  promovida: {
    rotulo: 'Versão promovida',
    definicao: 'A versão que já foi provada em um aparelho e foi escolhida como a oficial do parque. É a única que a '
      + 'loja distribui, e todo aparelho que tem o app passa a segui-la.',
  },
  'sem-promovida': {
    rotulo: 'Sem versão promovida',
    definicao: 'Nenhuma versão deste app foi escolhida como oficial ainda, então não há o que distribuir. Envie uma '
      + 'versão, prove-a em um aparelho e promova-a na página do app.',
  },
  'nunca-provada': {
    rotulo: 'Nunca provada',
    definicao: 'A versão foi recebida, mas ainda não foi instalada e aberta em nenhum aparelho para provar que funciona.',
  },
  'em-prova': {
    rotulo: 'Em prova (canário)',
    definicao: 'A versão está sendo testada em um único aparelho antes de chegar aos outros. Se der certo, você a promove.',
  },
  substituida: {
    rotulo: 'Substituída',
    definicao: 'Uma versão que já foi a oficial e foi trocada por outra. Os aparelhos voltam sozinhos para a versão '
      + 'promovida anterior.',
  },
  quarentena: {
    rotulo: 'Em quarentena',
    definicao: 'A versão foi tirada de circulação por dar problema. Ela deixa de ser distribuída até você decidir.',
  },
  pendente: {
    rotulo: 'Pendente',
    definicao: 'Aparelhos que ainda vão receber a versão: estão desligados, ocupados ou na fila. A instalação acontece '
      + 'quando puderem.',
  },
  'fora-do-catalogo': {
    rotulo: 'Fora do catálogo',
    definicao: 'Aparelhos com uma versão do app que não está cadastrada na loja (instalada por fora, por exemplo). Eles '
      + 'não seguem a versão promovida até serem atualizados.',
  },
  atualizacao: {
    rotulo: 'Atualização para N',
    definicao: 'N aparelhos têm o app numa versão mais antiga que a promovida e podem ser atualizados.',
  },
  'catalogo-de-acoes': {
    rotulo: 'IA com catálogo de ações',
    definicao: 'O sistema já conhece as telas e as ações deste app, então a IA age com mais segurança. Sem catálogo, a '
      + 'IA descobre o caminho sozinha (caminho livre).',
  },
  'ia-livre': {
    rotulo: 'IA pelo caminho livre',
    definicao: 'O sistema ainda não tem um catálogo de ações deste app: a IA descobre as telas sozinha, o que é mais lento.',
  },
};

/** Dica de um termo, por foco ou hover. O texto continua visível; a definição é complemento, nunca o único sinal. */
export function Termo({ termo, children }: { termo: TermoDaLoja; children: ReactNode }) {
  const { definicao } = GLOSSARIO_DA_LOJA[termo];
  return (
    <Tooltip content={definicao}>
      <span className={styles.termo} tabIndex={0}>{children}</span>
    </Tooltip>
  );
}

/** Texto para `title` (selos dentro de botão, onde não cabe um gatilho focável). */
export const dicaDoTermo = (termo: TermoDaLoja): string => GLOSSARIO_DA_LOJA[termo].definicao;

const NA_LEGENDA: readonly TermoDaLoja[] = [
  'promovida', 'sem-promovida', 'pendente', 'fora-do-catalogo', 'atualizacao', 'em-prova', 'nunca-provada',
  'substituida', 'quarentena', 'catalogo-de-acoes',
];

/** Legenda recolhível: todos os termos da loja com a definição, alcançável por teclado. */
export function LegendaDaLoja() {
  return (
    <Disclosure summary="O que significam os selos da loja" bare>
      <dl className={styles.legenda}>
        {NA_LEGENDA.map((t) => (
          <div key={t}>
            <dt>{GLOSSARIO_DA_LOJA[t].rotulo}</dt>
            <dd>{GLOSSARIO_DA_LOJA[t].definicao}</dd>
          </div>
        ))}
      </dl>
    </Disclosure>
  );
}
