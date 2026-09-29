/**
 * "Corrigir esta etapa" (plano 22.7): a correção de ensino nasce na VISÃO DA EXECUÇÃO, onde a pessoa vê a etapa que a
 * habilidade errou — não na tela de treinamento, que só conhece gravações. Só aparece na etapa `failed`/`uncertain`
 * que veio de uma habilidade (`origin` no passo do plano): a correção é SEMPRE de uma habilidade existente
 * (`correction_needs_skill`), e o backend só aceita etapa que falhou ou ficou sem prova (`CORRECTABLE_STEP`).
 *
 * Enviar faz dois pedidos: acha (ou abre) o ensino da habilidade e posta nele a correção com a execução e a LINHA de
 * `steps` (`Step.id`, nunca a `key` do plano). O texto passa pela triagem de credencial do ensino (400
 * `credential_in_text`: nem turno, nem sinal) e depois pela do livro, que grava o sinal `correcao_de_ensino` sem a nota
 * quando ela só FALA de credencial. Em linha, nunca modal (o padrão do `FeedbackItem`). A linha recolhida da etapa leva
 * a marca "corrigível" pela mesma regra (`MarcaCorrigivel`).
 */
import { GraduationCap, PencilLine } from 'lucide-react';
import { useId, useRef, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { RunDetail, Step, StepOrigin, StepStatus, TeachingSessionView } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Field, TextArea } from '../../components/Field';
import { useAppStore } from '../../store/app';
import { useUiStore } from '../../store/ui';
import styles from './CorrigirEtapa.module.css';

/** Teto do texto da pessoa no ensino (`teaching.py::MAX_TEXT`, `CorrectionBody.body`). */
export const CORRECAO_MAX = 2000;

/** `teaching.py::CORRECTABLE_STEP`: a etapa que falhou ou ficou sem prova. */
export const ETAPA_CORRIGIVEL: ReadonlySet<StepStatus> = new Set<StepStatus>(['failed', 'uncertain']);

/** Quantos ensinos abertos da mesma habilidade se abrem para achar o desta execução (cada um é um GET). */
const ABERTOS_CONFERIDOS = 10;

/**
 * A origem da etapa: o passo da MESMA versão do plano do objetivo (`plan_versions`, gravado com a lista que virou as
 * linhas de `steps`, cópias do `for_each` inclusive), pela `key`; sem ela, o plano da execução. `null` = a etapa não
 * veio de habilidade (plano do planejador).
 */
export function origemDaEtapa(detail: Pick<RunDetail, 'plan' | 'plan_versions'>,
                              s: Pick<Step, 'objective_id' | 'plan_version' | 'key'>): StepOrigin | null {
  const versao = detail.plan_versions.find((v) => v.objective_id === s.objective_id && v.version === s.plan_version);
  const passo = versao?.steps.find((p) => p.key === s.key) ?? detail.plan?.steps.find((p) => p.key === s.key);
  return passo?.origin ?? null;
}

/**
 * O texto de abertura do ensino. Não pode ficar vazio: sem instrução nem gravação, pedir a versão corrigida depois
 * morre em `nothing_to_teach`. Só com o id da habilidade e o número da versão — nunca o texto da pessoa, o id da
 * execução ou o título da etapa, que podem ter palavra com cara de senha e fazer a triagem recusar a abertura.
 */
export function instrucaoDoEnsino(o: StepOrigin): string {
  return `Corrigir a habilidade ${o.skill_id} (versão ${o.skill_version}).`;
}

function corrigeEstaExecucao(v: TeachingSessionView, runId: string): boolean {
  return v.turns.some((t) => t.kind === 'correction' && t.target?.run_id === runId);
}

/** Aberto por esta ação e sem nada dentro (a correção falhou depois de abrir): serve a qualquer execução. */
function vazioDestaAcao(v: TeachingSessionView, o: StepOrigin): boolean {
  return v.instruction === instrucaoDoEnsino(o) && v.demonstrations.length === 0
    && !v.turns.some((t) => t.kind === 'correction');
}

/**
 * O ensino onde a correção entra. O backend não busca por habilidade nem por execução: lê os abertos, fica com os da
 * mesma habilidade e versão e abre cada um até achar o que já corrige esta execução (ou um vazio aberto por aqui).
 * Falha na leitura não impede a correção: abre um ensino novo.
 */
async function acharEnsinoAberto(runId: string, o: StepOrigin): Promise<string | null> {
  let abertos;
  try {
    abertos = await api.listTeaching({ status: 'open', limit: 200 });
  } catch {
    return null;
  }
  const mesmos = abertos.filter((s) => s.skill_id === o.skill_id && (s.base_version ?? null) === o.skill_version)
    .slice(0, ABERTOS_CONFERIDOS);
  let vazio: string | null = null;
  for (const s of mesmos) {
    let v: TeachingSessionView;
    try {
      v = await api.getTeaching(s.id);
    } catch {
      continue;
    }
    if (corrigeEstaExecucao(v, runId)) return v.id;
    if (vazio === null && vazioDestaAcao(v, o)) vazio = v.id;
  }
  return vazio;
}

type Fase = 'ensino' | 'correcao';

// Recusa na fase da correção: o ensino pode já ter sido aberto um pedido antes (fica aberto e vazio, e a próxima
// correção desta habilidade o reaproveita). O que não foi gravado é a correção, não "nada".
const TEXTO_RECUSADO = 'A correção parece conter uma senha, um código ou uma chave e não foi gravada. Tire esse '
  + 'trecho e envie de novo.';

function mensagemDeErro(e: unknown, fase: Fase, o: StepOrigin): string {
  const err = toApiError(e);
  const habilidade = `${o.skill_id} (versão ${o.skill_version})`;
  switch (err.code) {
    case 'credential_in_text':
    case 'note_looks_secret':
      return fase === 'correcao' ? TEXTO_RECUSADO
        : `O servidor não abriu o ensino da habilidade ${habilidade}: o texto de abertura foi lido como credencial. Nada foi registrado.`;
    case 'skills_disabled':
      return 'As habilidades estão desligadas neste servidor: não há ensino onde registrar a correção.';
    case 'step_not_correctable':
      return 'Esta etapa não está mais como “falhou” ou “sem prova”, e só essas se corrigem. Recarregue a execução para ver a situação atual.';
    case 'correction_needs_skill':
      return 'Esta etapa não veio de uma habilidade: a correção precisa de uma habilidade para melhorar.';
    case 'unknown_app':
      return `O aplicativo da habilidade ${habilidade} não está cadastrado neste servidor: cadastre-o em Aplicativos e tente de novo.`;
    case 'teaching_state':
      return 'O ensino desta habilidade seguiu adiante enquanto você escrevia e não aceita mais correção. Envie de novo: a correção vai para outro ensino aberto desta habilidade, ou para um novo.';
    case 'validation':
      return `O texto da correção não foi aceito: escreva de 1 a ${CORRECAO_MAX} caracteres.`;
    default:
      break;
  }
  if (err.status === 404) {
    return fase === 'ensino'
      ? `A habilidade ${habilidade} não existe mais neste servidor: não há onde registrar a correção.`
      : 'A etapa não foi encontrada nesta execução, ou o ensino não existe mais. Recarregue a execução e tente de novo.';
  }
  // As outras recusas do ensino já vêm em português ("A correção está vazia.").
  if (err.status === 400) return err.message;
  return `${err.message} ${hintForError(err)}`.trim();
}

interface Feito {
  teachingId: string;
  origem: StepOrigin;
}

type DetalheDaCorrecao = Pick<RunDetail, 'id' | 'plan' | 'plan_versions'>;

/**
 * A origem da etapa quando ela se corrige aqui; `null` quando não cabe: habilidades desligadas
 * (`health.features.skills`), etapa que não falhou nem ficou sem prova, ou etapa que não veio de habilidade. Uma só
 * regra para a ação e para a marca da linha recolhida: marca "corrigível" sem a ação no detalhe seria pior que nada.
 */
function useOrigemCorrigivel(detail: DetalheDaCorrecao, step: Step): StepOrigin | null {
  const ligado = useAppStore((st) => st.health?.features?.skills === true);
  if (!ligado || !ETAPA_CORRIGIVEL.has(step.status)) return null;
  return origemDaEtapa(detail, step);
}

/**
 * A marca na linha RECOLHIDA da etapa: a ação mora no detalhe, e o `StepTable` começa com tudo fechado. Sem ela, a
 * etapa que a habilidade errou não daria sinal de que se corrige.
 */
export function MarcaCorrigivel({ detail, step }: { detail: DetalheDaCorrecao; step: Step }) {
  const origem = useOrigemCorrigivel(detail, step);
  if (!origem) return null;
  return (
    <Badge tone="info" icon={PencilLine} size="sm" className={styles.marca}
           title={`Veio da habilidade ${origem.skill_id} (versão ${origem.skill_version}): abra a etapa para corrigir no ensino.`}>
      corrigível<span className="sr-only"> no ensino da habilidade {origem.skill_id}</span>
    </Badge>
  );
}

/** A ação em si, no detalhe da etapa. Devolve `null` quando não cabe (a mesma regra da marca). */
export function CorrigirEtapa({ detail, step }: { detail: DetalheDaCorrecao; step: Step }) {
  const origem = useOrigemCorrigivel(detail, step);
  if (!origem) return null;
  return <Formulario runId={detail.id} step={step} origem={origem} />;
}

function Formulario({ runId, step, origem }: { runId: string; step: Step; origem: StepOrigin }) {
  const [aberto, setAberto] = useState(false);
  const [texto, setTexto] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [feito, setFeito] = useState<Feito | null>(null);
  // O ensino desta ação: entre os dois pedidos e entre um envio e outro, a correção recusada que volta corrigida
  // entra no MESMO ensino, em vez de deixar ensinos vazios para trás.
  const ensino = useRef<string | null>(null);
  const formId = useId();
  // Abrir leva o foco ao campo (`autoFocus`, só quando o formulário nasce); fechar, enviado ou cancelado, o devolve
  // ao botão, e não ao `body`: o botão que tinha o foco (Enviar, Cancelar) sai da tela junto com o formulário. No
  // manipulador, não num efeito: o `StrictMode` roda o efeito duas vezes na montagem e roubaria o foco da página.
  const botao = useRef<HTMLButtonElement>(null);

  const fechar = () => {
    setAberto(false);
    setErro(null);
    botao.current?.focus();
  };

  const enviar = async () => {
    const corpo = texto.trim();
    if (!corpo || enviando) return;
    setEnviando(true);
    setErro(null);
    // O aviso de sucesso é do envio ANTERIOR: não pode ficar ao lado do erro deste.
    setFeito(null);
    let fase: Fase = 'ensino';
    try {
      let id = ensino.current ?? await acharEnsinoAberto(runId, origem);
      if (id === null) {
        const novo = await api.startTeaching({ instruction: instrucaoDoEnsino(origem), skill_id: origem.skill_id,
                                               base_version: origem.skill_version });
        id = novo.id;
      }
      ensino.current = id;
      fase = 'correcao';
      const v = await api.addCorrection(id, { body: corpo, run_id: runId, step_id: step.id });
      setFeito({ teachingId: v.id, origem });
      setTexto('');
      fechar();
    } catch (e) {
      const err = toApiError(e);
      // O ensino guardado não serve mais (seguiu adiante ou sumiu): o próximo envio procura ou abre outro.
      if (fase === 'correcao' && (err.code === 'teaching_state' || err.status === 404)) ensino.current = null;
      // O texto NUNCA sai daqui (nem em toast, nem em log): fica só no campo, para a pessoa editar.
      setErro(mensagemDeErro(e, fase, origem));
    } finally {
      setEnviando(false);
    }
  };

  return (
    <div className={styles.corrigir} role="group" aria-label={`Corrigir a etapa ${step.title}`}>
      <div className={styles.linha}>
        <Button ref={botao} size="sm" variant="secondary" icon={PencilLine} aria-expanded={aberto} aria-controls={formId}
                onClick={() => {
                  setAberto((a) => !a);
                  setErro(null);
                }}>
          Corrigir esta etapa
        </Button>
        <span className={styles.rotulo}>
          Veio da habilidade <span className="mono">{origem.skill_id}</span> (versão {origem.skill_version})
        </span>
      </div>

      {aberto ? (
        <form id={formId} className={styles.form}
              onSubmit={(e) => {
                e.preventDefault();
                void enviar();
              }}>
          <Field label="O que devia ter acontecido" unit={`${texto.length}/${CORRECAO_MAX}`} className={styles.texto}
                 hint="A correção entra no ensino da habilidade e vai ao modelo quando ele gerar a versão corrigida. Não escreva senha nem código: o texto com cara de credencial é recusado e a correção não é gravada.">
            {({ id, describedBy }) => (
              <TextArea id={id} aria-describedby={describedBy} rows={3} maxLength={CORRECAO_MAX} value={texto}
                        autoFocus onChange={(e) => setTexto(e.target.value)} />
            )}
          </Field>
          <div className={styles.acoes}>
            <Button type="submit" size="sm" variant="primary" loading={enviando}
                    disabledReason={texto.trim() ? null : 'Escreva a correção.'}>
              Enviar correção
            </Button>
            <Button size="sm" variant="ghost" onClick={fechar}>Cancelar</Button>
          </div>
          {erro ? <p className={styles.erro} role="alert">{erro}</p> : null}
        </form>
      ) : erro ? <p className={styles.erro} role="alert">{erro}</p> : null}

      {feito ? (
        <div className={styles.feito} role="status">
          <span>
            <GraduationCap size={13} aria-hidden className={styles.icone} /> Correção registrada no ensino da
            habilidade <span className="mono">{feito.origem.skill_id}</span> (versão {feito.origem.skill_version}).
          </span>
          <span className={styles.rotulo}>
            O ensino <span className="mono">{feito.teachingId}</span> fica aberto e junta as correções desta execução.
            Gerar a versão corrigida ainda não tem tela no painel. Com o aprendizado ligado, a correção aparece em
            Aprendizado como “Corrigiu no ensino”.
          </span>
          <span>
            <Button size="sm" variant="ghost" onClick={() => useUiStore.getState().setView('aprendizado')}>
              Ver em Aprendizado
            </Button>
          </span>
        </div>
      ) : null}
    </div>
  );
}
