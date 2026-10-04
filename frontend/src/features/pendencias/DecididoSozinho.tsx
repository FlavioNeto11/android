import { Bot, CheckCircle2, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { cx } from '../../lib/format';
import { hashDe } from '../../lib/rotas';
import { formatDateTime, formatQuando, tempoRelativo, useNow } from '../../lib/time';
import { DecisaoInline } from '../aprendizado/DecisaoInline';
import { rotuloDaExecucao } from '../aprendizado/detalhe';
import { hrefDoItem } from '../aprendizado/DetalheRico';
import { isLivroKind, rotuloDoKind } from '../aprendizado/model';
import {
  MOTIVO_DO_DESFAZER_MAX, ROTULO_DA_FILA, ROTULO_DO_PERIODO, apiDecididas, desdeDoPeriodo, eDoVencimento, falhaDoDesfazer,
  fatosEmPortugues, itemDoAprendizado, motivoDaRegra, quemDesfez, tituloDoGrupo,
  type Decidida, type FilaDecidida, type ListaDecidida, type PeriodoDecidido,
} from './decididas';
import styles from './Pendencias.module.css';

const PERIODOS: readonly PeriodoDecidido[] = ['hoje', '7d', 'tudo'];

/** Um grupo do vencimento (fila + regra) ou uma decisão avulsa; a ordem da lista é a da primeira decisão de cada bloco. */
type Bloco =
  | { tipo: 'grupo'; chave: string; fila: FilaDecidida; regra: string; itens: Decidida[] }
  | { tipo: 'avulsa'; d: Decidida };

/**
 * As decisões do vencimento (31.43) chegam às dezenas e sem volta: um cartão cada afoga o que o dono ainda decide.
 * Só agrupa o que não tem desfazer nem foi desfeito; o resto segue como cartão, com o botão e o motivo.
 */
function emBlocos(itens: Decidida[]): Bloco[] {
  const blocos: Bloco[] = [];
  const grupos = new Map<string, Extract<Bloco, { tipo: 'grupo' }>>();
  for (const d of itens) {
    if (!eDoVencimento(d) || d.pode_desfazer || d.desfeita) { blocos.push({ tipo: 'avulsa', d }); continue; }
    const chave = `${d.fila}|${d.regra}`;
    let g = grupos.get(chave);
    if (!g) { g = { tipo: 'grupo', chave, fila: d.fila, regra: d.regra, itens: [] }; grupos.set(chave, g); blocos.push(g); }
    g.itens.push(d);
  }
  return blocos;
}

/** O nome do item do aprendizado (ou "Receita 180") com o link para ele no Livro, quando o tipo é conhecido. */
function TituloDoItem({ d }: { d: Decidida }) {
  const { kind, ref } = itemDoAprendizado(d);
  const nome = d.item_nome ?? (kind ? `${rotuloDoKind(kind)} ${ref}` : d.item_ref);
  return kind && isLivroKind(kind)
    ? <a className={styles.linkDoItem} href={hrefDoItem(kind, ref)} data-item-do-livro>{nome}</a>
    : <>{nome}</>;
}

/** Um grupo do vencimento: fechado por padrão, com a lista (link para a execução e a hora local) ao abrir. */
function GrupoDoVencimento({ g, agora }: { g: Extract<Bloco, { tipo: 'grupo' }>; agora: number }) {
  const porques = [...new Set(g.itens.map((d) => d.por_que_nao).filter((x): x is string => !!x))];
  return (
    <li className={styles.decidida} data-grupo-decidido={g.chave}>
      <div className={styles.topo}>
        <Badge size="sm" tone="accent"><Bot size={11} aria-hidden /> {ROTULO_DA_FILA[g.fila]}</Badge>
      </div>
      <Disclosure bare summary={<strong className={styles.titulo}>{tituloDoGrupo(g.fila, g.itens.length)}</strong>}>
        <span className={styles.detalhe}>Por quê: {motivoDaRegra(g.regra)}</span>
        {porques.map((p) => <span key={p} className={styles.semVolta} data-sem-volta>{p}</span>)}
        <ul className={styles.linhasDoGrupo} aria-label={tituloDoGrupo(g.fila, g.itens.length)}>
          {g.itens.map((d) => (
            <li key={d.id} data-decidida={d.id}>
              {d.run_id
                ? <a className={styles.linkDoItem} href={hashDe('execucoes', { segmentos: [d.run_id] })} title={d.run_id}>{rotuloDaExecucao(d.run_id)}</a>
                : <span>{d.item_ref}</span>}
              {' · '}
              <span title={formatDateTime(d.decidida_em)}>encerrada {formatQuando(d.decidida_em, agora)}</span>
            </li>
          ))}
        </ul>
      </Disclosure>
    </li>
  );
}

/** Uma decisão avulsa: o que foi feito, a regra, os fatos e o desfazer (ou o porquê de não haver). */
function CartaoDecidido({ d, agora, aberto, onAbrir, onFechar, onDesfazer }: {
  d: Decidida; agora: number; aberto: boolean; onAbrir: () => void; onFechar: () => void;
  onDesfazer: (d: Decidida, motivo: string) => Promise<string | null>;
}) {
  const fatos = fatosEmPortugues(d.fatos);
  const doAprendizado = d.fila === 'aprendizado';
  return (
    <li className={cx(styles.decidida, d.desfeita && styles.desfeita)} data-decidida={d.id}>
      <div className={styles.topo}>
        <Badge size="sm" tone="accent"><Bot size={11} aria-hidden /> {ROTULO_DA_FILA[d.fila]}</Badge>
        <span className={styles.idade} title={formatDateTime(d.decidida_em)}>decidido {tempoRelativo(d.decidida_em, agora)}</span>
      </div>
      {/* No aprendizado o destaque é QUAL item mudou (com o link para o Livro); o efeito vem logo abaixo. */}
      {doAprendizado ? (
        <>
          <strong className={styles.titulo}><TituloDoItem d={d} /></strong>
          <span className={styles.detalhe}>{d.efeito}</span>
        </>
      ) : <strong className={styles.titulo}>{d.efeito}</strong>}
      <span className={styles.detalhe}>Por quê: {motivoDaRegra(d.regra)}</span>
      {fatos.length > 0 ? <span className={styles.detalhe}>{fatos.join(' · ')}</span> : null}
      {d.desfeita ? (
        <span className={styles.detalhe} data-desfeita>
          Desfeita {tempoRelativo(d.desfeita_em, agora)}{d.desfeita_por ? ` ${quemDesfez(d.desfeita_por)}` : ''}
          {d.motivo_do_desfazer ? `: ${d.motivo_do_desfazer}` : ''}
        </span>
      ) : aberto ? (
        <DecisaoInline acao={{ confirmar: `${d.acao_do_desfazer} esta decisão`, perigo: true }} motivoOpcional
                       motivoMax={MOTIVO_DO_DESFAZER_MAX}
                       dica={`Fica registrado com o seu nome. Vale até ${formatDateTime(d.prazo_ate)}.`}
                       onConfirmar={(m) => onDesfazer(d, m)} onCancelar={onFechar} />
      ) : d.pode_desfazer ? (
        <div><Button size="sm" variant="secondary" onClick={onAbrir}>{d.acao_do_desfazer}</Button></div>
      ) : (
        <span className={styles.semVolta} data-sem-volta>{d.por_que_nao ?? 'Não dá para desfazer esta decisão.'}</span>
      )}
    </li>
  );
}

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
        quando a fila de origem tem uma volta segura. Quando os avisos estão ligados, você recebe no Telegram um resumo por janela, nunca um aviso por decisão.
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
          {emBlocos(lista.itens).map((b) => b.tipo === 'grupo'
            ? <GrupoDoVencimento key={b.chave} g={b} agora={agora} />
            : (
              <CartaoDecidido key={b.d.id} d={b.d} agora={agora} aberto={abrindo === b.d.id}
                              onAbrir={() => setAbrindo(b.d.id)} onFechar={() => setAbrindo(null)} onDesfazer={desfazer} />
            ))}
        </ul>
      ) : null}
    </div>
  );
}
