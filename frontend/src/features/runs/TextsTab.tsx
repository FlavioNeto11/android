import { CheckCircle2, MessageSquareQuote, RotateCcw, Send, Undo2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { api, hintForError, personaImageUrl, toApiError } from '../../api/client';
import type { Approval, ApprovalDecisionItem, RunDetail } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { TextArea } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { toast, toastError } from '../../store/toasts';
import styles from './Runs.module.css';

/** Texto em edição e a escolha do usuário para cada rascunho. */
type Escolha = 'enviar' | 'descartar';

export interface RunApprovals {
  itens: Approval[] | null;
  erro: { message: string; hint: string } | null;
  recarregar: () => Promise<void>;
  /** O que a pessoa escreveu e escolheu. Vive aqui, e não na aba, para sobreviver à troca de aba. */
  texto: Record<string, string>;
  setTexto: Dispatch<SetStateAction<Record<string, string>>>;
  escolha: Record<string, Escolha>;
  setEscolha: Dispatch<SetStateAction<Record<string, Escolha>>>;
}

/**
 * Os rascunhos pendentes desta execução — um por perfil, agora que cada um escreve o seu texto.
 * Fica no RunView para que a aba mostre a contagem sem uma segunda chamada.
 *
 * O texto em edição mora aqui pelo mesmo motivo: a aba é desmontada ao trocar de aba, e quem reescreveu oito
 * textos não pode perdê-los por ter ido conferir uma evidência.
 */
export function useRunApprovals(runId: string, token = ''): RunApprovals {
  const [itens, setItens] = useState<Approval[] | null>(null);
  const [erro, setErro] = useState<{ message: string; hint: string } | null>(null);
  const [texto, setTexto] = useState<Record<string, string>>({});
  const [escolha, setEscolha] = useState<Record<string, Escolha>>({});

  const recarregar = useCallback(async () => {
    try {
      const lista = await api.listApprovals('pending', undefined, runId);
      setItens(lista);
      setErro(null);
    } catch (e) {
      // A causa vem do backend e a dica é nossa: mostrar só a dica esconde justamente o que aconteceu.
      const err = toApiError(e);
      setErro({ message: err.message, hint: hintForError(err) });
      setItens([]);
    }
    // `token` muda quando a execução muda de situação: é o gatilho para buscar de novo.
  }, [runId, token]);

  useEffect(() => { void recarregar(); }, [recarregar]);
  return { itens, erro, recarregar, texto, setTexto, escolha, setEscolha };
}

/** Aprovação de ação sem texto (seguir, aceitar pedido): decide-se, mas não se escreve. */
function temTexto(a: Approval): boolean {
  return a.generated_content !== null;
}

function verboDe(a: Approval, texto: string | undefined, escolha: Escolha): ApprovalDecisionItem {
  if (escolha === 'descartar') return { id: a.id, verb: 'reject' };
  // Sem texto gerado, `edit` gravaria um conteúdo numa etapa que não escreve nada — e o texto entraria na guarda
  // de commit, que nunca seria satisfeita na tela. Aprovar é a única decisão que faz sentido aqui.
  const mudou = temTexto(a) && texto !== undefined && texto.trim() !== '' && texto !== (a.content ?? '');
  return mudou ? { id: a.id, verb: 'edit', content: texto } : { id: a.id, verb: 'approve' };
}

export function TextsTab({ detail, approvals }: { detail: RunDetail; approvals: RunApprovals }) {
  const { itens, erro, recarregar, texto, setTexto, escolha, setEscolha } = approvals;
  const [enviando, setEnviando] = useState(false);
  const [recusados, setRecusados] = useState<{ id: string; reason: string }[]>([]);

  // De qual aparelho veio cada rascunho: é o que torna a leitura lado a lado útil.
  const aparelhoDe = useMemo(() => {
    const m = new Map<string, string>();
    for (const o of detail.objectives) m.set(o.id, o.instance_id);
    return m;
  }, [detail.objectives]);

  const decisoes = useMemo(
    () => (itens ?? []).map((a) => verboDe(a, texto[a.id], escolha[a.id] ?? 'enviar')),
    [itens, texto, escolha],
  );
  const editados = decisoes.filter((d) => d.verb === 'edit').length;
  const descartados = decisoes.filter((d) => d.verb === 'reject').length;
  // Caixa esvaziada não é "sem mudança": é um texto que a pessoa tirou e ainda não reescreveu. Decidir assim
  // aprovaria justamente o texto apagado — o backend recusa conteúdo vazio, mas `approve` nem chega a mandá-lo.
  const emBranco = (itens ?? []).filter(
    (a) => temTexto(a) && (escolha[a.id] ?? 'enviar') === 'enviar' && (texto[a.id] ?? a.content ?? '').trim() === '',
  ).length;
  const escritos = (itens ?? []).filter(temTexto).length;

  async function decidirTudo() {
    if (decisoes.length === 0) return;
    setEnviando(true);
    try {
      const r = await api.decideApprovals(decisoes);
      setRecusados(r.refused);
      setTexto({});
      setEscolha({});
      await recarregar();
      toast({
        tone: r.refused.length > 0 ? 'info' : 'success',
        title: `${r.decided.length} decidido(s)`,
        message: r.refused.length > 0
          ? `${r.refused.length} não pôde(puderam) ser decidido(s) — veja o aviso na aba.`
          : 'Os textos aprovados voltam para a fila de execução.',
      });
    } catch (e) {
      toastError('Não foi possível decidir em lote', e);
    } finally {
      setEnviando(false);
    }
  }

  if (itens === null) {
    return (
      <LoadingRegion label="Carregando os textos…" className={styles.stack}>
        <Skeleton height={96} radius={8} />
        <Skeleton height={96} radius={8} />
      </LoadingRegion>
    );
  }

  if (erro && itens.length === 0) {
    return (
      <EmptyState
        icon={MessageSquareQuote}
        tone="danger"
        compact
        title="Textos indisponíveis"
        hint={erro.hint}
        actions={<Button variant="outline" icon={RotateCcw} onClick={() => void recarregar()}>Carregar de novo</Button>}
      >
        {erro.message}
      </EmptyState>
    );
  }

  if (itens.length === 0) {
    return (
      <EmptyState
        icon={CheckCircle2}
        compact
        title="Nenhum texto aguardando aprovação"
        hint="Quando uma etapa escreve (comentário, resposta ou mensagem), cada perfil gera o seu texto e ele aparece aqui antes de ser digitado no aparelho."
      />
    );
  }

  return (
    <div className={styles.stack}>
      {recusados.length > 0 ? (
        <Banner tone="warning" icon={Undo2} title={`${recusados.length} decisão(ões) recusada(s)`} role="status">
          <ul className={styles.skippedList}>
            {recusados.map((r) => <li key={r.id}><span className="mono">{r.id.slice(0, 8)}</span>: {r.reason}</li>)}
          </ul>
        </Banner>
      ) : null}

      <Banner tone="info" icon={MessageSquareQuote} title={`${escritos} texto(s) desta execução, lado a lado`} role="status">
        Cada perfil escreveu o seu, na própria voz. Leia todos, ajuste o que quiser e decida de uma vez — nada é digitado
        no aparelho antes disso.
        {escritos < itens.length ? (
          <> Há também {itens.length - escritos} ação(ões) sem texto (seguir, aceitar pedido) esperando decisão.</>
        ) : null}
      </Banner>

      <ul className={styles.drafts} aria-label="Textos aguardando aprovação">
        {itens.map((a) => {
          const alvo = escolha[a.id] ?? 'enviar';
          const aparelho = aparelhoDe.get(a.objective_id ?? '') ?? 'execução';
          const escreve = temTexto(a);
          const atual = texto[a.id] ?? a.content ?? '';
          const editado = escreve && alvo === 'enviar' && atual.trim() !== '' && atual !== (a.content ?? '');
          const branco = escreve && alvo === 'enviar' && atual.trim() === '';
          return (
            <li key={a.id} className={styles.draft} data-descartado={alvo === 'descartar' ? '' : undefined}>
              <div className={styles.draftHead}>
                <span className={styles.eventInst}>{aparelho}</span>
                <span className={styles.draftWhat}>{a.capability}{a.target ? ` · ${a.target}` : ''}</span>
                {editado ? <Badge tone="info">editado</Badge> : null}
                {branco ? <Badge tone="warning">em branco</Badge> : null}
                {alvo === 'descartar' ? <Badge tone="warning">não será enviado</Badge> : null}
                {a.rotulo_ia ? <Badge tone="neutral">com rótulo de IA</Badge> : null}
                {a.rotulo_ia === false ? <Badge tone="warning">sem rótulo de IA (imagem enviada por você)</Badge> : null}
              </div>
              {a.image_id && a.profile_id ? (
                // 29.30: a publicação leva esta imagem; quem aprova a legenda vê também o que vai ao feed.
                <img className={styles.draftImagem} src={personaImageUrl(a.profile_id, a.image_id)}
                     alt={`Imagem que será publicada por ${aparelho}`} loading="lazy" />
              ) : null}
              {escreve ? (
                <TextArea
                  rows={3}
                  aria-label={`Texto de ${aparelho}`}
                  value={atual}
                  // `readOnly` em vez de `disabled`: o leitor de tela continua podendo ler o que vai ser descartado.
                  readOnly={alvo === 'descartar'}
                  aria-disabled={alvo === 'descartar' ? true : undefined}
                  onChange={(e) => setTexto((s) => ({ ...s, [a.id]: e.target.value }))}
                />
              ) : (
                // Ação sem texto não ganha caixa: a caixa convidaria a escrever, e o que fosse escrito viraria
                // guarda de commit de uma etapa que não digita nada — guarda que a tela nunca satisfaz.
                <p className={styles.draftWhat}>{a.summary} — esta ação não escreve nada, só precisa do seu aval.</p>
              )}
              <div className={styles.draftActions}>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={alvo === 'descartar' ? Undo2 : undefined}
                  label={`${alvo === 'descartar' ? 'Voltar a enviar' : 'Não enviar este'} — ${aparelho}`}
                  onClick={() => setEscolha((s) => ({ ...s, [a.id]: alvo === 'descartar' ? 'enviar' : 'descartar' }))}
                >
                  {alvo === 'descartar' ? 'Voltar a enviar' : 'Não enviar este'}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>

      <div className={styles.draftBar}>
        <span className={styles.draftTally}>
          {itens.length - descartados} para enviar
          {editados > 0 ? ` · ${editados} editado(s)` : ''}
          {descartados > 0 ? ` · ${descartados} descartado(s)` : ''}
        </span>
        <Button
          variant="primary"
          icon={Send}
          loading={enviando}
          disabledReason={emBranco > 0
            ? `${emBranco} texto(s) em branco: escreva o texto ou marque “Não enviar este”.`
            : null}
          onClick={() => void decidirTudo()}
        >
          Decidir os {itens.length} de uma vez
        </Button>
      </div>
    </div>
  );
}
