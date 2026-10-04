import { Bot, CheckCircle2, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { cx } from '../../lib/format';
import { formatDateTime, tempoRelativo, useNow } from '../../lib/time';
import { DecisaoInline } from '../aprendizado/DecisaoInline';
import {
  ROTULO_DA_FILA, ROTULO_DO_PERIODO, apiDecididas, desdeDoPeriodo, falhaDoDesfazer, fatosEmPortugues, motivoDaRegra,
  type Decidida, type ListaDecidida, type PeriodoDecidido,
} from './decididas';
import styles from './Pendencias.module.css';

const PERIODOS: readonly PeriodoDecidido[] = ['hoje', '7d', 'tudo'];

/**
 * A aba "Decidido sozinho": o que a plataforma decidiu no lugar do dono (28.25), com a regra que decidiu e o desfazer.
 * Desfazer é um gesto com efeito na fila dona, então pede o motivo em linha (o mesmo `DecisaoInline` do Aprendizado,
 * nunca modal) e só vale dentro do prazo; sem volta segura, a linha diz o porquê no lugar do botão.
 */
export function DecididoSozinho() {
  const agora = useNow();
  const [periodo, setPeriodo] = useState<PeriodoDecidido>('7d');
  const [regra, setRegra] = useState('');
  const [comDesfeitas, setComDesfeitas] = useState(true);
  const [lista, setLista] = useState<ListaDecidida | null>(null);
  const [falhou, setFalhou] = useState(false);
  const [abrindo, setAbrindo] = useState<number | null>(null);

  const carregar = useCallback(async (sinal?: AbortSignal) => {
    try {
      setLista(await apiDecididas.listar({ regra: regra || undefined, desde: desdeDoPeriodo(periodo, Date.now()),
                                           desfeitas: comDesfeitas ? 'todas' : 'nao' }, sinal));
      setFalhou(false);
    } catch {
      if (!sinal?.aborted) setFalhou(true);
    }
  }, [periodo, regra, comDesfeitas]);

  useEffect(() => {
    const c = new AbortController();
    void carregar(c.signal);
    return () => c.abort();
  }, [carregar]);

  const desfazer = async (d: Decidida, motivo: string): Promise<string | null> => {
    try {
      await apiDecididas.desfazer(d.id, motivo);
    } catch (e) {
      return falhaDoDesfazer(e);
    }
    setAbrindo(null);
    await carregar();
    return null;
  };

  return (
    <div className={styles.decididas}>
      <p className={styles.nota}>
        O que a plataforma resolveu por você, com a regra que decidiu. Dá para desfazer dentro de {lista?.desfazer_dias ?? 7} dias,
        quando a fila de origem tem uma volta segura. Com os avisos do Telegram ligados, você recebe lá um resumo por janela,
        nunca um aviso por decisão.
      </p>

      <div className={styles.filtros}>
        <div className={styles.chips} role="radiogroup" aria-label="Período">
          {PERIODOS.map((p) => (
            <button key={p} type="button" role="radio" aria-checked={periodo === p}
                    className={cx(styles.chip, periodo === p && styles.chipOn)} onClick={() => setPeriodo(p)}>
              {ROTULO_DO_PERIODO[p]}
            </button>
          ))}
        </div>
        <label className={styles.filtro}>
          <span>Regra</span>
          <select value={regra} onChange={(e) => setRegra(e.target.value)} aria-label="Filtrar por regra">
            <option value="">Todas as regras</option>
            {(lista?.regras ?? []).map((r) => <option key={r} value={r}>{motivoDaRegra(r)}</option>)}
          </select>
        </label>
        <label className={styles.filtro}>
          <input type="checkbox" checked={comDesfeitas} onChange={(e) => setComDesfeitas(e.target.checked)} />
          <span>Mostrar as já desfeitas</span>
        </label>
      </div>

      {falhou ? <Banner tone="warning" icon={TriangleAlert} compact role="status">Não foi possível ler o que foi decidido agora. Tente de novo em instantes.</Banner> : null}

      {lista === null && !falhou ? (
        <LoadingRegion label="Carregando as decisões…"><Skeleton height={64} radius={8} /><Skeleton height={64} radius={8} /></LoadingRegion>
      ) : lista && lista.itens.length === 0 ? (
        <EmptyState icon={CheckCircle2} title="Nada decidido sozinho neste período"
                    hint="Quando a plataforma resolver algo por você, aparece aqui com a regra e o desfazer." />
      ) : lista ? (
        <ul className={styles.lista} aria-label="Decidido sozinho">
          {lista.itens.map((d) => (
            <li key={d.id} className={cx(styles.decidida, d.desfeita && styles.desfeita)} data-decidida={d.id}>
              <div className={styles.topo}>
                <Badge size="sm" tone="accent"><Bot size={11} aria-hidden /> {ROTULO_DA_FILA[d.fila]}</Badge>
                <span className={styles.idade} title={formatDateTime(d.decidida_em)}>decidido {tempoRelativo(d.decidida_em, agora)}</span>
              </div>
              <strong className={styles.titulo}>{d.efeito}</strong>
              <span className={styles.detalhe}>Por quê: {motivoDaRegra(d.regra)}</span>
              {fatosEmPortugues(d.fatos).length > 0 ? (
                <span className={styles.detalhe}>{fatosEmPortugues(d.fatos).join(' · ')}</span>
              ) : null}
              {d.desfeita ? (
                <span className={styles.detalhe} data-desfeita>
                  Desfeita {tempoRelativo(d.desfeita_em, agora)}{d.desfeita_por ? ` por ${d.desfeita_por}` : ''}
                  {d.motivo_do_desfazer ? `: ${d.motivo_do_desfazer}` : ''}
                </span>
              ) : abrindo === d.id ? (
                <DecisaoInline acao={{ confirmar: `${d.acao_do_desfazer} esta decisão`, perigo: true }} motivoOpcional
                               dica={`Fica registrado com o seu nome. Vale até ${formatDateTime(d.prazo_ate)}.`}
                               onConfirmar={(m) => desfazer(d, m)} onCancelar={() => setAbrindo(null)} />
              ) : d.pode_desfazer ? (
                <div><Button size="sm" variant="secondary" onClick={() => setAbrindo(d.id)}>{d.acao_do_desfazer}</Button></div>
              ) : (
                <span className={styles.semVolta} data-sem-volta>{d.por_que_nao ?? 'Não dá para desfazer esta decisão.'}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
