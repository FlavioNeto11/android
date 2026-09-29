import { useEffect, useId, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { RunSummary } from '../../api/types';
import { Disclosure } from '../../components/Disclosure';
import { formatInt, plural } from '../../lib/format';
import usageStyles from '../usage/Usage.module.css';
import { UsageTiles } from '../usage/UsageView';
import {
  lerProjecao, rotuloDaJanela, semHistorico, textoDasChamadas, textoDoTempo, textoDoUsd, type ProjecaoDoPlano,
} from './projecao';
import styles from './Runs.module.css';

type Leitura =
  | { estado: 'carregando' }
  | { estado: 'pronta'; projecao: ProjecaoDoPlano }
  | { estado: 'sem_plano' }
  | { estado: 'ausente' }
  | { estado: 'erro'; mensagem: string };

/**
 * `GET /api/runs/{id}/projection`, relido quando a situação da execução muda (ex.: `planning` → `planned`, o plano
 * acabou de nascer). A leitura anterior fica na tela enquanto a nova chega. 404 (servidor sem a rota, ou execução que
 * sumiu) e resposta sem as somas viram "ausente": a seção some, em vez de um erro que a pessoa não tem como resolver.
 */
function useProjecao(runId: string, recarregarQuando: string): Leitura {
  const [lida, setLida] = useState<{ runId: string; leitura: Leitura } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    api.runProjection(runId, ctl.signal)
      .then((raw) => {
        if (ctl.signal.aborted) return;
        const projecao = lerProjecao(raw);
        setLida({ runId, leitura: projecao ? { estado: 'pronta', projecao } : { estado: 'ausente' } });
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        const err = toApiError(e);
        const leitura: Leitura = err.status === 409 && err.code === 'no_plan' ? { estado: 'sem_plano' }
          : err.status === 404 ? { estado: 'ausente' } : { estado: 'erro', mensagem: err.message };
        setLida({ runId, leitura });
      });
    return () => ctl.abort();
  }, [runId, recarregarQuando]);
  return lida && lida.runId === runId ? lida.leitura : { estado: 'carregando' };
}

/**
 * "Normal medido para este plano" (item 18.3): ao lado do custo real, o que o histórico diz que um plano assim
 * costuma custar — mediana e p90 de chamadas de IA, US$ e tempo —, com a janela EFETIVA escrita por extenso.
 * Nunca é erro para a pessoa: sem plano ainda, diz isso; sem histórico, diz que a primeira execução mede.
 */
export function ProjecaoDoPlano({ run }: { run: Pick<RunSummary, 'id' | 'status'> }) {
  const leitura = useProjecao(run.id, run.status);
  const titulo = useId();
  if (leitura.estado === 'carregando' || leitura.estado === 'ausente') return null;
  return (
    <section className={usageStyles.wrap} aria-labelledby={titulo}>
      <h4 id={titulo} className={styles.subTitle} style={{ marginBottom: 0 }}>Normal medido para este plano</h4>
      {leitura.estado === 'sem_plano' ? (
        <p className={usageStyles.empty}>A execução ainda não tem plano: a projeção aparece quando ele ficar pronto.</p>
      ) : leitura.estado === 'erro' ? (
        <p className={usageStyles.empty}>Projeção indisponível agora: {leitura.mensagem}</p>
      ) : (
        <CorpoDaProjecao projecao={leitura.projecao} />
      )}
    </section>
  );
}

function CorpoDaProjecao({ projecao: p }: { projecao: ProjecaoDoPlano }) {
  const janela = <p className={usageStyles.toolbarNote}>{rotuloDaJanela(p)}.</p>;
  if (semHistorico(p)) {
    return (
      <>
        {janela}
        <p className={usageStyles.empty}>
          Sem histórico suficiente para projetar {p.etapas.length === 1 ? 'a etapa' : `as ${formatInt(p.etapas.length)} etapas`} — a
          primeira execução mede.
        </p>
      </>
    );
  }
  const minimo = p.minimo_de_amostras;
  return (
    <>
      {janela}
      <UsageTiles
        tiles={[
          { key: 'chamadas', label: 'Chamadas de IA (mediana–p90)', value: textoDasChamadas(p.chamadas) },
          { key: 'usd', label: 'US$ (mediana–p90)', value: textoDoUsd(p.usd) },
          { key: 'tempo', label: 'Tempo das etapas (mediana–p90)', value: textoDoTempo(p.segundos) },
        ]}
      />
      {p.sem_base.length > 0 ? (
        <p className={usageStyles.empty}>
          {plural(p.sem_base.length, 'etapa sem base própria', 'etapas sem base própria')}
          {minimo !== null ? ` (menos de ${plural(minimo, 'amostra', 'amostras')} da ação)` : ''}: no lugar, o normal
          do app quando ele existe, ou zero — nunca um número inventado.
        </p>
      ) : null}
      {p.amostras_sem_custo > 0 ? (
        <p className={usageStyles.empty}>
          {plural(p.amostras_sem_custo, 'amostra não fez', 'amostras não fizeram')} chamada de IA (receita ou fluxo
          reproduzindo): é número pequeno de verdade, não registro perdido.
        </p>
      ) : null}
      {p.etapas.length > 0 ? (
        <Disclosure bare summary="Por etapa" meta={plural(p.etapas.length, 'etapa', 'etapas')}>
          {() => <TabelaDaProjecao projecao={p} />}
        </Disclosure>
      ) : null}
    </>
  );
}

function TabelaDaProjecao({ projecao: p }: { projecao: ProjecaoDoPlano }) {
  return (
    <div className={usageStyles.tableWrap}>
      <table className={usageStyles.table}>
        <caption className="sr-only">Normal medido por etapa do plano</caption>
        <thead>
          <tr>
            <th scope="col">Etapa</th>
            <th scope="col">Ação</th>
            <th scope="col" className={usageStyles.num}>Amostras</th>
            <th scope="col" className={usageStyles.num}>Chamadas</th>
            <th scope="col" className={usageStyles.num}>US$</th>
            <th scope="col" className={usageStyles.num}>Tempo</th>
          </tr>
        </thead>
        <tbody>
          {p.etapas.map((e) => (
            <tr key={e.key}>
              <th scope="row">{e.title}{e.no_baseline ? <span className={usageStyles.empty}> · sem base própria</span> : null}</th>
              <td><span className="mono">{e.action}</span></td>
              <td className={usageStyles.num}>{formatInt(e.samples)}</td>
              <td className={usageStyles.num}>{textoDasChamadas(e.calls)}</td>
              <td className={usageStyles.num}>{textoDoUsd(e.usd)}</td>
              <td className={usageStyles.num}>{textoDoTempo(e.seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
