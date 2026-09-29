import { ThumbsDown, ThumbsUp, Undo2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { hintForError, toApiError } from '../../api/client';
import { Button } from '../../components/Button';
import { Field, Select, TextArea } from '../../components/Field';
import { cx } from '../../lib/format';
import { toastError } from '../../store/toasts';
import { apiAprendizado } from '../aprendizado/api';
import {
  type CorpoDoVoto, type EfeitoDoVoto, type FeedbackDaExecucao, type Motivo, MOTIVOS, NOTA_MAX, type RespostaDoVoto,
  type Veredito, type Voto, desfazerDoEfeito, isMotivo, rotuloDoMotivo, textoDoEfeito,
} from '../aprendizado/model';
import styles from '../aprendizado/Aprendizado.module.css';

/**
 * Os votos e os sinais implícitos de uma execução (`GET /api/runs/{id}/feedback`, ADR-054/A4), lidos UMA vez por
 * execução e repartidos entre os itens. Falha de leitura é silenciosa: sem a rota (backend anterior ao A4) o botão
 * continua funcionando, só não mostra o voto antigo. `undefined` = ainda lendo; `null` = o servidor não informou.
 */
export function useFeedbackDaExecucao(runId: string, recarregarQuando?: string | number | null): FeedbackDaExecucao | null | undefined {
  // Guarda DE QUAL execução é a leitura: trocar de execução não mostra, nem por um instante, os votos da anterior.
  const [lido, setLido] = useState<{ runId: string; fb: FeedbackDaExecucao | null } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    apiAprendizado.feedback(runId, ctl.signal)
      .then((f) => {
        if (!ctl.signal.aborted) setLido({ runId, fb: f });
      })
      .catch(() => {
        if (!ctl.signal.aborted) setLido({ runId, fb: null });
      });
    return () => ctl.abort();
  }, [runId, recarregarQuando]);
  return lido && lido.runId === runId ? lido.fb : undefined;
}

const NOTA_RECUSADA = 'A nota parece conter uma senha ou chave e não foi gravada — nada foi registrado. Tire esse trecho e envie de novo.';

function mensagemDeErro(e: unknown): string {
  const api = toApiError(e);
  if (api.code === 'note_looks_secret') return NOTA_RECUSADA;
  // A rota inexistente (backend anterior ao A4) responde o 404 cru do FastAPI, sem `code` do contrato.
  if (api.status === 404 && api.code === 'http_404') return 'Este servidor ainda não recebe votos: falta implantar o aprendizado (ADR-054).';
  return `${api.message} ${hintForError(api)}`.trim();
}

interface FeedbackItemProps {
  runId: string;
  /** Sem ele, o voto vale para a execução inteira (`source_ref = run:<id>`). */
  objectiveId?: string | null;
  /** O voto já dado (de `GET …/feedback`); aparece marcado. */
  voto: Voto | null;
  /** Execução simulada: o voto é gravado, mas não rebaixa nada real. */
  simulada?: boolean;
  onVotou?: (v: Voto) => void;
}

/**
 * "Deu certo / Deu errado" (D2 do ADR-054). Nunca modal e nunca pergunta sozinho: fica ao lado das outras ações do
 * item, inclusive no concluído e no que falhou. "Deu errado" abre, EM LINHA, o motivo (vocabulário fechado) e uma
 * nota opcional; depois do voto, uma linha diz o que mudou e oferece desfazer em um clique. Votar de novo troca o voto,
 * mas não reativa sozinho o que foi desligado — para isso existe o desfazer.
 */
export function FeedbackItem({ runId, objectiveId = null, voto, simulada, onVotou }: FeedbackItemProps) {
  const [atual, setAtual] = useState<Voto | null>(voto);
  const [aberto, setAberto] = useState(false);
  const [motivo, setMotivo] = useState<Motivo | ''>('');
  const [nota, setNota] = useState('');
  const [enviando, setEnviando] = useState<Veredito | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [resposta, setResposta] = useState<RespostaDoVoto | null>(null);
  const [desfeitos, setDesfeitos] = useState<ReadonlySet<string>>(() => new Set());
  const [desfazendo, setDesfazendo] = useState<string | null>(null);

  // O voto lido do servidor chega depois da montagem (e muda quando outra aba vota): acompanha-o.
  const verdictDoServidor = voto?.verdict ?? null;
  const motivoDoServidor = voto?.reason ?? null;
  useEffect(() => {
    if (verdictDoServidor) setAtual({ objective_id: objectiveId, verdict: verdictDoServidor, reason: motivoDoServidor, created_by: null });
  }, [verdictDoServidor, motivoDoServidor, objectiveId]);

  const alvo = objectiveId ? 'este item' : 'esta execução';

  const votar = async (verdict: Veredito, reason?: Motivo, note?: string) => {
    if (enviando) return;
    setEnviando(verdict);
    setErro(null);
    const corpo: CorpoDoVoto = {
      ...(objectiveId ? { objective_id: objectiveId } : {}),
      verdict,
      ...(reason ? { reason } : {}),
      ...(note ? { note } : {}),
    };
    try {
      const r = await apiAprendizado.votar(runId, corpo);
      const novo: Voto = { objective_id: objectiveId, verdict, reason: reason ?? null, created_by: null };
      setAtual(novo);
      setResposta(r);
      setDesfeitos(new Set());
      setAberto(false);
      setMotivo('');
      setNota('');
      onVotou?.(novo);
    } catch (e) {
      // A nota NUNCA sai daqui (nem em toast, nem em log): fica só no campo, para a pessoa editar.
      setErro(mensagemDeErro(e));
    } finally {
      setEnviando(null);
    }
  };

  const desfazer = async (ef: EfeitoDoVoto) => {
    const volta = desfazerDoEfeito(ef);
    if (!volta) return;
    const chave = `${ef.kind}:${ef.ref}`;
    setDesfazendo(chave);
    try {
      await apiAprendizado.mudarEstado(volta.kind, volta.ref, volta.to, volta.reason);
      setDesfeitos((antes) => new Set([...antes, chave]));
    } catch (e) {
      toastError('Não foi possível desfazer', e);
    } finally {
      setDesfazendo(null);
    }
  };

  const efeitoDoMotivo = motivo ? MOTIVOS.find((m) => m.id === motivo)?.efeito : null;

  return (
    <div className={styles.feedback} role="group" aria-label={`Avaliar ${alvo}`}>
      <div className={styles.feedbackLinha}>
        <span className={styles.feedbackRotulo}>{objectiveId ? 'Este item deu certo?' : 'Esta execução deu certo?'}</span>
        <Button size="sm" variant="secondary" icon={ThumbsUp} className={styles.votado}
                aria-pressed={atual?.verdict === 'certo'} loading={enviando === 'certo'}
                onClick={() => {
                  setAberto(false);
                  void votar('certo');
                }}>
          Deu certo
        </Button>
        <Button size="sm" variant="secondary" icon={ThumbsDown} className={cx(styles.votado, styles.votadoErrado)}
                aria-pressed={atual?.verdict === 'errado'} aria-expanded={aberto}
                onClick={() => {
                  setAberto((a) => !a);
                  setErro(null);
                }}>
          Deu errado
        </Button>
        {atual?.verdict === 'errado' && atual.reason ? (
          <span className={styles.feedbackRotulo}>Motivo: {rotuloDoMotivo(atual.reason)}</span>
        ) : null}
      </div>

      {aberto ? (
        <form className={styles.feedbackForm}
              onSubmit={(e) => {
                e.preventDefault();
                if (motivo) void votar('errado', motivo, nota.trim() || undefined);
              }}>
          <Field label="Motivo" className={styles.feedbackMotivo}
                 hint={efeitoDoMotivo ? `Efeito: ${efeitoDoMotivo}.` : 'Escolha o que deu errado.'}>
            {({ id, describedBy }) => (
              <Select id={id} small aria-describedby={describedBy} value={motivo}
                      onChange={(e) => setMotivo(isMotivo(e.target.value) ? e.target.value : '')}>
                <option value="">Escolha…</option>
                {MOTIVOS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Nota (opcional)" unit={`${nota.length}/${NOTA_MAX}`} className={styles.feedbackNota}
                 hint="Não escreva senha nem código: a nota com cara de credencial é recusada e nada é gravado.">
            {({ id, describedBy }) => (
              <TextArea id={id} aria-describedby={describedBy} rows={2} maxLength={NOTA_MAX} value={nota}
                        onChange={(e) => setNota(e.target.value)} />
            )}
          </Field>
          <div className={styles.feedbackFormAcoes}>
            <Button type="submit" size="sm" variant="danger" loading={enviando === 'errado'}
                    disabledReason={motivo ? null : 'Escolha o motivo.'}>
              Enviar
            </Button>
            <Button size="sm" variant="ghost" onClick={() => { setAberto(false); setErro(null); }}>Cancelar</Button>
            {simulada ? <span className={styles.feedbackRotulo}>Execução simulada: o voto é gravado, mas não rebaixa nada real.</span> : null}
          </div>
          {erro ? <p className={styles.erroInline} role="alert">{erro}</p> : null}
        </form>
      ) : erro ? <p className={styles.erroInline} role="alert">{erro}</p> : null}

      {resposta ? (
        <div className={styles.mudou} role="status">
          <span>{resposta.resumo || (resposta.efeitos.length === 0 ? 'Voto registrado; nada foi rebaixado.' : 'Voto registrado.')}</span>
          {resposta.efeitos.map((ef) => {
            const chave = `${ef.kind}:${ef.ref}`;
            const volta = desfazerDoEfeito(ef);
            return (
              <span key={chave} className={styles.mudouEfeito}>
                <span>{textoDoEfeito(ef)}</span>
                {volta && desfeitos.has(chave) ? <strong>desfeito</strong> : null}
                {volta && !desfeitos.has(chave) ? (
                  <Button size="sm" variant="ghost" icon={Undo2} loading={desfazendo === chave}
                          onClick={() => void desfazer(ef)}>
                    {volta.to === 'published' ? 'Reativar' : 'Desfazer'}
                  </Button>
                ) : null}
              </span>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
