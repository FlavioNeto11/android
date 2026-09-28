import { ArrowLeft, CircleCheck, CircleHelp, Info, MessageSquareReply, Sparkles } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { api, toApiError } from '../../api/client';
import type { CommandRefinement, RefineAnswer, RefineQuestion } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { TextArea, TextInput } from '../../components/Field';
import ui from '../../components/ui.module.css';
import { cx, plural } from '../../lib/format';
import { pareceCredencial } from './history';
import styles from './AssistenteDoComando.module.css';

/**
 * Assistente do comando (ADR-047). Em vez de mandar a pessoa de volta ao texto, a IA reescreve o comando em blocos
 * (Objetivo, App ou site, Passos, Dados, Concluído quando) e pergunta só o que falta; cada resposta entra no texto
 * na rodada seguinte, até não sobrar pendência.
 *
 * Duas portas usam o mesmo componente: o Comando ("Refinar com IA": `onUsar` troca o texto do campo) e a execução
 * em `needs_input` (as perguntas do planejador já chegam abertas; `acoes` planeja ou executa a sucessora).
 *
 * "Pronto para planejar" é a previsão da IA, não a do planejador — quem decide continua sendo o plano.
 */
export interface AssistenteProps {
  /** O texto de partida (o do campo, ou o da execução). */
  comando: string;
  /** Contexto para a IA: alvos da seleção, ou a execução que está sendo respondida. */
  contexto: { instance_ids?: string[]; profile_ids?: string[]; run_id?: string };
  /** Perguntas já feitas (as do planejador numa execução em `needs_input`): abertas antes da primeira rodada. */
  perguntasIniciais?: RefineQuestion[];
  /** Começa refinando sozinho (o botão "Refinar com IA" já foi o pedido). */
  autoIniciar?: boolean;
  /** Botões do fim, com o texto refinado da rodada atual (ou o de partida, antes da primeira). */
  acoes: (texto: string, pronto: boolean) => ReactNode;
  onFechar?: () => void;
  titulo?: string;
}

type Respostas = Record<string, string>;

const chave = (q: RefineQuestion, i: number) => `${q.field || 'pergunta'}-${i}`;

export function AssistenteDoComando({ comando, contexto, perguntasIniciais = [], autoIniciar, acoes, onFechar,
                                      titulo = 'Assistente do comando' }: AssistenteProps) {
  // Cada rodada guarda o resultado; "Voltar" desfaz a última (a pessoa pode não gostar do que a IA reescreveu).
  const [rodadas, setRodadas] = useState<CommandRefinement[]>([]);
  const [texto, setTexto] = useState<string | null>(null);   // o refinado, editável à mão
  const [respostas, setRespostas] = useState<Respostas>({});
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const idBase = useId();
  const pedido = useRef<AbortController | null>(null);

  const atual = rodadas.at(-1) ?? null;
  const perguntas = atual ? atual.questions : perguntasIniciais;
  const base = texto ?? atual?.command ?? comando;
  const respondidas: RefineAnswer[] = perguntas.flatMap((q, i) => {
    const r = (respostas[chave(q, i)] ?? '').trim();
    return r ? [{ field: q.field, question: q.question, answer: r }] : [];
  });
  const comSenha = pareceCredencial(base) || respondidas.some((r) => pareceCredencial(r.answer));
  const pronto = atual !== null && atual.ready && atual.questions.length === 0;

  const refinar = async () => {
    if (carregando || comSenha) return;
    pedido.current?.abort();
    const ctrl = new AbortController();
    pedido.current = ctrl;
    setCarregando(true);
    setErro(null);
    try {
      const r = await api.refineCommand({
        command: base.trim(), answers: respondidas,
        ...(contexto.run_id ? { run_id: contexto.run_id } : {}),
        ...(contexto.instance_ids?.length ? { instance_ids: contexto.instance_ids } : {}),
        ...(contexto.profile_ids?.length ? { profile_ids: contexto.profile_ids } : {}),
      }, ctrl.signal);
      if (ctrl.signal.aborted) return;
      setRodadas((rs) => [...rs, r]);
      setTexto(null);
      setRespostas({});
    } catch (e) {
      if (ctrl.signal.aborted) return;
      const err = toApiError(e);
      setErro(err.message || 'O assistente não respondeu agora. Tente de novo.');
    } finally {
      if (pedido.current === ctrl) setCarregando(false);
    }
  };

  useEffect(() => {
    // Sem trava de "já iniciou": em desenvolvimento o React monta duas vezes, a limpeza aborta o primeiro pedido e
    // só o segundo vale — uma trava num ref deixaria a caixa aberta sem nenhuma rodada.
    if (autoIniciar) void refinar();
    return () => pedido.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- só na montagem: o pedido é o clique que abriu o painel
  }, []);

  const voltar = () => {
    setRodadas((rs) => rs.slice(0, -1));
    setTexto(null);
    setRespostas({});
    setErro(null);
  };

  const pendentes = perguntas.length;
  const faltaResponder = pendentes > 0 && respondidas.length === 0;

  return (
    <section className={styles.box} aria-label={titulo} aria-busy={carregando}>
      <header className={styles.head}>
        <span className={styles.headTitle}><Sparkles size={15} aria-hidden /> {titulo}</span>
        {atual ? (
          pronto
            ? <Badge tone="success" icon={CircleCheck}>Pronto para planejar</Badge>
            : <Badge tone="warning" icon={CircleHelp}>{plural(pendentes, 'pendência', 'pendências')}</Badge>
        ) : pendentes > 0 ? <Badge tone="warning" icon={CircleHelp}>{plural(pendentes, 'pergunta', 'perguntas')}</Badge> : null}
        {onFechar ? <Button size="sm" variant="ghost" className={styles.fechar} onClick={onFechar}>Fechar</Button> : null}
      </header>

      {atual ? (
        <div className={styles.resultado}>
          {atual.summary ? <p className={styles.resumo}>{atual.summary}</p> : null}
          <label htmlFor={`${idBase}-texto`} className={styles.rotulo}>Comando refinado (pode ajustar à mão)</label>
          <TextArea id={`${idBase}-texto`} className={styles.texto} rows={Math.min(14, Math.max(5, base.split('\n').length + 1))}
                    value={base} onChange={(e) => setTexto(e.target.value)} />
          {atual.notes.length > 0 ? (
            <ul className={styles.notas}>
              {atual.notes.map((n) => <li key={n}><Info size={13} aria-hidden /> {n}</li>)}
            </ul>
          ) : null}
        </div>
      ) : carregando ? (
        <p className={styles.resumo}>Lendo o comando e o que o sistema sabe fazer…</p>
      ) : null}

      {pendentes > 0 ? (
        <ol className={styles.perguntas} aria-label="O que ainda falta">
          {perguntas.map((q, i) => {
            const k = chave(q, i);
            const id = `${idBase}-${k}`;
            const valor = respostas[k] ?? '';
            return (
              <li key={k} className={styles.pergunta}>
                <label htmlFor={id} className={styles.perguntaTexto}>{q.question}</label>
                {q.why ? <span className={styles.porque}>{q.why}</span> : null}
                {q.options.length > 0 ? (
                  <div className={styles.opcoes} role="group" aria-label={`Opções para: ${q.question}`}>
                    {q.options.map((o) => (
                      <button key={o} type="button" className={ui.chip} aria-pressed={valor === o}
                              onClick={() => setRespostas((r) => ({ ...r, [k]: valor === o ? '' : o }))}>
                        {o}
                      </button>
                    ))}
                  </div>
                ) : null}
                <TextInput id={id} small value={valor} placeholder={q.options.length > 0 ? 'ou escreva a resposta' : 'Sua resposta'}
                           invalid={pareceCredencial(valor)}
                           onChange={(e) => setRespostas((r) => ({ ...r, [k]: e.target.value }))}
                           onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void refinar(); } }} />
              </li>
            );
          })}
        </ol>
      ) : null}

      {comSenha ? (
        <p className={cx(styles.aviso, styles.avisoPerigo)} role="alert">
          Há uma senha no texto ou numa resposta. Ela não vai à IA: guarde-a na conta da persona (Personas → a pessoa →
          “Contas e acesso”), e a automação a digita de lá.
        </p>
      ) : null}
      {erro ? <p className={cx(styles.aviso, styles.avisoPerigo)} role="alert">{erro}</p> : null}

      <footer className={styles.rodape}>
        <Button size="sm" icon={atual || pendentes > 0 ? MessageSquareReply : Sparkles} loading={carregando}
                variant={atual && pronto ? undefined : 'primary'}
                disabledReason={comSenha ? 'Tire a senha antes de continuar.'
                  : faltaResponder ? 'Responda a ao menos uma pergunta (ou ajuste o texto e refine de novo).'
                  : base.trim().length < 3 ? 'Escreva o comando primeiro.' : null}
                onClick={() => void refinar()}>
          {pendentes > 0 ? 'Responder e refinar' : atual ? 'Refinar de novo' : 'Refinar com IA'}
        </Button>
        {rodadas.length > 0 ? (
          <Button size="sm" variant="ghost" icon={ArrowLeft} disabled={carregando} onClick={voltar}>
            {rodadas.length > 1 ? 'Desfazer a última rodada' : 'Voltar ao texto original'}
          </Button>
        ) : null}
        <span className={styles.espaco} />
        {acoes(base.trim(), pronto)}
      </footer>
    </section>
  );
}
