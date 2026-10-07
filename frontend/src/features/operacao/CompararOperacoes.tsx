import { Info } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Banner } from '../../components/Banner';
import { Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { hashDe } from '../../lib/rotas';
import { useUiStore } from '../../store/ui';
import { apiOperacoes } from './api';
import { NAO_MEDIDO, compararRelatorios, type LinhaComparada } from './comparacao';
import { latenciaDaOperacao } from './latencia';
import { montarRelatorio, type RelatorioDaOperacao } from './relatorio';
import styles from './Operacao.module.css';

/**
 * O relatório de UMA operação para comparar: o do central (v1.111) quando ele o oferece; sem ele, o painel monta o seu do detalhe da
 * operação (sem o aprendizado) e a latência dos carimbos. Quem compara vê de onde veio cada lado.
 */
async function relatorioParaComparar(id: string, signal: AbortSignal): Promise<RelatorioDaOperacao> {
  const doCentral = await apiOperacoes.relatorio(id, signal);
  if (doCentral.situacao === 'central') return doCentral.relatorio;
  const op = await apiOperacoes.detalhe(id, signal);
  const r = montarRelatorio(op, new Date(), { situacao: 'indisponivel', motivo: 'O aprendizado não entra na comparação.' });
  const l = latenciaDaOperacao(op.alvos, op.latencia_por_estagio);
  return {
    ...r,
    latencia: {
      por_estagio: l.porEstagio.map((e) => ({ estagio: e.estagio, rotulo: e.estagio, n: e.agentes, p50_ms: e.medianaMs, p95_ms: e.p95Ms ?? null, max_ms: e.maiorMs })),
      duracao_mediana_ms: l.medianaDoTotalMs, mais_lento: null,
    },
  };
}

function Tabela({ titulo, linhas, a, b }: { titulo: string; linhas: LinhaComparada[]; a: string; b: string }) {
  if (linhas.length === 0) return null;
  return (
    <section aria-label={titulo}>
      <h2 className={styles.subtitulo}>{titulo}</h2>
      <div className={styles.rolagem}>
        <table className={styles.tabela}>
          <thead><tr><th scope="col">Medida</th><th scope="col">{a}</th><th scope="col">{b}</th><th scope="col">B menos A</th></tr></thead>
          <tbody>
            {linhas.map((l) => (
              <tr key={l.rotulo} data-linha={l.rotulo} data-diferente={l.diferente ? 'sim' : 'nao'}>
                <th scope="row" className={styles.persona}>{l.rotulo}</th>
                <td className={l.a === NAO_MEDIDO ? styles.mudo : undefined}>{l.a}</td>
                <td className={l.b === NAO_MEDIDO ? styles.mudo : undefined}>{l.b}</td>
                <td className={styles.numero} data-delta>{l.delta ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** 31.204: `#/operacoes/comparar?a=<id>&b=<id>`: duas operações lado a lado (custo, latência, critérios, identidades, falhas). */
export function CompararOperacoes() {
  const rota = useUiStore((s) => s.rota);
  const idA = rota.query.a ?? '';
  const idB = rota.query.b ?? '';
  const [par, setPar] = useState<{ a: RelatorioDaOperacao; b: RelatorioDaOperacao } | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [vez, setVez] = useState(0);
  useEffect(() => {
    if (!idA || !idB || idA === idB) return;
    const ctl = new AbortController();
    setPar(null);
    setErro(null);
    Promise.all([relatorioParaComparar(idA, ctl.signal), relatorioParaComparar(idB, ctl.signal)])
      .then(([a, b]) => { if (!ctl.signal.aborted) setPar({ a, b }); })
      .catch((e: unknown) => { if (!ctl.signal.aborted) setErro(toLoadError(e)); });
    return () => ctl.abort();
  }, [idA, idB, vez]);
  const c = useMemo(() => (par ? compararRelatorios(par.a, par.b) : null), [par]);
  const voltar = <a className={styles.link} href={hashDe('operacoes')}>← Todas as operações</a>;

  if (!idA || !idB) {
    return <Page title="Comparar operações"><p>{voltar}</p><p className={styles.mudo}>Escolha duas operações na lista para compará-las.</p></Page>;
  }
  if (idA === idB) {
    return <Page title="Comparar operações"><p>{voltar}</p><p className={styles.mudo}>As duas são a mesma operação: escolha duas diferentes.</p></Page>;
  }
  if (erro) return <Page title="Comparar operações"><LoadErrorState what="as operações" error={erro} onRetry={() => setVez((n) => n + 1)} /></Page>;
  if (!par || !c) return <Page title="Comparar operações"><LoadingRegion label="Lendo as duas operações"><Skeleton height={160} /></LoadingRegion></Page>;
  const nomeA = `A: ${par.a.operacao.id}`;
  const nomeB = `B: ${par.b.operacao.id}`;
  const reserva = par.a.fonte === 'painel' || par.b.fonte === 'painel';
  return (
    <Page title="Comparar operações" lead="Duas operações lado a lado, com a diferença onde o número é medido nas duas. O que uma não mediu fica “não medido”, nunca zero.">
      <p>{voltar}</p>
      {reserva ? (
        <Banner tone="info" icon={Info} compact role="status" title="Parte da comparação foi montada no painel">
          O central ainda não entrega o relatório de {par.a.fonte === 'painel' && par.b.fonte === 'painel' ? 'nenhuma das duas' : 'uma das duas'}: os critérios e as identidades só existem no relatório do central.
        </Banner>
      ) : null}
      <Tabela titulo="Resumo" linhas={c.resumo} a={nomeA} b={nomeB} />
      <Tabela titulo="Agentes e identidades" linhas={c.agentes} a={nomeA} b={nomeB} />
      <Tabela titulo="Custo" linhas={c.custo} a={nomeA} b={nomeB} />
      <Tabela titulo="Latência" linhas={c.latencia} a={nomeA} b={nomeB} />
      <Tabela titulo="Tempo por estágio (mediana)" linhas={c.porEstagio} a={nomeA} b={nomeB} />
      <Tabela titulo="Falhas por motivo (agentes)" linhas={c.falhas} a={nomeA} b={nomeB} />
      {c.criterios ? (
        <section aria-label="Critérios do diagnóstico">
          <h2 className={styles.subtitulo}>Critérios do diagnóstico</h2>
          <div className={styles.rolagem}>
            <table className={styles.tabela}>
              <thead><tr><th scope="col">Critério</th><th scope="col">{nomeA}</th><th scope="col">{nomeB}</th></tr></thead>
              <tbody>
                {c.criterios.map((k) => (
                  <tr key={k.id} data-criterio={k.id} data-diferente={k.diferente ? 'sim' : 'nao'}>
                    <th scope="row" className={styles.persona}>{k.id}. {k.nome}</th>
                    <td>{k.a}</td><td>{k.b}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : <p className={styles.mudo} data-sem-criterios>Os critérios só aparecem quando as duas operações têm o relatório do central.</p>}
    </Page>
  );
}
