import { FlaskConical } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt, plural } from '../../lib/format';
import { LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { formatQuando } from '../../lib/time';
import { apiRendimentoDoEnsino, type PorUso, type RendimentoDoEnsino } from './contratoDoRendimento';
import styles from './Training.module.css';

/**
 * 31.203: o que esta sessão de ensino gerou e quanto disso foi usado (`GET /api/training/{id}/rendimento`, adendo v1.101). Só leitura, sem
 * IA. O uso real, a prova e a execução simulada ficam em colunas separadas e nunca se somam: só o uso real diz que o ensino serviu.
 */

const SEM_DADO = 'sem dado';
const n = (v: number | null): string => (v === null ? SEM_DADO : formatInt(v));
const usd = (v: number | null): string => (v === null ? SEM_DADO : `US$ ${v < 1 ? v.toFixed(4) : v.toFixed(2)}`);
const sim = (v: boolean | null): string => (v === null ? 'não informado' : v ? 'sim' : 'não');

function Linha({ rotulo, u, dado }: { rotulo: string; u: PorUso; dado: string }) {
  return (
    <tr data-linha={dado}>
      <th scope="row">{rotulo}</th>
      <td data-uso="real">{n(u.real)}</td><td data-uso="prova">{n(u.prova)}</td><td data-uso="simulada">{n(u.simulada)}</td>
    </tr>
  );
}

const CABECA = <thead><tr><th scope="col">O quê</th><th scope="col">Uso real</th><th scope="col">Prova</th><th scope="col">Simulada</th></tr></thead>;

export function RendimentoDoEnsinoView({ r }: { r: RendimentoDoEnsino }) {
  const rs = r.resumo;
  return (
    <div className={styles.rendimento}>
      {r.exemplo ? (
        <Banner tone="info" icon={FlaskConical} compact role="status" title="Dados de exemplo">
          O central ainda não oferece o rendimento do ensino: estes números são um exemplo inventado. Nada aqui veio do parque.
        </Banner>
      ) : null}
      <p data-usado role="status">
        {rs.usadoDeVerdade === null ? 'O central não informou se este ensino já foi usado de verdade.'
          : rs.usadoDeVerdade ? <><Badge tone="success" size="sm">Usado de verdade</Badge> Este ensino já serviu em execução real.</>
            : <><Badge tone="warning" size="sm">Ainda sem uso real</Badge> Só prova e simulação usaram o que esta sessão gerou.</>}
      </p>
      <p className={styles.hint}>
        {rs.receitas === null ? '' : `${plural(rs.receitas, 'receita', 'receitas')}${rs.receitasLiberadas === null ? '' : `, ${rs.receitasLiberadas} liberada${rs.receitasLiberadas === 1 ? '' : 's'} fora de quem ensinou`}`}
        {rs.licoes === null ? '' : ` · ${plural(rs.licoes, 'lição', 'lições')}`}{rs.vizinhos === null ? '' : ` · ${plural(rs.vizinhos, 'pacote vizinho', 'pacotes vizinhos')}`}.
      </p>
      {r.fluxo ? (
        <p data-fluxo>
          Fluxo <span className="mono">{r.fluxo.id ?? 'sem id'}</span> · {r.fluxo.status ?? 'estado não informado'} · {r.fluxo.uses === null ? 'usos não informados' : plural(r.fluxo.uses, 'uso', 'usos')}
          {r.fluxo.nascidoDeProva ? ' · nascido de prova' : ''}
          {r.fluxo.emUsoRealDesde ? ` · em uso real desde ${formatQuando(r.fluxo.emUsoRealDesde)}` : r.fluxo.emUsoRealDesde === null ? ' · sem uso real ainda' : ''}
        </p>
      ) : <p className={styles.hint} data-fluxo>Esta sessão não gerou fluxo.</p>}
      <table className={styles.tabelaRendimento}>
        <caption>Uso do que a sessão gerou (prova e simulada não contam como uso real)</caption>
        {CABECA}
        <tbody>
          <Linha dado="execucoes_do_fluxo" rotulo="Execuções do fluxo" u={r.execucoesDoFluxo} />
          <Linha dado="etapas_sem_ia" rotulo="Etapas sem IA (receitas)" u={rs.etapasSemIa} />
          {r.vizinhos.map((v) => <Linha key={v.pacote} dado={`vizinho:${v.pacote}`} rotulo={`Vizinho ${v.pacote}: etapas de planos livres`} u={v.etapasEmPlanosLivres} />)}
        </tbody>
      </table>
      {r.receitas.length ? (
        <table className={styles.tabelaRendimento} aria-label="Receitas da sessão">
          <caption>Receitas</caption>
          <thead><tr><th scope="col">Receita</th><th scope="col">Liberada</th><th scope="col">Sem IA (real/prova/sim.)</th><th scope="col">Caiu na IA (real/prova/sim.)</th><th scope="col">IA gasta</th></tr></thead>
          <tbody>
            {r.receitas.map((x, i) => (
              <tr key={x.id ?? i} data-receita={x.id ?? i}>
                <th scope="row"><span className="mono">{x.stepKey ?? `nº ${x.id ?? '?'}`}</span>{x.app ? <span className={styles.muted}> · {x.app}</span> : null}</th>
                <td>{sim(x.liberada)}</td>
                <td data-sem-ia>{n(x.semIa.real)} / {n(x.semIa.prova)} / {n(x.semIa.simulada)}</td>
                <td data-caiu-na-ia>{n(x.caiuNaIa.real)} / {n(x.caiuNaIa.prova)} / {n(x.caiuNaIa.simulada)}</td>
                <td>{usd(x.usdDaIaNaRetencao)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className={styles.hint}>Nenhuma receita saiu desta sessão.</p>}
      {r.licoes.length ? (
        <ul className={styles.entryList} aria-label="Lições da sessão">
          {r.licoes.map((l) => <li key={l.id} data-licao={l.id}><span className={styles.muted}>{[l.estado, l.papel].filter(Boolean).join(' · ') || 'lição'}: </span>{l.texto ?? 'sem texto'}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

export function RendimentoDoEnsinoSecao({ sessionId }: { sessionId: string }) {
  const [dado, setDado] = useState<RendimentoDoEnsino | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [vez, setVez] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setDado(null);
    setErro(null);
    apiRendimentoDoEnsino.daSessao(sessionId, ctl.signal).then((d) => { if (!ctl.signal.aborted) setDado(d); })
      .catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toLoadError(e)); });
    return () => ctl.abort();
  }, [sessionId, vez]);
  const tentar = useCallback(() => setVez((x) => x + 1), []);
  if (erro) return <LoadErrorState what="o rendimento do ensino" error={erro} onRetry={tentar} compact />;
  if (!dado) return <LoadingRegion label="Lendo o rendimento"><Skeleton height={48} /></LoadingRegion>;
  return <RendimentoDoEnsinoView r={dado} />;
}
