import { Ban, CheckCircle2, Clock, Hourglass, ImageIcon, Lock, Play, RotateCcw, ShieldQuestion, Undo2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { AprovarPlanoItem, ItemDaPorta, PreviaDaPorta, SeloDaPorta } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { TextArea } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import type { Tone } from '../../lib/status';
import { formatClock, formatDateTime } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Runs.module.css';

/** O que cada selo diz para quem decide (adendo v1.32). */
const SELO: Record<SeloDaPorta, { rotulo: string; tone: Tone; icon: typeof Lock }> = {
  permitido: { rotulo: 'liberada', tone: 'success', icon: CheckCircle2 },
  aprovacao: { rotulo: 'pede seu aval', tone: 'warning', icon: Lock },
  adiado: { rotulo: 'espera', tone: 'info', icon: Hourglass },
  recusado: { rotulo: 'não será feita', tone: 'muted', icon: Ban },
  na_execucao: { rotulo: 'decide na execução', tone: 'neutral', icon: ShieldQuestion },
};

function rotuloDoSelo(item: ItemDaPorta): string {
  return item.selo === 'adiado' && item.retry_at ? `espera até ${formatClock(item.retry_at)}` : SELO[item.selo].rotulo;
}

/** A ação em frase curta: "SEND_MESSAGE para @ana". */
function oQue(item: ItemDaPorta): string {
  const acao = item.acao ?? item.titulo;
  return item.alvo ? `${acao} para @${item.alvo.replace(/^@/, '')}` : acao;
}

interface Mudou { step_id: string; selo: string | null; motivo: string }

/** O começo do motivo que o servidor dá quando o item deixou de ser 🔒 por causa do texto editado (B1). */
const MOTIVO_DO_TEXTO_EDITADO = 'com o texto editado:';

/** O mesmo limite e o mesmo marcador de modelo do servidor (`LIMITE_DO_TEXTO`, `tem_variavel`). O `\w` do Python
 * cobre Unicode e o do JavaScript não: `[\p{L}\p{N}_]` com a flag `u` é o equivalente, para `{ação}` ser variável nos
 * dois lados (nota da Ferramentas, 04/10). */
export const LIMITE_DO_TEXTO = 2200;
export function temVariavel(texto: string): boolean {
  return /\{\{|\$\{|\{[A-Za-z_][\p{L}\p{N}_.:-]*\}/u.test(texto);
}

/** O tamanho como o servidor conta (`len` do Python: ponto de código, não unidade UTF-16; um emoji conta 1). */
export function tamanhoDoTexto(texto: string): number {
  return [...texto.trim()].length;
}

/** Sobra `{` que não é marcador (`{ nome }`, `:-{`): o texto sai exatamente assim. Avisa, não trava. */
export function temChaveSolta(texto: string): boolean {
  return texto.includes('{') && !temVariavel(texto);
}

/** 30.68: a espera da digitação antes de conferir o texto editado na porta (sair do campo confere na hora). */
export const ESPERA_DA_DIGITACAO_MS = 500;

/** A prévia do item com o texto editado: para qual texto foi pedida e o que voltou. */
interface PreviaDoTexto { texto: string; item: ItemDaPorta | null; erro: string | null; carregando: boolean }

type Conferencia = 'conferindo' | 'ok' | 'mudou' | 'erro';

function conferencia(p: PreviaDoTexto | undefined, texto: string): Conferencia {
  if (!p || p.texto !== texto || p.carregando) return 'conferindo';
  if (p.erro !== null) return 'erro';
  return p.item?.selo === 'aprovacao' && p.item.chave ? 'ok' : 'mudou';
}

/** Lê o 409 `plano_mudou`: a lista do que mudou e a prévia nova. Tolerante: o que faltar vira vazio. */
function lerPlanoMudou(detail: Record<string, unknown> | null): { mudaram: Mudou[]; previa: PreviaDaPorta | null } {
  const mudaram = Array.isArray(detail?.mudaram) ? (detail.mudaram as Mudou[]) : [];
  const previa = detail?.previa && typeof detail.previa === 'object' ? (detail.previa as PreviaDaPorta) : null;
  return { mudaram, previa };
}

/**
 * 30.61: a prévia da porta numa execução com plano pronto. O dono vê o que cada etapa com efeito vai encontrar na porta
 * do despacho e aprova ANTES de iniciar o que pede o aval dele. O selo é a mesma conta do despacho, só lendo, e é uma
 * estimativa (a porta confere de novo na execução). O sim só vale para o item idêntico: editar o texto aqui grava a
 * chave do texto editado (o servidor recalcula).
 */
export function PortaDoPlano({ runId }: { runId: string }) {
  const [previa, setPrevia] = useState<PreviaDaPorta | null>(null);
  const [erro, setErro] = useState<{ message: string; hint: string } | null>(null);
  const [tiradas, setTiradas] = useState<Set<string>>(() => new Set());
  const [textos, setTextos] = useState<Record<string, string>>({});
  const [mudaram, setMudaram] = useState<Mudou[]>([]);
  const [enviando, setEnviando] = useState(false);
  // 30.68: a prévia de cada texto editado; `pedidos` guarda o último texto pedido por etapa (resposta velha não vale).
  const [previasDoTexto, setPreviasDoTexto] = useState<Record<string, PreviaDoTexto>>({});
  const pedidos = useRef<Map<string, string>>(new Map());

  const carregar = useCallback(async (signal?: AbortSignal) => {
    try {
      const p = await api.portaDoPlano(runId, signal);
      setPrevia(p);
      setErro(null);
    } catch (e) {
      if (signal?.aborted) return;
      const err = toApiError(e);
      setErro({ message: err.message, hint: hintForError(err) });
    }
  }, [runId]);

  useEffect(() => {
    const ctl = new AbortController();
    void carregar(ctl.signal);
    return () => ctl.abort();
  }, [carregar]);

  const itens = useMemo(() => previa?.itens ?? [], [previa]);
  // Tirar uma etapa tira as que dependem dela (a conta final é do servidor; aqui é para a pessoa ver antes).
  const tiradasComDependentes = useMemo(() => {
    const todas = new Set(tiradas);
    for (const item of itens) if (tiradas.has(item.step_id)) for (const d of item.dependentes) todas.add(d);
    return todas;
  }, [itens, tiradas]);
  const aprovaveis = useMemo(
    () => itens.filter((i) => i.selo === 'aprovacao' && i.chave && !tiradasComDependentes.has(i.step_id)),
    [itens, tiradasComDependentes],
  );
  const emBranco = aprovaveis.filter((i) => i.texto != null && (textos[i.step_id] ?? i.texto).trim() === '').length;
  const comVariavel = aprovaveis.filter((i) => temVariavel(textos[i.step_id] ?? '')).length;
  const longos = aprovaveis.filter((i) => tamanhoDoTexto(textos[i.step_id] ?? '') > LIMITE_DO_TEXTO).length;

  // 30.68: o texto editado DE VERDADE (diferente do da prévia) e que passa nas travas locais: esse se confere na porta,
  // porque há regra que depende do texto (a DM repetida) e o dono tem de ver o motivo novo antes do sim.
  const editados = useMemo(() => {
    const m = new Map<string, string>();
    for (const i of aprovaveis) {
      const t = textos[i.step_id];
      if (t === undefined || i.texto == null) continue;
      const limpo = t.trim();
      if (limpo === i.texto.trim() || limpo === '' || temVariavel(limpo) || tamanhoDoTexto(limpo) > LIMITE_DO_TEXTO) continue;
      m.set(i.step_id, limpo);
    }
    return m;
  }, [aprovaveis, textos]);

  const conferir = useCallback(async (stepId: string, texto: string) => {
    if (pedidos.current.get(stepId) === texto) return;
    pedidos.current.set(stepId, texto);
    setPreviasDoTexto((s) => ({ ...s, [stepId]: { texto, item: null, erro: null, carregando: true } }));
    try {
      const r = await api.previaDoItem(runId, stepId, texto);
      if (pedidos.current.get(stepId) !== texto) return;
      setPreviasDoTexto((s) => ({ ...s, [stepId]: { texto, item: r.item, erro: null, carregando: false } }));
    } catch (e) {
      if (pedidos.current.get(stepId) !== texto) return;
      pedidos.current.delete(stepId);   // sair do campo tenta de novo
      const err = toApiError(e);
      setPreviasDoTexto((s) => ({ ...s, [stepId]: { texto, item: null, erro: err.message, carregando: false } }));
    }
  }, [runId]);

  // Prévia nova (recarga ou 409): o que se conferiu era da anterior.
  useEffect(() => {
    pedidos.current.clear();
    setPreviasDoTexto({});
  }, [previa]);

  useEffect(() => {
    if (editados.size === 0) return undefined;
    const espera = window.setTimeout(() => {
      for (const [sid, texto] of editados) void conferir(sid, texto);
    }, ESPERA_DA_DIGITACAO_MS);
    return () => window.clearTimeout(espera);
  }, [editados, conferir]);

  const conferencias = [...editados].map(([sid, texto]) => conferencia(previasDoTexto[sid], texto));
  const conferindo = conferencias.filter((c) => c === 'conferindo').length;
  const naoFecham = conferencias.filter((c) => c === 'mudou').length;
  const semConferir = conferencias.filter((c) => c === 'erro').length;

  const cartoes = useMemo(() => {
    const m = new Map<string, ItemDaPorta[]>();
    for (const item of itens) {
      const chave = `${item.aparelho}|${item.persona_rotulo ?? ''}`;
      m.set(chave, [...(m.get(chave) ?? []), item]);
    }
    // O ⛔ vai para o fim do cartão, em cinza: não é decisão, é informação.
    return [...m.entries()].map(([chave, lista]) => ({
      chave, aparelho: lista[0]?.aparelho ?? '', persona: lista[0]?.persona_rotulo ?? null,
      lista: [...lista.filter((i) => i.selo !== 'recusado'), ...lista.filter((i) => i.selo === 'recusado')],
    }));
  }, [itens]);

  function alternar(stepId: string) {
    setTiradas((s) => {
      const novo = new Set(s);
      if (novo.has(stepId)) novo.delete(stepId); else novo.add(stepId);
      return novo;
    });
  }

  async function aprovarEIniciar() {
    if (!previa) return;
    setEnviando(true);
    // 30.68: o texto editado vai com a chave da prévia DESSE texto, a que o dono viu (o servidor confere de novo).
    const aprovar: AprovarPlanoItem[] = aprovaveis.map((i) => {
      const editado = editados.get(i.step_id);
      // Sem a prévia do texto (o botão já trava), a chave da prévia faz o servidor devolver 409: a edição nunca se perde.
      return editado !== undefined
        ? { step_id: i.step_id, chave: previasDoTexto[i.step_id]?.item?.chave ?? (i.chave as string), texto: editado }
        : { step_id: i.step_id, chave: i.chave as string };
    });
    try {
      const r = await api.aprovarPlano(runId, aprovar, [...tiradas]);
      setMudaram([]);
      toast({
        tone: 'success',
        title: r.aprovacoes.length > 0 ? `${r.aprovacoes.length} aprovado(s) e execução iniciada` : 'Execução iniciada',
        message: r.aprovacoes.length > 0
          ? `O sim vale até ${formatDateTime(r.validade_ate)}, só para o item idêntico; o resto a porta decide na execução.`
          : 'A porta decide cada item na execução, como sempre.',
      });
      // A execução já está `running`: o RunView troca de situação pela store, como no “Iniciar execução”.
      useAppStore.getState().upsertRun(r.run);
    } catch (e) {
      const err = toApiError(e);
      if (err.code === 'plano_mudou') {
        const lido = lerPlanoMudou(err.detail);
        setMudaram(lido.mudaram);
        if (lido.previa) {
          // F1/F3 da revisão: o que a pessoa marcou e editou só vale para as etapas da prévia NOVA. Uma tirada que sumiu
          // no replanejamento voltaria em 409 a cada clique sem aparecer para desmarcar; a edição de um item que mudou
          // esconderia o texto novo.
          const vivas = new Set(lido.previa.itens.map((i) => i.step_id));
          // N2: quando o que mudou foi o PRÓPRIO texto editado ("com o texto editado: …"), a edição fica no campo para o
          // dono corrigir, em vez de redigitar.
          const mudados = new Set(lido.mudaram.filter((m) => !m.motivo.startsWith(MOTIVO_DO_TEXTO_EDITADO))
            .map((m) => m.step_id));
          setTiradas((t) => new Set([...t].filter((id) => vivas.has(id))));
          setTextos((t) => Object.fromEntries(Object.entries(t).filter(([id]) => vivas.has(id) && !mudados.has(id))));
          setPrevia(lido.previa);
        }
        else await carregar();
      } else {
        toastError('Não foi possível aprovar o plano', e);
      }
    } finally {
      setEnviando(false);
    }
  }

  if (previa === null && erro === null) {
    return (
      <LoadingRegion label="Calculando a porta de cada etapa…" className={styles.stack}>
        <Skeleton height={64} radius={8} />
        <Skeleton height={96} radius={8} />
      </LoadingRegion>
    );
  }
  if (previa === null) {
    return (
      <EmptyState
        icon={ShieldQuestion}
        tone="danger"
        compact
        title="A prévia da porta não pôde ser calculada"
        hint={`${erro?.hint ?? ''} Você ainda pode iniciar: a porta decide cada item na execução, como sempre.`}
        actions={<Button variant="outline" icon={RotateCcw} onClick={() => void carregar()}>Calcular de novo</Button>}
      >
        {erro?.message}
      </EmptyState>
    );
  }

  const contagem = (s: SeloDaPorta) => itens.filter((i) => i.selo === s).length;
  const aparelhos = new Set(itens.map((i) => i.aparelho)).size;
  const naExecucao = itens.filter((i) => i.selo === 'na_execucao');
  const partes = [
    contagem('permitido') ? `${contagem('permitido')} liberada(s)` : '',
    contagem('aprovacao') ? `${contagem('aprovacao')} pede(m) seu aval` : '',
    contagem('adiado') ? `${contagem('adiado')} espera(m)` : '',
    contagem('recusado') ? `${contagem('recusado')} não será(ão) feita(s)` : '',
    naExecucao.length ? `${naExecucao.length} decide(m) na execução` : '',
  ].filter(Boolean);

  return (
    <section className={styles.stack} aria-label="Prévia da porta">
      <Banner tone="info" icon={Lock} title={itens.length === 0
        ? 'Nenhuma etapa com efeito neste plano'
        : `${itens.length} ação(ões) em ${aparelhos} aparelho(s): ${partes.join(', ')}`} role="status">
        É uma estimativa: a porta confere tudo de novo na hora de cada efeito. O que você aprovar aqui vale só para o item
        idêntico (mesma conta, alvo e texto) e até {formatDateTime(previa.validade_ate)}.
      </Banner>

      {mudaram.length > 0 ? (
        <Banner tone="warning" icon={Undo2} title={`O plano mudou desde a prévia: ${mudaram.length} item(ns)`} role="alert">
          Nada foi gravado. A prévia abaixo já é a nova; confira e aprove de novo.
          <ul className={styles.skippedList}>
            {mudaram.map((m) => <li key={m.step_id}>{itens.find((i) => i.step_id === m.step_id)?.titulo ?? m.step_id}: {m.motivo}</li>)}
          </ul>
        </Banner>
      ) : null}
      {previa.parcial || previa.total ? (
        <Banner tone="warning" icon={ShieldQuestion} title={previa.total ? 'A porta não pôde ser calculada para nenhum item' : 'Parte da prévia falhou'} role="status">
          Os itens sem cálculo ficam para a execução, sem Aprovar; lá a porta pergunta como sempre.
        </Banner>
      ) : null}

      {cartoes.map((c) => (
        <article key={c.chave} className={styles.draft} aria-label={`Ações de ${c.persona ?? c.aparelho} em ${c.aparelho}`}>
          <div className={styles.draftHead}>
            <span className={styles.eventInst}>{c.aparelho}</span>
            {c.persona ? <span className={styles.draftWhat}>{c.persona}</span> : null}
          </div>
          <ul className={styles.drafts}>
            {c.lista.map((item) => {
              const selo = SELO[item.selo];
              const tirada = tiradasComDependentes.has(item.step_id);
              const porDependencia = tirada && !tiradas.has(item.step_id);
              const texto = textos[item.step_id] ?? item.texto ?? '';
              const editavel = item.selo === 'aprovacao' && item.chave && item.texto != null && !tirada;
              return (
                <li key={item.step_id} className={styles.draft} data-descartado={tirada || item.selo === 'recusado' ? '' : undefined}>
                  <div className={styles.draftHead}>
                    <span className={styles.draftWhat}>{oQue(item)}</span>
                    <Badge tone={selo.tone} icon={selo.icon}>{rotuloDoSelo(item)}</Badge>
                    {item.tem_imagem ? <Badge tone="neutral" icon={ImageIcon}>com imagem</Badge> : null}
                    {tirada ? <Badge tone="warning">{porDependencia ? 'sai junto (depende de uma tirada)' : 'não será feita'}</Badge> : null}
                  </div>
                  {editavel ? (
                    <TextArea rows={3} aria-label={`Texto de ${oQue(item)} em ${item.aparelho}`} value={texto}
                              onChange={(e) => setTextos((s) => ({ ...s, [item.step_id]: e.target.value }))}
                              onBlur={() => {
                                const editado = editados.get(item.step_id);
                                if (editado !== undefined) void conferir(item.step_id, editado);
                              }} />
                  ) : item.texto ? <p className={styles.draftWhat}>“{item.texto}”</p> : null}
                  {editavel && editados.has(item.step_id)
                    ? <TextoConferido previa={previasDoTexto[item.step_id]} texto={editados.get(item.step_id) ?? ''} />
                    : null}
                  {editavel && temChaveSolta(texto) ? (
                    <p className={styles.draftWhat} role="note">Este texto tem uma chave ({'{'}); ele sai exatamente assim.</p>
                  ) : null}
                  {item.motivo || item.dica ? (
                    <Disclosure summary="Saiba mais">
                      {item.motivo ? <p>{item.motivo}</p> : null}
                      {item.dica ? <p>{item.dica}</p> : null}
                    </Disclosure>
                  ) : null}
                  {item.selo !== 'recusado' && !porDependencia ? (
                    <div className={styles.draftActions}>
                      <Button size="sm" variant="ghost" icon={tirada ? Undo2 : undefined}
                              aria-label={`${tirada ? 'Voltar a fazer' : 'Não fazer esta'} — ${oQue(item)} em ${item.aparelho}`}
                              onClick={() => alternar(item.step_id)}>
                        {tirada ? 'Voltar a fazer' : 'Não fazer esta'}
                      </Button>
                      {item.dependentes.length > 0 && !tirada ? (
                        <span className={styles.draftWhat}>tirar esta tira também {item.dependentes.length} etapa(s) que dependem dela</span>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </article>
      ))}

      <Disclosure summary="Ainda vão pedir você na execução" defaultOpen={naExecucao.length > 0}>
        <ul className={styles.skippedList}>
          {naExecucao.map((i) => <li key={i.step_id}>{i.aparelho}: {oQue(i)}{i.motivo ? ` — ${i.motivo}` : ''}</li>)}
          {previa.na_execucao.itens_for_each > 0 ? (
            <li>{previa.na_execucao.itens_for_each} etapa(s) que se repetem por item de uma lista: os itens só existem na execução</li>
          ) : null}
          <li>Sempre com você: {previa.na_execucao.sempre.join(', ')}</li>
        </ul>
      </Disclosure>

      <div className={styles.draftBar}>
        <span className={styles.draftTally}>
          <Clock size={12} aria-hidden /> {aprovaveis.length} para aprovar
          {tiradas.size > 0 ? ` · ${tiradasComDependentes.size} tirada(s)` : ''}
        </span>
        <Button
          variant="primary"
          icon={Play}
          loading={enviando}
          disabledReason={emBranco > 0
            ? `${emBranco} texto(s) em branco: escreva o texto ou marque “Não fazer esta”.`
            : comVariavel > 0 ? 'Há texto com {nome}: escreva o texto final.'
              : longos > 0 ? `Há texto acima de ${LIMITE_DO_TEXTO} caracteres.`
                : naoFecham > 0 ? `${naoFecham} texto(s) editado(s) não pedem mais o seu aval aqui: volte ao texto da prévia ou marque “Não fazer esta”.`
                  : semConferir > 0 ? 'Não deu para conferir o texto editado: saia do campo para tentar de novo.'
                    : conferindo > 0 ? 'Conferindo na porta o texto editado…' : null}
          onClick={() => void aprovarEIniciar()}
        >
          {aprovaveis.length > 0 ? `Aprovar ${aprovaveis.length} e iniciar` : 'Iniciar e decidir na execução'}
        </Button>
      </div>
    </section>
  );
}

/** O texto do resultado do Renovar. O caso misto diz os dois números: senão o dono acha que renovou tudo. */
export function frasesDoRenovar(renovadas: number, vencidas: number): string {
  if (vencidas === 0) return `${renovadas} sim(ns) do plano renovado(s).`;
  return `${renovadas} renovado(s), ${vencidas} vencido(s) voltam para você rever.`;
}


/** 30.68: o que a porta faz com o texto editado, junto do item: o motivo novo, ou por que ele deixa de ser 🔒. */
function TextoConferido({ previa, texto }: { previa: PreviaDoTexto | undefined; texto: string }) {
  const estado = conferencia(previa, texto);
  if (estado === 'conferindo') return <p className={styles.draftWhat} role="status">Conferindo este texto na porta…</p>;
  if (estado === 'erro') {
    return <p className={styles.draftWhat} role="alert">Não deu para conferir este texto: {previa?.erro}. Saia do campo para tentar de novo.</p>;
  }
  const item = previa?.item ?? null;
  if (estado === 'mudou') {
    const selo = item ? SELO[item.selo].rotulo : 'não fecha mais uma ação';
    return (
      <p className={styles.draftWhat} role="alert">
        Com este texto, a ação não pede mais o seu aval aqui ({selo}){item?.motivo ? `: ${item.motivo}` : ''}. Volte ao
        texto da prévia ou marque “Não fazer esta”.
      </p>
    );
  }
  return (
    <p className={styles.draftWhat} role="status">
      Com este texto: pede seu aval{item?.motivo ? ` — ${item.motivo}` : ''}.
    </p>
  );
}

/**
 * 30.61: a validade dos sins dados na prévia, numa execução viva, com "Renovar". Renovar estende só o que ainda vale; o
 * que já venceu volta para o dono rever (a porta pergunta de novo na execução).
 */
export function ValidadeDoPlano({ runId, token = '' }: { runId: string; token?: string }) {
  const [abertos, setAbertos] = useState<{ total: number; vence: string | null; vencidos: number } | null>(null);
  const [renovando, setRenovando] = useState(false);

  const carregar = useCallback(async () => {
    try {
      const lista = await api.listApprovals('approved', undefined, runId);
      const doPlano = lista.filter((a) => a.origem === 'plano' && a.interaction_id === null && a.expires_at);
      // F2: o vencido que a faxina ainda não marcou não "vale até" uma hora passada; ele já voltou para o dono.
      const agora = Date.now();
      const validos = doPlano.filter((a) => Date.parse(a.expires_at as string) > agora);
      const vence = validos.map((a) => a.expires_at as string).sort()[0] ?? null;
      setAbertos({ total: validos.length, vence, vencidos: doPlano.length - validos.length });
    } catch {
      setAbertos(null);                       // sem a lista, não há o que mostrar; a porta segue decidindo
    }
    // `token` muda quando a execução muda de situação: é o gatilho para reler.
  }, [runId, token]);

  useEffect(() => { void carregar(); }, [carregar]);

  async function renovar() {
    setRenovando(true);
    try {
      const r = await api.renovarPorta(runId);
      toast({ tone: r.vencidas > 0 ? 'info' : 'success', title: 'Validade do plano', message: frasesDoRenovar(r.renovadas, r.vencidas) });
    } catch (e) {
      const err = toApiError(e);
      if (err.code === 'sim_vencido') {
        toast({ tone: 'warning', title: 'Nada renovado', message: `${err.message}` });
      } else {
        toastError('Não foi possível renovar', e);
      }
    } finally {
      setRenovando(false);
      await carregar();
    }
  }

  if (!abertos || abertos.total + abertos.vencidos === 0) return null;
  return (
    <Banner
      tone="info"
      icon={Clock}
      role="status"
      title={abertos.total > 0
        ? `${abertos.total} sim(ns) dado(s) na prévia valem até ${formatDateTime(abertos.vence)}`
          + (abertos.vencidos > 0 ? `; ${abertos.vencidos} venceu(ram) e volta(m) para você` : '')
        : `${abertos.vencidos} sim(ns) dado(s) na prévia venceu(ram) e volta(m) para você`}
      actions={<Button size="sm" icon={RotateCcw} loading={renovando} onClick={() => void renovar()}>Renovar</Button>}
    >
      Cada um vale só para o item idêntico e uma vez. Renovar estende o que ainda vale; o que já venceu volta para você.
    </Banner>
  );
}
