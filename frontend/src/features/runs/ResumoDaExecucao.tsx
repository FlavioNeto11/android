import { HelpCircle } from 'lucide-react';
import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { RunSummary } from '../../api/types';
import { Tooltip } from '../../components/Tooltip';
import { cx } from '../../lib/format';
import { hashDe } from '../../lib/rotas';
import { useNow } from '../../lib/time';
import { apiAprendizado } from '../aprendizado/api';
import { useCarga } from '../aprendizado/useCarga';
import { vereditoDoPedido } from '../aprendizado/validacao';
import {
  type AbaDaExecucao, pedidoEhLongo, fonteDoEfeitoRepetido, fraseDoEfeitoRepetido,
  objetivosComSucesso, oQuePrecisaDaPessoa, resultadoDaExecucao,
} from './resumo';
import styles from './ResumoDaExecucao.module.css';
import { SeloDeOrigem, ehDoSistema, origemDaExecucao } from './SeloDeProva';

/** O texto da legenda de "sucesso comprovado": um só, para a tela e para o teste. */
export const LEGENDA_DE_SUCESSO_COMPROVADO =
  'Sucesso comprovado: cada etapa foi confirmada por evidência na tela do aparelho, não só pela resposta do agente. '
  + 'Se alguma etapa foi confirmada à mão ou ficou sem prova, o Relatório avisa.';

/**
 * A legenda que abre por foco de teclado, por toque e por mouse (o `title` do selo só abria com o mouse). É um botão
 * para poder receber foco; ele não faz nada além de mostrar o texto.
 */
export function LegendaDeSucessoComprovado({ rotulo = 'sucesso comprovado' }: { rotulo?: string }) {
  return (
    <Tooltip content={LEGENDA_DE_SUCESSO_COMPROVADO} placement="bottom">
      <button type="button" className={styles.legenda} aria-label={`O que é ${rotulo}?`}>
        <HelpCircle size={13} aria-hidden />
        <span>{rotulo}</span>
      </button>
    </Tooltip>
  );
}

/**
 * 30.43: numa execução de validação (prova de fluxo, re-execução do QA) quem comprova é o ITEM, não a execução: ela
 * pode terminar "concluída" e o fluxo levar evidência contra (caso da e1b7d0). O veredito vem do pedido de validação
 * dessa execução. Sem pedido achado, a legenda de sempre; enquanto lê ou se a leitura falha, nunca "sucesso comprovado".
 */
function VereditoDaValidacao({ runId }: { runId: string }) {
  const { dado, erro, carregando } = useCarga(
    (signal) => apiAprendizado.validacoes({ run: runId, limite: 1 }, signal), runId);
  if (carregando && !dado) return <span className={styles.secundario} data-veredito="lendo">Veredito da validação: conferindo…</span>;
  if (erro) return <span className={styles.secundario} data-veredito="indisponivel">Veredito da validação: indisponível agora</span>;
  const veredito = vereditoDoPedido(dado?.itens[0] ?? null);
  if (!veredito) return <LegendaDeSucessoComprovado />;
  return <span data-veredito={veredito}>Veredito da validação: <strong>{veredito}</strong></span>;
}

/** 29.60: uma etapa com efeito repetido, já pronta para o resumo. */
export interface EfeitoRepetidoNoResumo {
  chave: string; titulo: string; aparelho: string; copias: number; fonte: 'verificador' | 'acoes';
}

interface Props {
  run: RunSummary;
  /** 29.60: as etapas cujo efeito apareceu mais de uma vez (29.58). Vazio = a linha não aparece. */
  repetidos?: readonly EfeitoRepetidoNoResumo[];
  /** Perguntas da IA que esperam resposta (execução `needs_input`). */
  perguntas: number;
  /** 29.52: o tipo da credencial que a pergunta pede; ela não se responde aqui. */
  sensivel?: string | null;
  bloqueados: number;
  textosParaAprovar: number;
  terminal: boolean;
  /** Leva a uma guia do detalhe (as linhas de "O que precisa de você" são atalhos). */
  irParaAba: (aba: AbaDaExecucao) => void;
}

/**
 * Três linhas no topo do detalhe: o pedido (completo, com "Ver pedido completo" quando é longo), o resultado em uma
 * frase e o que depende da pessoa. A terceira só existe quando há algo a decidir.
 */
export function ResumoDaExecucao({
  run, repetidos = [], perguntas, sensivel = null, bloqueados, textosParaAprovar, terminal, irParaAba,
}: Props) {
  const now = useNow();
  const idPedido = useId();
  const pedidoRef = useRef<HTMLParagraphElement>(null);
  const [aberto, setAberto] = useState(false);
  // O corte real depende da largura: além do tamanho do texto, mede se a caixa de duas linhas realmente cortou.
  const [cortado, setCortado] = useState(false);
  useLayoutEffect(() => {
    const el = pedidoRef.current;
    if (!el || aberto) return undefined;
    const medir = () => setCortado(el.scrollHeight > el.clientHeight + 1);
    medir();
    window.addEventListener('resize', medir);
    return () => window.removeEventListener('resize', medir);
  }, [run.command, aberto]);

  const origem = origemDaExecucao(run);
  const doSistema = ehDoSistema(run);
  const longo = pedidoEhLongo(run.command) || cortado;
  const resultado = resultadoDaExecucao(run, now);
  const sucessos = objetivosComSucesso(run);
  const precisa = oQuePrecisaDaPessoa({
    status: run.status, perguntas, sensivel, bloqueados, textosParaAprovar, terminal,
  });

  return (
    <dl className={styles.resumo} aria-label="Resumo da execução">
      <div className={styles.linha}>
        <dt className={styles.rotulo}>{doSistema ? 'Comando de origem' : 'Pedido'}</dt>
        <dd className={styles.valor}>
          {origem ? (
            <p className={styles.origemDaProva}>
              <SeloDeOrigem origem={origem} size="md" />
              {/* 30.38 (a): a validação do QA leva ao pedido dela, em Aprendizado › Validação. */}
              {origem === 'validacao_qa' ? (
                <a className={styles.linkDaOrigem} href={hashDe('aprendizado', { query: { aba: 'validacao' } })}
                   title={run.origem_ref ?? undefined}>Ver o pedido de validação</a>
              ) : null}
            </p>
          ) : null}
          <p ref={pedidoRef} id={idPedido} className={cx(styles.pedido, longo && !aberto && styles.pedidoCortado)}>
            {run.command}
          </p>
          {longo ? (
            <button type="button" className={styles.expansor} aria-expanded={aberto} aria-controls={idPedido}
                    onClick={() => setAberto((v) => !v)}>
              {aberto ? 'Mostrar menos' : 'Ver pedido completo'}
            </button>
          ) : null}
        </dd>
      </div>
      <div className={styles.linha}>
        <dt className={styles.rotulo}>Resultado</dt>
        <dd className={styles.valor}>
          <span className={styles.resultado}>{resultado}</span>
          {sucessos ? <span className={styles.secundario}> · {sucessos}</span> : null}
          {run.status === 'completed' || run.status === 'completed_with_issues' ? (
            <>{' '}{origem === 'prova_fluxo' || origem === 'validacao_qa'
              ? <VereditoDaValidacao runId={run.id} />
              : <LegendaDeSucessoComprovado />}</>
          ) : null}
        </dd>
      </div>
      {repetidos.length > 0 ? (
        <div className={cx(styles.linha, styles.linhaAtencao)}>
          <dt className={styles.rotulo}>Efeito repetido</dt>
          <dd className={styles.valor}>
            <ul className={styles.precisa}>
              {repetidos.map((r) => (
                <li key={r.chave}>
                  {r.titulo} no {r.aparelho}: {fraseDoEfeitoRepetido(r.copias)} ({fonteDoEfeitoRepetido(r.fonte)}).
                  Confira no app e apague as cópias, se for o caso.
                </li>
              ))}
            </ul>
          </dd>
        </div>
      ) : null}
      {precisa.length > 0 ? (
        <div className={cx(styles.linha, styles.linhaAtencao)}>
          <dt className={styles.rotulo}>Precisa de você</dt>
          <dd className={styles.valor}>
            <ul className={styles.precisa}>
              {precisa.map((p) => (
                <li key={p.chave}>
                  {p.aba ? (
                    <button type="button" className={styles.expansor} onClick={() => irParaAba(p.aba as AbaDaExecucao)}>{p.texto}</button>
                  ) : p.texto}
                </li>
              ))}
            </ul>
          </dd>
        </div>
      ) : null}
    </dl>
  );
}
