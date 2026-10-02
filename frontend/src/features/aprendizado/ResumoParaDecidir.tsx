import { Zap } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Badge } from '../../components/Badge';
import { toApiError } from '../../api/client';
import { hashDe } from '../../lib/rotas';
import { formatDateTime, formatQuando } from '../../lib/time';
import { apiAprendizado } from './api';
import { rotuloDaFerramenta } from './detalhe';
import type { AcaoDaReceita, ConteudoDoItem, DetalheDoLivro, EntradaDoLivro } from './model';
import styles from './Aprendizado.module.css';

/** O nome do alvo como a pessoa o vê na tela: o texto ou a descrição; sem eles, o fim do id (`…/send_button`). */
function nomeDoAlvo(a: AcaoDaReceita): string | null {
  for (const s of a.alvo) if (s.texto) return `“${s.texto}”`;
  for (const s of a.alvo) if (s.desc) return `“${s.desc}”`;
  for (const s of a.alvo) if (s.rid) return s.rid.split('/').pop() ?? s.rid;
  return null;
}

/** Os passos de uma receita em uma frase cada: "Toca em “Enviar”". */
export function frasesDaReceita(acoes: readonly AcaoDaReceita[]): { texto: string; efeito: boolean }[] {
  return acoes.map((a) => {
    const alvo = nomeDoAlvo(a);
    return { texto: `${rotuloDaFerramenta(a.ferramenta)}${alvo ? ` em ${alvo}` : ''}`, efeito: a.commit };
  });
}

/** O aparelho da etapa de origem (`r-…:android-05:v2:send_message_i1` → `android-05`). */
export function aparelhoDaEtapa(stepId: string | null | undefined): string | null {
  const partes = (stepId ?? '').split(':');
  return partes.length >= 3 && partes[1] ? partes[1] : null;
}

function Corpo({ c, item }: { c: ConteudoDoItem; item: EntradaDoLivro }) {
  if (c.tipo === 'receita') {
    const frases = frasesDaReceita(c.acoes);
    const origem = c.origem;
    const aparelho = origem.tipo === 'execucao' ? aparelhoDaEtapa(origem.step_id) : null;
    return (
      <>
        <span>
          <strong>O que faz:</strong>{' '}
          {frases.length === 0 ? 'nenhum passo gravado' : frases.map((f, i) => (
            <span key={i}>
              {i > 0 ? ' → ' : ''}{f.texto}
              {f.efeito ? <> <Badge tone="warning" size="sm" icon={Zap} title="É este passo que faz o efeito fora do sistema">efeito</Badge></> : null}
            </span>
          ))}
        </span>
        <span>
          <strong>Aprendida:</strong>{' '}
          {origem.tipo === 'execucao' ? (
            <>
              {aparelho ? `no ${aparelho}, ` : ''}
              {origem.run_id ? <a className={styles.linkAlvo} href={hashDe('execucoes', { segmentos: [origem.run_id] })}>na execução {origem.run_id}</a>
                : 'numa execução que não existe mais'}
            </>
          ) : origem.tipo === 'treino' ? 'no treino' : 'origem desconhecida'}
          {item.created_at ? <span title={formatDateTime(item.created_at)}> · {formatQuando(item.created_at)}</span> : null}
        </span>
        <span>
          <strong>Versão do app:</strong> {c.identidade.app_version ?? 'sem dado'}
          {c.identidade.variante ? ` · ${c.identidade.variante}` : ''}
        </span>
      </>
    );
  }
  if (c.tipo === 'fluxo') {
    return <span><strong>Comando:</strong> {c.comando_modelo ?? 'sem dado'}</span>;
  }
  return null;
}

/**
 * O que a pessoa precisa ver ANTES de aprovar ou rejeitar (B3 da validação de 02/10): o que o item faz, onde e quando
 * foi aprendido e em que versão. Dois itens de mesmo nome ("send_message_i1 (v1)") se distinguem por aqui. Carrega o
 * detalhe do item sob demanda; a fila "Para aprovar" é curta.
 */
export function ResumoParaDecidir({ entrada }: { entrada: EntradaDoLivro }) {
  const [conteudo, setConteudo] = useState<ConteudoDoItem | null | undefined>(undefined);
  const [erro, setErro] = useState<string | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    apiAprendizado.detalhe(entrada.kind, entrada.ref, ctl.signal)
      .then((d: DetalheDoLivro) => setConteudo(d.conteudo ?? null))
      .catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toApiError(e).message); });
    return () => ctl.abort();
  }, [entrada.kind, entrada.ref]);
  if (erro) return <p className={styles.erroInline}>Não foi possível ler o que o item faz: {erro}</p>;
  if (conteudo === undefined) return <p className={styles.secaoLead}>Lendo o que o item faz…</p>;
  if (conteudo === null) return null;
  return <div className={styles.resumoParaDecidir} data-resumo-para-decidir><Corpo c={conteudo} item={entrada} /></div>;
}
