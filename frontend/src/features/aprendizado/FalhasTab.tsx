import { ClipboardCopy, Flame, RefreshCw, ScanSearch, TrendingDown, TrendingUp } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { money } from '../../lib/aiBalance';
import { copyText, formatDecimal, formatInt, formatPercent } from '../../lib/format';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { toast } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import {
  CAMADAS, type GrupoDeFalha, type RelatorioDeFalhas, mdDoItem, ordenarFalhas, rotuloDaCamada, rotuloDaFalha,
} from './model';
import styles from './Aprendizado.module.css';

const JANELAS = [7, 14, 30] as const;
/** Acima disto o classificador precisa de regra nova (ADR-054, plataforma §5). */
const OUTRO_MAX_PCT = 15;

function Numero({ valor, rotulo }: { valor: string; rotulo: string }) {
  return (
    <div className={styles.numero}>
      <span className={styles.numeroValor}>{valor}</span>
      <span className={styles.numeroRotulo}>{rotulo}</span>
    </div>
  );
}

function Tendencia({ t }: { t: GrupoDeFalha['tendencia'] }) {
  if (!t) return null;
  const sobe = t.atual > t.anterior;
  const Icon = sobe ? TrendingUp : TrendingDown;
  return (
    <Badge tone={sobe ? 'danger' : t.atual < t.anterior ? 'success' : 'neutral'} size="sm" icon={Icon}
           title="Últimos 7 dias contra os 7 anteriores">
      {formatInt(t.anterior)} → {formatInt(t.atual)}
    </Badge>
  );
}

function LinhaDeFalha({ g, posicao }: { g: GrupoDeFalha; posicao: number }) {
  const abrirExecucao = (runId: string) => {
    useUiStore.getState().selectRun(runId);
    useUiStore.getState().setView('execucoes');
  };
  const copiar = async () => {
    const ok = await copyText(mdDoItem(g));
    toast(ok
      ? { tone: 'success', title: `${g.id} copiado`, message: 'Cole na sessão de desenvolvimento: traz onde alterar e como provar.' }
      : { tone: 'danger', title: 'Não foi possível copiar', hint: 'O navegador recusou a área de transferência nesta página.' });
  };
  const onde = g.capability === '*' ? g.app : `${g.app} › ${g.capability}`;
  return (
    <li className={styles.item} data-item={g.id}>
      <div className={styles.itemHead}>
        <span className={styles.posicao} aria-label={`Posição ${posicao}`}>{posicao}</span>
        <span className={styles.itemTitulo}>{rotuloDaFalha(g.failure_kind)}{g.titulo ? ` — ${g.titulo}` : ''}</span>
        {g.falso_positivo ? <Badge tone="danger" size="sm" title="Sucesso mascarado: sempre no topo">falso positivo do verificador</Badge> : null}
        <Badge tone="neutral" size="sm">{rotuloDaCamada(g.camada)}</Badge>
        {g.retroativo ? <Badge tone="muted" size="sm" title="Legado classificado na leitura; nada foi gravado">retroativo</Badge> : null}
        {g.estado_backlog ? <Badge tone="info" size="sm">backlog: {g.estado_backlog}</Badge> : null}
        <Tendencia t={g.tendencia} />
      </div>
      <div className={styles.itemMeta}>
        <span><span className={styles.mono}>{onde}</span>{g.failure_screen ? <> · tela <span className={styles.mono}>{g.failure_screen}</span></> : null}</span>
        <span className={styles.mono}>{g.id}</span>
      </div>
      <div className={styles.numeros}>
        <Numero valor={formatInt(g.ocorrencias)} rotulo={g.taxa !== null ? `ocorrências (${formatPercent(g.taxa * 100)})` : 'ocorrências'} />
        <Numero valor={money(g.usd_perdido, 'USD')} rotulo="US$ perdido" />
        <Numero valor={g.min_perdidos !== null ? formatInt(g.min_perdidos) : '—'} rotulo="minutos perdidos" />
        <Numero valor={g.intervencoes !== null ? formatInt(g.intervencoes) : '—'} rotulo="intervenções humanas" />
        <Numero valor={g.execucoes !== null ? formatInt(g.execucoes) : '—'} rotulo="execuções" />
        <Numero valor={g.aparelhos !== null ? formatInt(g.aparelhos) : '—'} rotulo="aparelhos" />
      </div>
      {g.onde_alterar.length > 0 ? (
        <p className={styles.secaoLead}>
          Onde alterar: {g.onde_alterar.map((a, i) => <span key={a}>{i > 0 ? ', ' : ''}<span className={styles.mono}>{a}</span></span>)}
          {g.prova ? <> · prova: {g.prova}</> : null}
        </p>
      ) : null}
      {g.exemplos.length > 0 ? (
        <ul className={styles.exemplos} aria-label="Exemplos">
          {g.exemplos.map((x) => (
            <li key={`${x.run_id}-${x.attempt_id ?? ''}`}>
              <button type="button" className={styles.linkBtn} onClick={() => abrirExecucao(x.run_id)}>{x.run_id}</button>
              {x.attempt_id ? <span className={styles.mono}> / {x.attempt_id}</span> : null}
              {x.erro ? <> — {x.erro}</> : null}
            </li>
          ))}
        </ul>
      ) : null}
      <div className={styles.itemAcoes}>
        <Button size="sm" variant="outline" icon={ClipboardCopy} onClick={() => void copiar()}>Copiar para sessão</Button>
      </div>
    </li>
  );
}

/**
 * "O que mais falha" (ADR-054, decisão 7): o aprendizado para as sessões de desenvolvimento. Grupo por app, ação,
 * tipo e tela; os três custos (US$, minutos, intervenções) aparecem separados, e o total só ordena. O falso positivo
 * do verificador fica sempre no topo. Nada disto vai à IA.
 */
export function FalhasTab() {
  const [dias, setDias] = useState<number>(14);
  const [camada, setCamada] = useState('');
  const [rel, setRel] = useState<RelatorioDeFalhas | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const vez = useRef(0);

  const carregar = useCallback(async () => {
    const minha = ++vez.current;
    setCarregando(true);
    try {
      const r = await apiAprendizado.falhas({ dias, camada });
      if (minha !== vez.current) return;
      setRel(r);
      setErro(null);
    } catch (e) {
      if (minha === vez.current) setErro(toLoadError(e));
    } finally {
      if (minha === vez.current) setCarregando(false);
    }
  }, [dias, camada]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  const grupos = useMemo(() => ordenarFalhas(rel?.grupos ?? []), [rel]);

  return (
    <section className={styles.secao} aria-label="O que mais falha">
      <div className={styles.toolbar}>
        <Field label="Janela" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={String(dias)} onChange={(e) => setDias(Number(e.target.value))}>
              {JANELAS.map((d) => <option key={d} value={d}>{d} dias</option>)}
            </Select>
          )}
        </Field>
        <Field label="Camada" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={camada} onChange={(e) => setCamada(e.target.value)}>
              <option value="">Todas</option>
              {CAMADAS.map((c) => <option key={c} value={c}>{rotuloDaCamada(c)}</option>)}
            </Select>
          )}
        </Field>
        <div className={styles.toolbarFim}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Atualizar</Button>
        </div>
      </div>

      {rel && rel.outro_pct !== null && rel.outro_pct > OUTRO_MAX_PCT ? (
        <Banner tone="warning" icon={ScanSearch} compact role="status" title={`${formatDecimal(rel.outro_pct)}% das falhas ainda sem tipo definido`}>
          Acima de {OUTRO_MAX_PCT}% o agrupamento de falhas precisa de uma regra nova (tarefa para o desenvolvimento).
        </Banner>
      ) : null}
      {erro && rel ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
      {!rel ? (
        erro ? <LoadErrorState what="o que mais falha" error={erro} onRetry={() => void carregar()} /> : (
          <LoadingRegion label="Agrupando as falhas…" className={styles.secao}>
            <Skeleton height={96} radius={8} />
            <Skeleton height={96} radius={8} />
          </LoadingRegion>
        )
      ) : grupos.length === 0 ? (
        <EmptyState icon={Flame} compact title="Nenhum grupo de falha nesta janela">
          Um grupo só entra com pelo menos 3 ocorrências; execuções simuladas ficam fora.
        </EmptyState>
      ) : (
        <ol className={styles.lista} aria-label="Falhas por custo">
          {grupos.map((g, i) => <LinhaDeFalha key={g.id} g={g} posicao={i + 1} />)}
        </ol>
      )}
    </section>
  );
}
