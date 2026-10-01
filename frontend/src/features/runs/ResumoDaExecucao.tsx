import { HelpCircle } from 'lucide-react';
import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { RunSummary } from '../../api/types';
import { Tooltip } from '../../components/Tooltip';
import { cx } from '../../lib/format';
import { useNow } from '../../lib/time';
import {
  objetivosComSucesso, oQuePrecisaDaPessoa, pedidoEhLongo, resultadoDaExecucao, type AbaDaExecucao,
} from './resumo';
import styles from './ResumoDaExecucao.module.css';

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

interface Props {
  run: RunSummary;
  /** Perguntas da IA que esperam resposta (execução `needs_input`). */
  perguntas: number;
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
export function ResumoDaExecucao({ run, perguntas, bloqueados, textosParaAprovar, terminal, irParaAba }: Props) {
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

  const longo = pedidoEhLongo(run.command) || cortado;
  const resultado = resultadoDaExecucao(run, now);
  const sucessos = objetivosComSucesso(run);
  const precisa = oQuePrecisaDaPessoa({ status: run.status, perguntas, bloqueados, textosParaAprovar, terminal });

  return (
    <dl className={styles.resumo} aria-label="Resumo da execução">
      <div className={styles.linha}>
        <dt className={styles.rotulo}>Pedido</dt>
        <dd className={styles.valor}>
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
            <>{' '}<LegendaDeSucessoComprovado /></>
          ) : null}
        </dd>
      </div>
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
