/**
 * O lote em andamento (v0.34): o servidor gera N personas em segundo plano (`POST /personas/generate/batch`) e avisa
 * cada mudança de item pelo evento `persona.batch.updated`; ao ouvir o evento DESTE lote, o diálogo relê o estado
 * (`GET …/batch/{id}`, que traz os rascunhos). Sem WebSocket a leitura periódica cobre, e um lote perdido num
 * reinício do servidor (404) vira aviso, não erro: as criadas já estão na lista.
 *
 * No fim: as criadas com "Abrir", os rascunhos (lote "Revisar antes de criar") com caixa de seleção e "Criar
 * selecionadas", e as falhas com o motivo que o servidor deu.
 */
import { CircleCheck, Layers, TriangleAlert, UserRound } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiLote, toApiError } from '../../api/client';
import type { PersonaBatch, PersonaBatchItem, PersonaBatchItemStatus } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Checkbox } from '../../components/Field';
import { ProgressBar } from '../../components/ProgressBar';
import { isRecord } from '../../lib/format';
import type { Tone } from '../../lib/status';
import { onLiveEvent } from '../../store/live';
import { toast } from '../../store/toasts';
import { executarEmLote } from './emLote';
import styles from './Profiles.module.css';

export const EVENTO_LOTE = 'persona.batch.updated';
/** Sem evento (WebSocket caído), o estado é relido nesse intervalo enquanto o lote não termina. */
export const RELEITURA_DO_LOTE_MS = 3000;

const ESTADO: Record<PersonaBatchItemStatus, { label: string; tone: Tone }> = {
  pending: { label: 'na fila', tone: 'neutral' },
  generating: { label: 'gerando…', tone: 'info' },
  ready: { label: 'rascunho', tone: 'accent' },
  created: { label: 'criada', tone: 'success' },
  failed: { label: 'falhou', tone: 'danger' },
};

/** O que o painel fez com um rascunho depois do lote ("Criar selecionadas"). */
type Criacao = { ok: true; id: string; name: string } | { ok: false; motivo: string };

function linhaDoRascunho(item: PersonaBatchItem): string {
  const d = item.draft;
  if (!d) return '';
  return [d.gender, d.biography?.home?.city, d.biography?.work?.profession].filter(Boolean).join(' · ');
}

export function LoteDePersonas({ batchId, onClose, onAbrir, onLote, releituraMs = RELEITURA_DO_LOTE_MS }: {
  batchId: string;
  onClose: () => void;
  onAbrir?: (id: string) => void;
  /** Relê a lista de personas (as criadas aparecem): no fim do lote e depois de "Criar selecionadas". */
  onLote?: () => Promise<void> | void;
  /** Intervalo da releitura sem evento (os testes o encurtam ou alongam). */
  releituraMs?: number;
}) {
  const [lote, setLote] = useState<PersonaBatch | null>(null);
  const [perdido, setPerdido] = useState<string | null>(null);
  const [desmarcadas, setDesmarcadas] = useState<Set<number>>(() => new Set());
  const [criacoes, setCriacoes] = useState<Record<number, Criacao>>({});
  const [criando, setCriando] = useState(false);
  const token = useRef(0);
  const avisouFim = useRef(false);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    try {
      const r = await apiLote.getPersonaBatch(batchId);
      if (meu === token.current) setLote(r);
    } catch (e) {
      const x = toApiError(e);
      if (meu !== token.current) return;
      // O lote vive na memória do servidor: 404 é "o servidor reiniciou", não defeito desta tela.
      if (x.status === 404) setPerdido(x.message);
    }
  }, [batchId]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  // Cada mudança de item chega por evento; relê só quando é DESTE lote.
  useEffect(() => onLiveEvent((ev) => {
    if (ev.kind !== EVENTO_LOTE || !isRecord(ev.data) || ev.data.batch_id !== batchId) return;
    void carregar();
  }), [batchId, carregar]);

  // Rede de segurança: sem WebSocket, o evento não chega. Enquanto não termina, relê de tempos em tempos.
  const terminado = !!lote?.done || !!perdido;
  useEffect(() => {
    if (terminado) return;
    const t = setInterval(() => void carregar(), releituraMs);
    return () => clearInterval(t);
  }, [terminado, carregar, releituraMs]);

  // No fim, a lista de personas se relê uma vez (as criadas pelo lote aparecem nela).
  useEffect(() => {
    if (!lote?.done || avisouFim.current) return;
    avisouFim.current = true;
    void onLote?.();
  }, [lote?.done, onLote]);

  const itens = lote?.items ?? [];
  const concluidos = itens.filter((i) => i.status === 'ready' || i.status === 'created' || i.status === 'failed').length;
  const criadas = itens.filter((i) => i.status === 'created').length;
  const falhas = itens.filter((i) => i.status === 'failed').length;
  const rascunhos = itens.filter((i) => i.status === 'ready');
  const selecionaveis = rascunhos.filter((i) => !criacoes[i.index]?.ok && i.draft);
  const escolhidas = selecionaveis.filter((i) => !desmarcadas.has(i.index));

  function alternar(index: number) {
    setDesmarcadas((s) => {
      const n = new Set(s);
      if (n.has(index)) n.delete(index);
      else n.add(index);
      return n;
    });
  }

  async function criarSelecionadas() {
    if (criando || escolhidas.length === 0) return;
    setCriando(true);
    try {
      const alvo = [...escolhidas];
      const r = await executarEmLote(alvo, 3, async (item) => {
        const criada = await api.createPersona(item.draft!);
        setCriacoes((c) => ({ ...c, [item.index]: { ok: true, id: criada.id, name: criada.name } }));
      }, (i, res) => {
        if (!res.ok) setCriacoes((c) => ({ ...c, [alvo[i]!.index]: { ok: false, motivo: res.motivo } }));
      });
      const ok = r.filter((x) => x.ok).length;
      toast({ tone: ok === r.length ? 'success' : 'warning', title: `${ok} de ${r.length} persona(s) criada(s)`,
              message: ok < r.length ? 'As que falharam mostram o motivo na lista.' : undefined });
      await onLote?.();
    } finally {
      setCriando(false);
    }
  }

  function fechar() {
    void onLote?.();
    onClose();
  }

  const titulo = lote ? `Lote de ${lote.count} personas` : 'Lote de personas';
  return (
    <Dialog
      open
      onClose={fechar}
      title={titulo}
      icon={Layers}
      size="md"
      footer={(
        <>
          <Button variant="secondary" onClick={fechar}>Fechar</Button>
          {lote && !lote.create && lote.done && selecionaveis.length > 0 ? (
            <Button variant="primary" icon={UserRound} loading={criando}
                    disabledReason={escolhidas.length === 0 ? 'Marque ao menos um rascunho.' : null}
                    onClick={() => void criarSelecionadas()}>
              Criar selecionadas ({escolhidas.length})
            </Button>
          ) : null}
        </>
      )}
    >
      <div className={styles.form}>
        {perdido ? (
          <Banner tone="warning" icon={TriangleAlert} role="status" title="O lote se perdeu">
            {perdido} Rascunhos que não foram criados precisam ser gerados de novo.
          </Banner>
        ) : !lote ? (
          <p className={styles.detail}>Lendo o lote…</p>
        ) : (
          <>
            <ProgressBar value={lote.count ? concluidos / lote.count : 0} label="Progresso do lote"
                         text={`${concluidos}/${lote.count}`} tone={lote.done ? (falhas ? 'warning' : 'success') : 'accent'} />
            <p className={styles.detail} role="status">
              {lote.done
                ? `Terminado: ${criadas} criada(s) · ${rascunhos.length} rascunho(s) · ${falhas} com falha.`
                : `Gerando ${lote.count} pessoas, duas por vez. Pode fechar: o lote continua no servidor e as criadas aparecem na lista.`}
            </p>
            <ol className={styles.loteLista} aria-label="Pessoas do lote">
              {itens.map((item) => {
                const meta = ESTADO[item.status];
                const minha = criacoes[item.index];
                const criadaAqui = minha?.ok ? minha : null;
                const idCriada = item.persona_id ?? criadaAqui?.id ?? null;
                const nome = item.name ?? `Pessoa ${item.index + 1}`;
                const podeMarcar = item.status === 'ready' && !!item.draft && !criadaAqui;
                return (
                  <li key={item.index} className={styles.loteItem} data-status={item.status}>
                    {podeMarcar ? (
                      <Checkbox aria-label={`Selecionar ${nome}`} checked={!desmarcadas.has(item.index)}
                                onChange={() => alternar(item.index)} disabled={criando} />
                    ) : <span className={styles.loteNumero} aria-hidden>{item.index + 1}</span>}
                    <div className={styles.loteInfo}>
                      <p className={styles.loteNome}>
                        <span className={styles.loteNomeTexto}>{nome}</span>
                        {criadaAqui ? <Badge size="sm" tone="success" icon={CircleCheck}>criada</Badge>
                          : <Badge size="sm" tone={meta.tone} spin={item.status === 'generating'}>{meta.label}</Badge>}
                      </p>
                      {item.status === 'ready' && linhaDoRascunho(item) ? (
                        <p className={styles.loteDetalhe}>{linhaDoRascunho(item)}</p>
                      ) : null}
                      {item.status === 'ready' && item.draft?.summary ? (
                        <p className={styles.loteDetalhe}>{item.draft.summary}</p>
                      ) : null}
                      {item.error ? <p className={styles.loteErro}>{item.error}</p> : null}
                      {minha && !minha.ok ? <p className={styles.loteErro}>Não foi criada: {minha.motivo}</p> : null}
                    </div>
                    {idCriada && onAbrir ? (
                      <Button size="sm" variant="outline" aria-label={`Abrir ${criadaAqui?.name ?? nome}`}
                              onClick={() => onAbrir(idCriada)}>
                        Abrir
                      </Button>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </div>
    </Dialog>
  );
}
