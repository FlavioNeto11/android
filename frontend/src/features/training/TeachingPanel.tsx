/**
 * Ensino v2 (fase F) dentro da revisão do treino — só com `health.features.skills`. A gravação vira uma CANDIDATA de
 * habilidade versionada (documento `automation/v1alpha1`): o painel mostra o comando, os parâmetros inferidos com
 * tipo e exemplo, as etapas e os riscos, e as PERGUNTAS que o generalizador fez em vez de inventar. Respondidas, a
 * pessoa pede outra candidata; validada, ela vira RASCUNHO de versão. Publicar a habilidade é outra decisão, fora
 * daqui. O "Salvar habilidade" de sempre (fluxo) continua ao lado, sem ponte entre os dois.
 */
import { FileCheck2, MessageCircleQuestion, Sparkles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { SkillCandidate, TeachingSessionView, TeachingStatus } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { TextInput } from '../../components/Field';
import { toast, toastError } from '../../store/toasts';
import styles from './Training.module.css';

const STATUS: Record<TeachingStatus, string> = {
  open: 'aberto', demonstrating: 'gravando', proposing: 'gerando', asking: 'com perguntas', validating: 'a validar',
  ready: 'pronto', published: 'virou rascunho', discarded: 'descartado',
};

function chave(): string {
  return `ensino-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function titulo(no: SkillCandidate['document']['spec']['nodes'][number]): string {
  return no.goal?.title ?? no.capability ?? no.id;
}

export function TeachingPanel({ trainingSessionId, intent, appId }: { trainingSessionId: string; intent: string; appId: string | null }) {
  const [ensino, setEnsino] = useState<TeachingSessionView | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [respostas, setRespostas] = useState<Record<number, string>>({});

  useEffect(() => {
    let vivo = true;
    void api.teachingOfRecording(trainingSessionId)
      .then(async (lista) => (lista[0] ? api.getTeaching(lista[0].id) : null))
      .then((v) => { if (vivo) setEnsino(v); })
      .catch(() => undefined);
    return () => { vivo = false; };
  }, [trainingSessionId]);

  async function executar(acao: () => Promise<TeachingSessionView>, erro: string) {
    setOcupado(true);
    try {
      setEnsino(await acao());
    } catch (e) {
      toastError(erro, e);
    } finally {
      setOcupado(false);
    }
  }

  const criar = () => executar(async () => {
    const novo = await api.startTeaching({ instruction: intent, app_id: appId });
    await api.attachRecording(novo.id, trainingSessionId);
    return api.proposeCandidate(novo.id, chave());
  }, 'Não foi possível gerar a candidata');

  const responder = (id: string, perguntaId: number) => executar(async () => {
    const v = await api.answerQuestion(id, perguntaId, (respostas[perguntaId] ?? '').trim());
    setRespostas((r) => ({ ...r, [perguntaId]: '' }));
    return v;
  }, 'Não foi possível registrar a resposta');

  const salvarRascunho = (v: TeachingSessionView, candidata: SkillCandidate) => executar(async () => {
    const validado = v.status === 'validating' ? await api.validateCandidate(candidata.id) : v;
    if (validado.status !== 'ready') return validado;         // não compilou: a nota do compilador aparece abaixo
    const salvo = await api.publishCandidate(candidata.id);
    toast({ tone: 'success', title: 'Rascunho de habilidade criado', message: `${salvo.result_version_id} — publicar é outra decisão.` });
    return salvo;
  }, 'Não foi possível salvar o rascunho');

  const candidata = ensino?.current_candidate ?? null;
  const nota = ensino ? [...ensino.turns].reverse().find((t) => t.kind === 'note' && t.candidate_id === candidata?.id && t.body) : undefined;

  return (
    <fieldset className={styles.scope} aria-label="Habilidade versionada">
      <legend>Habilidade versionada (ensino v2)</legend>
      {!ensino ? (
        <div className={styles.ask}>
          <p>Gera uma candidata de habilidade versionada a partir desta gravação: comando, parâmetros com tipo e exemplo,
            etapas e riscos. O que só você sabe vira pergunta. Uma chamada do modelo do planejador.</p>
          <Button variant="outline" icon={Sparkles} loading={ocupado} onClick={() => void criar()}>Gerar candidata de habilidade</Button>
        </div>
      ) : (
        <>
          <p className={styles.muted}>Ensino <Badge size="sm" tone={ensino.status === 'published' ? 'success' : 'info'}>{STATUS[ensino.status]}</Badge>
            {candidata ? <> · candidata {candidata.seq} ({candidata.status})</> : null}</p>
          {candidata ? (
            <>
              <code className={styles.command}>{candidata.document.spec.invocation.command_template}</code>
              {candidata.annotations.parameters.length ? (
                <p className={styles.params}>
                  {candidata.annotations.parameters.map((p) => (
                    <Badge key={p.name} size="sm" tone="accent">{`{${p.name}}`} · {p.type}{p.examples.length ? ` = ${p.examples.join(', ')}` : ''}</Badge>
                  ))}
                </p>
              ) : null}
              <ol className={styles.steps} aria-label="Etapas da candidata">
                {candidata.document.spec.nodes.map((n) => (
                  <li key={n.id} className={styles.step}>
                    <div className={styles.stepHead}>
                      <span>{titulo(n)}</span>
                      {n.capability ? <Badge size="sm" tone="neutral">{n.capability}</Badge> : null}
                      {n.side_effect || candidata.annotations.effects.some((e) => e.node === n.id)
                        ? <Badge size="sm" tone="warning">efeito externo</Badge> : null}
                    </div>
                  </li>
                ))}
              </ol>
              {candidata.annotations.risks.length ? (
                <ul className={styles.questions} aria-label="Riscos">{candidata.annotations.risks.map((r) => <li key={r}>{r}</li>)}</ul>
              ) : null}
            </>
          ) : null}
          {nota && candidata?.status === 'rejected' ? <p className={styles.muted}>Candidata recusada: {nota.body}</p> : null}
          {ensino.errors.length ? (
            <ul className={styles.questions} aria-label="Erros de compilação">{ensino.errors.map((e) => <li key={e}>{e}</li>)}</ul>
          ) : null}
          {ensino.open_questions.map((q) => (
            <div key={q.id} className={styles.step}>
              <p><MessageCircleQuestion size={14} aria-hidden /> {q.text}</p>
              <div className={styles.stepHead}>
                <TextInput aria-label={`Resposta à pergunta ${q.id}`} value={respostas[q.id] ?? ''}
                           onChange={(e) => setRespostas((r) => ({ ...r, [q.id]: e.target.value }))} />
                <Button size="sm" variant="primary" loading={ocupado}
                        disabledReason={(respostas[q.id] ?? '').trim() ? null : 'Escreva a resposta (sem senha nem código).'}
                        onClick={() => void responder(ensino.id, q.id)}>Responder</Button>
              </div>
            </div>
          ))}
          <div className={styles.actions}>
            {(ensino.status === 'asking' && ensino.open_questions.length === 0) || ensino.status === 'open' ? (
              <Button size="sm" variant="outline" icon={Sparkles} loading={ocupado}
                      onClick={() => void executar(() => api.proposeCandidate(ensino.id, chave()), 'Não foi possível gerar a candidata')}>
                {ensino.status === 'asking' ? 'Gerar de novo com as respostas' : 'Pedir outra candidata'}
              </Button>
            ) : null}
            {candidata && (ensino.status === 'validating' || ensino.status === 'ready') ? (
              <Button size="sm" variant="primary" icon={FileCheck2} loading={ocupado} onClick={() => void salvarRascunho(ensino, candidata)}>
                Salvar como rascunho
              </Button>
            ) : null}
          </div>
          {ensino.status === 'published' ? (
            <p>Rascunho <strong>{ensino.result_version_id}</strong> criado. Publicar a habilidade é outra decisão (Configurações → Fluxos e receitas → Habilidades).</p>
          ) : null}
        </>
      )}
    </fieldset>
  );
}
