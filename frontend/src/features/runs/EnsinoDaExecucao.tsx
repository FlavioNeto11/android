import { CircleCheck, GraduationCap, RefreshCw, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '../../api/client';
import type { RunSummary } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { plural } from '../../lib/format';
import { type LoadError, LoadErrorBanner, toLoadError } from '../../lib/loadError';
import { isRunTerminal } from '../../lib/status';
import { toast } from '../../store/toasts';
import { apiAprendizado } from '../aprendizado/api';
import styles from './EnsinoDaExecucao.module.css';
import { type EnsinoDaExecucao as Ensino, motivoEmPalavras, receitaEmPalavras, rotuloDaReceita } from './ensinoLido';

type RunRef = Pick<RunSummary, 'id' | 'status'>;

/**
 * 31.226: "Ensinar a partir desta execução" na tela da execução. Por etapa, a receita candidata que nasceu dela ou o motivo de não ter
 * nascido (v1.120, 31.221); o botão promove as candidatas ensináveis pelo Livro (candidate → validated → published) e a tela diz,
 * por receita, o que foi promovido e o que foi recusado. Só aparece com a execução terminada; o central anterior (sem a rota) deixa só
 * uma linha dizendo isso.
 */
export function EnsinoDaExecucao({ run }: { run: RunRef }) {
  const terminal = isRunTerminal(run.status);
  const [ensino, setEnsino] = useState<Ensino | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [semModulo, setSemModulo] = useState(false);
  const [carregando, setCarregando] = useState(false);
  const [ensinando, setEnsinando] = useState(false);
  const [recusa, setRecusa] = useState<LoadError | null>(null);
  const [vez, setVez] = useState(0);

  useEffect(() => {
    if (!terminal) return undefined;
    const ctl = new AbortController();
    setCarregando(true);
    setErro(null);
    apiAprendizado.ensinoDaExecucao(run.id, ctl.signal)
      .then((r) => { if (!ctl.signal.aborted) { setEnsino(r); setSemModulo(false); } })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        if (e instanceof ApiError && e.status === 404 && e.code !== 'execucao_desconhecida') setSemModulo(true);
        else setErro(toLoadError(e));
      })
      .finally(() => { if (!ctl.signal.aborted) setCarregando(false); });
    return () => ctl.abort();
  }, [run.id, terminal, vez]);

  const ensinar = useCallback(async () => {
    if (!ensino || ensinando) return;
    const n = ensino.ensinaveis ?? 0;
    const r = await confirm({
      title: 'Ensinar a partir desta execução?', confirmLabel: 'Ensinar', cancelLabel: 'Voltar',
      body: (
        <div data-confirma-ensino>
          <p>{plural(n, 'receita candidata', 'receitas candidatas')} {n === 1 ? 'vai' : 'vão'} passar por candidata → validada → publicada pelo Livro.</p>
          <p>Depois disso a IA passa a usar {n === 1 ? 'essa receita' : 'essas receitas'} nas próximas execuções. O motivo fica registrado como ensino desta execução.</p>
        </div>
      ),
    });
    if (!r.confirmed) return;
    setEnsinando(true);
    setRecusa(null);
    try {
      const depois = await apiAprendizado.ensinarDaExecucao(run.id);
      setEnsino(depois);
      const feitas = depois.promovidas?.length ?? 0;
      toast({ tone: feitas > 0 ? 'success' : 'info', title: feitas > 0 ? 'Ensino feito' : 'Nada foi promovido', message: `${plural(feitas, 'receita promovida', 'receitas promovidas')}.` });
    } catch (e) {
      setRecusa(toLoadError(e));
    } finally {
      setEnsinando(false);
    }
  }, [ensino, ensinando, run.id]);

  if (!terminal) return null;
  if (semModulo) return <p className={styles.mudo} data-ensino-sem-modulo>O central ainda não oferece o ensino a partir da execução.</p>;
  if (carregando && !ensino) return <LoadingRegion label="Lendo o que esta execução pode ensinar"><Skeleton height={64} /></LoadingRegion>;
  if (erro && !ensino) return <LoadErrorBanner error={erro} onRetry={() => setVez((v) => v + 1)} />;
  if (!ensino) return null;

  const n = ensino.ensinaveis ?? ensino.etapas.filter((e) => e.ensinavel).length;
  const semDados = ensino.etapas.length === 0;
  const motivoSemEnsinar = ensino.simulada === true ? 'A execução foi simulada: não há o que ensinar a partir dela.'
    : n === 0 ? 'Nenhuma etapa desta execução está pronta para ensinar.' : null;

  return (
    <section className={styles.secao} aria-label="Ensinar a partir desta execução" data-ensino-da-execucao>
      <div className={styles.cabecalho}>
        <h3 className={styles.titulo}><GraduationCap size={14} aria-hidden /> Ensinar a partir desta execução</h3>
        <span className={styles.mudo}>
          {semDados ? 'Sem etapas para olhar.' : `${plural(n, 'etapa pronta', 'etapas prontas')} para ensinar, de ${ensino.etapas.length}.`}
        </span>
        <span className={styles.acoes}>
          <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => setVez((v) => v + 1)} loading={carregando}>Atualizar</Button>
          <Button size="sm" variant="primary" icon={GraduationCap} loading={ensinando} disabledReason={motivoSemEnsinar} onClick={() => void ensinar()}>
            Ensinar a partir da execução
          </Button>
        </span>
      </div>
      {erro ? <LoadErrorBanner error={erro} onRetry={() => setVez((v) => v + 1)} /> : null}
      {recusa ? <LoadErrorBanner error={recusa} /> : null}
      {ensino.promovidas || ensino.recusadas ? <ResultadoDoEnsino ensino={ensino} /> : null}
      {semDados ? null : (
        <ul className={styles.lista} aria-label="Etapas e receitas">
          {ensino.etapas.map((e) => {
            const motivo = motivoEmPalavras(e);
            return (
              <li key={e.stepId} className={styles.etapa} data-etapa={e.chave} data-ensinavel={e.ensinavel ? 'sim' : 'nao'}>
                <span className="mono">{e.chave}</span>
                <span className={styles.mudo}>{[e.capacidade, e.status, e.drivenBy].filter(Boolean).join(' · ') || 'sem detalhe'}</span>
                {e.receita ? (
                  <span data-receita={e.receita.id}>
                    <a href={`#/aprendizado?aba=aprendido&item=${encodeURIComponent(`receita:${e.receita.id}`)}`}>{receitaEmPalavras(e.receita)}</a>
                  </span>
                ) : null}
                {e.ensinavel ? <strong data-pronta>Pronta para ensinar.</strong> : null}
                {motivo ? <span className={styles.mudo} data-motivo={e.motivo ?? 'desconhecido'}>{motivo}</span> : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function ResultadoDoEnsino({ ensino }: { ensino: Ensino }) {
  const promovidas = ensino.promovidas ?? [];
  const recusadas = ensino.recusadas ?? [];
  const nada = promovidas.length === 0 && recusadas.length === 0;
  return (
    <Banner tone={recusadas.length > 0 ? 'warning' : 'success'} icon={recusadas.length > 0 ? TriangleAlert : CircleCheck} compact role="status" title={nada ? 'Nenhuma receita candidata para promover' : 'Resultado do ensino'}>
      <div data-resultado-do-ensino>
        {promovidas.length > 0 ? (
          <ul aria-label="Promovidas">
            {promovidas.map((p) => {
              const r = ensino.etapas.find((e) => e.receita?.id === p.receitaId)?.receita ?? null;
              return <li key={`${p.receitaId}:${p.etapa}`} data-promovida={p.receitaId}>Promovida: receita {p.receitaId}{p.etapa ? ` (etapa ${p.etapa})` : ''}{r ? `, agora ${rotuloDaReceita(r.status)}` : ''}.</li>;
            })}
          </ul>
        ) : null}
        {recusadas.length > 0 ? (
          <ul aria-label="Recusadas">
            {recusadas.map((p) => (
              <li key={`${p.receitaId}:${p.etapa}`} data-recusada={p.receitaId}>
                Recusada: receita {p.receitaId}{p.etapa ? ` (etapa ${p.etapa})` : ''}: {p.mensagem ?? 'o servidor não disse o motivo'}{p.codigo ? ` [${p.codigo}]` : ''}.
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Banner>
  );
}
