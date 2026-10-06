/**
 * 31.111 F5 (adendo v1.75): "Ensinar a corrigir" na etapa que falhou ou ficou sem prova. A pessoa ensina a tarefa no
 * aparelho da etapa e o treino nasce LIGADO a ela (`POST /api/training/from-run`), com o contexto da execução na
 * revisão. Nada é automático: o botão pede o controle do aparelho (a IA fica em espera nele) só depois da escolha da
 * pessoa, e o treino abre no Foco. É o ÚNICO caminho de ensino na etapa (31.91 T1, ADR-078): o ensino por habilidade (v2)
 * saiu da tela.
 */
import { Wrench } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { PersonaOnDevice, RunDetail, Step, StepStatus } from '../../api/types';
import { Button } from '../../components/Button';
import { Field, Select, TextInput } from '../../components/Field';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { toast } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { personasDoEnsino } from '../training/TrainingBar';
import styles from './EnsinarACorrigir.module.css';

/**
 * As etapas de onde se ensina: a que falhou, a que ficou sem prova e a que parou esperando uma pessoa (o backend aceita
 * as três: `training/origem.py::STATUS_ENSINAVEIS`, 31.111 A).
 */
export const ETAPA_CORRIGIVEL: ReadonlySet<StepStatus> = new Set<StepStatus>(['failed', 'uncertain', 'waiting_user']);

/** O texto de abertura que o backend usaria sozinho (adendo v1.75): só se manda quando a pessoa o reescreve. */
export function intencaoDaCorrecao(titulo: string): string {
  return `Corrigir a etapa «${titulo}»`.slice(0, 400);
}

export function EnsinarACorrigir({ detail, step }: { detail: Pick<RunDetail, 'id'>; step: Step }) {
  const [aberto, setAberto] = useState(false);
  const padrao = intencaoDaCorrecao(step.title);
  const [texto, setTexto] = useState(padrao);
  const [personas, setPersonas] = useState<PersonaOnDevice[] | null>(null);
  const [quem, setQuem] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const formId = useId();
  const botao = useRef<HTMLButtonElement>(null);
  const aparelho = step.instance_id;

  // As personas do aparelho se leem quando o formulário abre: com mais de uma o backend exige a escolha (409 `persona_ambigua`).
  useEffect(() => {
    if (!aberto) return;
    let vivo = true;
    setPersonas(null);
    api.instancePersonas(aparelho)
      .then((lista) => { if (vivo) setPersonas(lista); })
      .catch(() => { if (vivo) setPersonas([]); });
    return () => { vivo = false; };
  }, [aberto, aparelho]);

  if (!ETAPA_CORRIGIVEL.has(step.status)) return null;

  const candidatas = personasDoEnsino(personas ?? [], step.app_id ?? '');
  const precisaEscolher = candidatas.length > 1;
  const escolhaValida = candidatas.some((p) => p.profile_id === quem);
  const lendo = aberto && personas === null;

  const fechar = () => {
    setAberto(false);
    setErro(null);
    botao.current?.focus();
  };

  const abrir = async () => {
    if (enviando || lendo || (precisaEscolher && !escolhaValida)) return;
    setEnviando(true);
    setErro(null);
    try {
      let lease = useControlStore.getState().leases[aparelho];
      const instancia = useAppStore.getState().instances[aparelho];
      if (!lease || lease.status !== 'granted' || instancia?.control !== 'user') {
        await useControlStore.getState().take(aparelho);
        lease = useControlStore.getState().leases[aparelho];
      }
      if (!lease) { setErro(`Sem o controle de ${aparelho} o treino não abre. O aviso acima diz o que impediu.`); return; }
      if (lease.status !== 'granted') {
        setErro(`Pedi o controle de ${aparelho}: a IA termina a ação atual e ele vira seu. Quando estiver com você, clique de novo.`);
        return;
      }
      const intencao = texto.trim();
      await api.startTrainingFromRun({
        run_id: detail.id, step_id: step.id, lease_id: lease.leaseId,
        ...(intencao && intencao !== padrao ? { intent: intencao } : {}),
        ...(precisaEscolher && escolhaValida ? { profile_id: quem } : {}),
      });
      toast({ tone: 'success', title: 'Treino aberto a partir da falha', message: `Ensine a tarefa em ${aparelho}; a gravação segue a etapa «${step.title}».` });
      setAberto(false);
      useUiStore.getState().openFocus(aparelho);
    } catch (e) {
      // A recusa não muda nada (etapa que já não falhou, controle perdido, persona de outro aparelho): a mensagem vem do backend.
      setErro(toApiError(e).message);
    } finally {
      setEnviando(false);
    }
  };

  return (
    <div className={styles.corrigir}>
      <div className={styles.linha}>
        <Button ref={botao} size="sm" variant="outline" icon={Wrench} onClick={() => setAberto(true)} aria-expanded={aberto}
                aria-controls={aberto ? formId : undefined}>
          Ensinar a corrigir
        </Button>
        <span className={styles.rotulo}>Você refaz a tarefa em {aparelho} e o treino fica ligado a esta etapa.</span>
      </div>
      {aberto ? (
        <form id={formId} className={styles.form} onSubmit={(e) => { e.preventDefault(); void abrir(); }}>
          <p className={styles.rotulo}>
            Isto assume o controle de <strong>{aparelho}</strong> (a IA fica em espera nele até você devolver) e abre o treino no Foco.
            Nada roda sozinho.
          </p>
          <Field label="O que você vai ensinar?">
            {({ id }) => <TextInput id={id} value={texto} maxLength={400} onChange={(e) => setTexto(e.target.value)} />}
          </Field>
          {precisaEscolher ? (
            <Field label="De quem é o ensino?">
              {({ id }) => (
                <Select id={id} value={quem} onChange={(e) => setQuem(e.target.value)}>
                  <option value="">Escolha a persona</option>
                  {candidatas.map((p) => <option key={p.profile_id} value={p.profile_id}>{p.display_name || p.name}</option>)}
                </Select>
              )}
            </Field>
          ) : null}
          <div className={styles.linha}>
            <Button size="sm" variant="primary" type="submit" loading={enviando}
                    disabledReason={lendo ? 'Lendo as personas deste aparelho.' : precisaEscolher && !escolhaValida ? 'Escolha de qual persona é o ensino.' : null}>
              Assumir o controle e abrir o treino
            </Button>
            <Button size="sm" variant="ghost" onClick={fechar}>Cancelar</Button>
          </div>
          {erro ? <p className={styles.erro} role="alert">{erro}</p> : null}
        </form>
      ) : null}
    </div>
  );
}
