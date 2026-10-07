import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Badge } from '../../components/Badge';
import { formatInt, formatUsd4, plural } from '../../lib/format';
import { intervaloVisivel } from '../../lib/polling';
import { hashDe, type Tela } from '../../lib/rotas';
import { metaDaSessao } from '../../lib/status';
import { formatQuando } from '../../lib/time';
import { apiPedidos } from '../pedidos/api';
import { useAppStore } from '../../store/app';
import { ROTULO_DO_ESTADO } from '../../store/metricas';
import { apiOperacoes } from '../operacao/api';
import { ROTULO_DO_STATUS, type Operacao, type ResumoDaOperacao } from '../operacao/modelo';
import { usePersonas } from '../profiles/usePersonas';
import { useUsage } from '../usage/useUsage';
import { precisaDePessoa } from '../pendencias/modelo';
import { aparelhosDeContaReal, custoDasOperacoes, operacoesDoDia, perguntasAbertas, ultimoDeploy } from './contasDoResumo';
import styles from './ResumoDoDia.module.css';

/**
 * 31.215: o resumo do dia, no alto da página inicial. Só o que as telas já leem (nenhuma rota nova): as operações em curso e do dia, os
 * aparelhos de conta real com o estado, o custo de IA do dia, as perguntas abertas ao dono e o que está no ar. Cada bloco leva à tela
 * dona do assunto; o que não foi lido diz "não lido", nunca zero. Só leitura.
 */

export const PERIODO_DO_RESUMO_MS = 60_000;
/** Das operações do dia, quantas têm o detalhe lido para somar o custo (o resumo da lista não traz custo). */
export const OPERACOES_COM_CUSTO = 6;

interface LeituraDasOperacoes {
  situacao: 'lendo' | 'sem_modulo' | 'erro' | 'ok';
  itens: ResumoDaOperacao[];
  lidas: Operacao[];
  /** Quantas do dia ficaram sem detalhe lido (além do limite ou com erro). */
  semLeitura: number;
}

function useOperacoes(): LeituraDasOperacoes {
  const [leitura, setLeitura] = useState<LeituraDasOperacoes>({ situacao: 'lendo', itens: [], lidas: [], semLeitura: 0 });
  useEffect(() => {
    const ctl = new AbortController();
    const detalhes = new Map<string, Operacao>();          // a operação encerrada não muda: lê uma vez; a em curso, a cada minuto
    const ler = async (): Promise<void> => {
      try {
        const lista = await apiOperacoes.lista(ctl.signal);
        if (lista.exemplo) { setLeitura({ situacao: 'sem_modulo', itens: [], lidas: [], semLeitura: 0 }); return; }
        const doDia = operacoesDoDia(lista.itens, Date.now()).doDia;
        const alvo = doDia.slice(0, OPERACOES_COM_CUSTO);
        const lidas: Operacao[] = [];
        let falhas = 0;
        await Promise.all(alvo.map(async (o) => {
          const guardada = detalhes.get(o.id);
          if (guardada && o.status !== 'em_curso') { lidas.push(guardada); return; }
          try {
            const op = await apiOperacoes.detalhe(o.id, ctl.signal);
            detalhes.set(o.id, op);
            lidas.push(op);
          } catch { if (!ctl.signal.aborted) falhas += 1; }
        }));
        if (!ctl.signal.aborted) setLeitura({ situacao: 'ok', itens: lista.itens, lidas, semLeitura: doDia.length - alvo.length + falhas });
      } catch { if (!ctl.signal.aborted) setLeitura((l) => ({ ...l, situacao: 'erro' })); }
    };
    void ler();
    const desligar = intervaloVisivel(() => void ler(), PERIODO_DO_RESUMO_MS);
    return () => { ctl.abort(); desligar(); };
  }, []);
  return leitura;
}

function Bloco({ titulo, tela, children, dataBloco, rotuloDoLink }: { titulo: string; tela: Tela; children: ReactNode; dataBloco: string; rotuloDoLink: string }) {
  return (
    <section className={styles.bloco} aria-label={titulo} data-bloco={dataBloco}>
      <h3 className={styles.titulo}>{titulo}</h3>
      <div className={styles.corpo}>{children}</div>
      <a className={styles.link} href={hashDe(tela)}>{rotuloDoLink}</a>
    </section>
  );
}

const Mudo = ({ children }: { children: ReactNode }) => <p className={styles.mudo}>{children}</p>;

function BlocoDeOperacoes({ l }: { l: LeituraDasOperacoes }) {
  const r = useMemo(() => operacoesDoDia(l.itens, Date.now()), [l.itens]);
  const custo = useMemo(() => custoDasOperacoes(l.lidas), [l.lidas]);
  return (
    <Bloco titulo="Operações" tela="operacoes" dataBloco="operacoes" rotuloDoLink="Abrir as operações">
      {l.situacao === 'lendo' ? <Mudo>Lendo…</Mudo> : null}
      {l.situacao === 'sem_modulo' ? <Mudo>O central ainda não oferece o módulo de operações.</Mudo> : null}
      {l.situacao === 'erro' ? <Mudo>Não lido: erro ao ler as operações.</Mudo> : null}
      {l.situacao === 'ok' ? (
        <>
          <p className={styles.numero} data-em-curso><strong>{formatInt(r.emCurso)}</strong> {r.emCurso === 1 ? 'em andamento' : 'em andamento'}</p>
          <p data-do-dia>{plural(r.doDia.length, 'operação criada hoje', 'operações criadas hoje')}</p>
          {r.doDia.length > 0 ? (
            <p className={styles.mudo} data-capacidade>
              {r.solicitados === null ? 'Capacidade não informada' : `${formatInt(r.solicitados)} solicitados · ${r.concluidas === null ? '—' : formatInt(r.concluidas)} concluídas · ${r.bloqueadas === null ? '—' : formatInt(r.bloqueadas)} bloqueadas`}
            </p>
          ) : null}
          {r.doDia.length > 0 ? (
            <p className={styles.mudo} data-custo-das-operacoes>
              Custo de IA {custo.totalUsd === null ? 'não informado' : formatUsd4(custo.totalUsd)}
              {l.semLeitura > 0 || custo.semCusto > 0 ? ` (${l.semLeitura > 0 ? `${plural(l.semLeitura, 'operação sem leitura', 'operações sem leitura')}` : ''}${l.semLeitura > 0 && custo.semCusto > 0 ? '; ' : ''}${custo.semCusto > 0 ? `${plural(custo.semCusto, 'sem custo informado', 'sem custo informado')}` : ''} fora da soma)` : ''}
            </p>
          ) : null}
          {r.doDia[0] ? (
            <p className={styles.mudo}>
              Mais recente: <a href={hashDe('operacoes', { segmentos: [r.doDia[0].id] })}>{r.doDia[0].command || r.doDia[0].id}</a>
              {r.doDia[0].status ? ` · ${ROTULO_DO_STATUS[r.doDia[0].status]}` : ''}
            </p>
          ) : null}
        </>
      ) : null}
    </Bloco>
  );
}

function BlocoDeAparelhos() {
  const personas = usePersonas();
  const instances = useAppStore((s) => s.instances);
  const workers = useAppStore((s) => s.workers);
  const linhas = useMemo(() => aparelhosDeContaReal(personas ?? [], instances, workers), [personas, instances, workers]);
  return (
    <Bloco titulo="Aparelhos de conta real" tela="infraestrutura" dataBloco="aparelhos" rotuloDoLink="Abrir a infraestrutura">
      {personas === null ? <Mudo>Lendo…</Mudo> : null}
      {personas !== null && linhas.length === 0 ? <Mudo>Nenhum aparelho com conta de um app real.</Mudo> : null}
      {linhas.length > 0 ? (
        <ul className={styles.lista} aria-label="Aparelhos de conta real">
          {linhas.slice(0, 8).map((a) => {
            const meta = metaDaSessao(a.sessao);
            return (
              <li key={`${a.instanceId}|${a.appId}`} data-aparelho={a.instanceId}>
                <strong>{a.instanceId}</strong> <span className={styles.mudo}>{a.appId}</span>{' · '}
                <span data-estado>{a.estado ? ROTULO_DO_ESTADO[a.estado][0] : 'estado não informado'}</span>{' · '}
                {a.sessao ? <Badge tone={meta.tone} size="sm">{meta.label}</Badge> : <span className={styles.mudo}>sessão não informada</span>}
                {a.sessao && precisaDePessoa(a.sessao) ? <span data-precisa-de-pessoa className="sr-only"> espera uma pessoa</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {linhas.length > 8 ? <Mudo>E mais {formatInt(linhas.length - 8)} na infraestrutura.</Mudo> : null}
    </Bloco>
  );
}

function BlocoDeCusto() {
  const { report, error, loading } = useUsage({ days: 1 });
  return (
    <Bloco titulo="Custo de IA do dia" tela="diagnostico" dataBloco="custo" rotuloDoLink="Abrir o diagnóstico">
      {loading && !report ? <Mudo>Lendo…</Mudo> : null}
      {error && !report ? <Mudo>Não lido: {error.message}</Mudo> : null}
      {report ? <p className={styles.numero} data-custo-do-dia><strong>{formatUsd4(report.total_usd)}</strong> nas últimas 24 h</p> : null}
    </Bloco>
  );
}

function BlocoDeDeploy() {
  const health = useAppStore((s) => s.health);
  const d = ultimoDeploy(health);
  return (
    <Bloco titulo="No ar" tela="diagnostico" dataBloco="deploy" rotuloDoLink="Abrir o diagnóstico">
      {d === null ? <Mudo>Saúde ainda não lida.</Mudo> : (
        <>
          <p data-deploy><strong>{d.commit ?? 'commit não informado'}</strong>{d.versao ? ` · versão ${d.versao}` : ''}{d.migracao ? ` · migração ${d.migracao}` : ''}</p>
          <Mudo>A saúde não informa a hora do deploy.</Mudo>
        </>
      )}
    </Bloco>
  );
}

function BlocoDePerguntas() {
  const [n, setN] = useState<number | null | undefined>(undefined);        // undefined = lendo; null = sem leitura
  useEffect(() => {
    const ctl = new AbortController();
    const ler = (): void => {
      apiPedidos.avisos({ requer_pessoa: 1, lido: 0, limit: 100 }, ctl.signal)
        .then((r) => { if (!ctl.signal.aborted) setN(perguntasAbertas(Array.isArray(r?.items) ? r.items : null)); })
        .catch(() => { if (!ctl.signal.aborted) setN(null); });
    };
    ler();
    const desligar = intervaloVisivel(ler, PERIODO_DO_RESUMO_MS);
    return () => { ctl.abort(); desligar(); };
  }, []);
  if (n === null || n === undefined) return null;                            // sem a leitura, o bloco some (não mostra zero)
  return (
    <Bloco titulo="Perguntas ao dono" tela="pendencias" dataBloco="perguntas" rotuloDoLink="Abrir as pendências">
      <p className={styles.numero} data-perguntas><strong>{formatInt(n)}</strong> {n === 1 ? 'pergunta aberta' : 'perguntas abertas'}</p>
      {n === 0 ? <Mudo>Nada espera uma resposta sua.</Mudo> : null}
    </Bloco>
  );
}

export function ResumoDoDia() {
  const operacoes = useOperacoes();
  return (
    <section className={styles.resumo} aria-label="Resumo do dia" data-resumo-do-dia>
      <h2 className={styles.cabecalho}>Resumo do dia <span className={styles.mudo}>({formatQuando(new Date().toISOString())})</span></h2>
      <div className={styles.grade}>
        <BlocoDeOperacoes l={operacoes} />
        <BlocoDeAparelhos />
        <BlocoDeCusto />
        <BlocoDePerguntas />
        <BlocoDeDeploy />
      </div>
    </section>
  );
}
