/**
 * Modo treinamento no Foco (item 13.3). Com o controle na mão, a pessoa diz o que vai ensinar e faz a tarefa; cada
 * toque, texto, deslize, tecla e "abrir app" é gravado com o elemento tocado. Ao concluir, a revisão (IA propõe,
 * pessoa ajusta) transforma a gravação em habilidade para os perfis escolhidos.
 */
import { CircleDot, GraduationCap, Square, Trash2, Undo2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { Instance, PersonaOnDevice, TrainingSession } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Disclosure } from '../../components/Disclosure';
import { Field, Select, TextInput } from '../../components/Field';
import { isRecord, plural } from '../../lib/format';
import { tempoRelativo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { onLiveEvent } from '../../store/live';
import { toast, toastError } from '../../store/toasts';
import { descartarSessaoConcluida } from './descartarSessao';
import { OrigemDoTreino, SeloDeOrigem } from './OrigemDoTreino';
import { RefazerReceitas, TrainingReview, toqueSemAlvo } from './TrainingReview';
import { useTrainingStore } from './trainingStore';
import styles from './Training.module.css';

/** Quantos caracteres do nome da gravação cabem no botão de "Para revisar"; o nome inteiro vai no rótulo. */
const ROTULO_DA_GRAVACAO = 40;

/** Corta o nome na última palavra inteira que cabe, com reticências (29.142: antes o corte caía no meio da palavra). */
/** O estado de uma sessão concluída, em palavras: "sem proposta ainda" diz o que falta (31.119). */
const estadoDaPendente = (s: TrainingSession): string => (s.status === 'proposed' ? 'proposta pronta' : 'sem proposta ainda');
const entradasDe = (s: TrainingSession): number => Math.max(s.input_count ?? 0, s.inputs?.length ?? 0);

/** O texto de "Para revisar" depois do nome: estado, há quanto tempo e quantas entradas ("sem proposta ainda · há 3 h · 0 entradas"). */
function metaDaPendente(s: TrainingSession, agora: number): string {
  const quando = s.updated_at || s.finished_at || s.created_at;
  return [estadoDaPendente(s), quando ? tempoRelativo(quando, agora) : null, plural(entradasDe(s), 'entrada', 'entradas')].filter(Boolean).join(' · ');
}

function encurtar(nome: string): string {
  if (nome.length <= ROTULO_DA_GRAVACAO) return nome;
  const corte = nome.slice(0, ROTULO_DA_GRAVACAO - 1);
  const espaco = corte.lastIndexOf(' ');
  return `${(espaco > ROTULO_DA_GRAVACAO / 2 ? corte.slice(0, espaco) : corte).trimEnd()}…`;
}

/** Quantas sessões salvas a barra lista para refazer receitas: as mais novas (a lista vem do backend da mais nova para a mais velha). */
const SALVAS_NA_BARRA = 5;

/**
 * As personas que podem ser dona do ensino neste app (31.90-C): uma por pessoa, as vinculadas ao app escolhido ou sem app
 * no vínculo; sem app escolhido, todas. É a mesma conta do backend (`persona_ambigua` com mais de uma).
 */
export function personasDoEnsino(personas: readonly PersonaOnDevice[], appId: string): PersonaOnDevice[] {
  const vistas = new Set<string>();
  return personas.filter((p) => {
    if (appId && p.app_id && p.app_id !== appId) return false;
    if (vistas.has(p.profile_id)) return false;
    vistas.add(p.profile_id);
    return true;
  });
}

const DESCRICAO: Record<string, string> = {
  tap: 'toque', long_press: 'toque longo', swipe: 'deslize', text: 'texto', key: 'tecla', open_app: 'abrir app',
};

export function TrainingBar({ instance, leaseId, mine, somenteRevisao = false }: {
  instance: Instance; leaseId: string | null; mine: boolean;
  /** Aparelho fora do ar (31.86): sem formulário de iniciar; a lista "Para revisar" e a revisão seguem de pé. */
  somenteRevisao?: boolean;
}) {
  const apps = useAppStore((s) => s.apps);
  const [sessoes, setSessoes] = useState<TrainingSession[]>([]);
  const [ativa, setAtiva] = useState<TrainingSession | null>(null);
  const [intencao, setIntencao] = useState('');
  const [appId, setAppId] = useState('');
  // 31.90-C: de quem é o ensino. Com mais de uma persona para o app o backend recusa (409 `persona_ambigua`): a pessoa
  // escolhe; com uma só, ela segue sozinha; sem nenhuma, o fluxo nasce sem persona e não vale em aparelho nenhum (30.81).
  const [personas, setPersonas] = useState<PersonaOnDevice[] | null>(null);
  // 'lendo' trava o Iniciar: sem a lista, aparelho com várias personas mandaria o início sem dono e o backend recusaria.
  const [leitura, setLeitura] = useState<'lendo' | 'pronta' | 'falhou'>('lendo');
  const [quemEnsina, setQuemEnsina] = useState('');
  // Uma ação em voo por vez; cada botão gira só pela sua e o outro explica por que espera.
  const [ocupado, setOcupado] = useState<'iniciar' | 'concluir' | 'descartar' | 'desfazer' | null>(null);
  const [revisando, setRevisando] = useState<string | null>(null);
  const recusadas = useTrainingStore((s) => s.recusadas[instance.id] ?? 0);
  const definirGravando = useTrainingStore((s) => s.definirGravando);
  // Só é "gravando" para o Foco (padrão de "Limpar o campo antes", contador de recusas) quando a gravação é desta pessoa.
  const gravandoMesmo = !!ativa && mine;
  useEffect(() => {
    definirGravando(instance.id, gravandoMesmo);
  }, [instance.id, gravandoMesmo, definirGravando]);
  useEffect(() => () => definirGravando(instance.id, false), [instance.id, definirGravando]);

  const carregar = useCallback(async () => {
    try {
      const lista = await api.listTraining(instance.id);
      setSessoes(lista);
      const gravando = lista.find((s) => s.status === 'recording');
      setAtiva(gravando ? await api.getTraining(gravando.id) : null);
    } catch {
      /* sem gravações: a barra fica no estado inicial */
    }
  }, [instance.id]);

  useEffect(() => {
    void carregar();
  }, [carregar, mine]);

  // As personas vinculadas a este aparelho: só se leem quando o formulário de iniciar está à vista.
  const formularioAberto = mine && !ativa && !somenteRevisao;
  useEffect(() => {
    if (!formularioAberto) return;
    let vivo = true;
    // Lista de outro aparelho não vale para este: some enquanto a nova não chega.
    setPersonas(null);
    setLeitura('lendo');
    // Falhar a leitura não trava o início: sem a lista o formulário segue como era e o backend decide.
    api.instancePersonas(instance.id)
      .then((lista) => { if (vivo) { setPersonas(lista); setLeitura('pronta'); } })
      .catch(() => { if (vivo) { setPersonas(null); setLeitura('falhou'); } });
    return () => { vivo = false; };
  }, [formularioAberto, instance.id]);

  const candidatas = personasDoEnsino(personas ?? [], appId);
  const precisaEscolher = candidatas.length > 1;
  const escolhaValida = candidatas.some((p) => p.profile_id === quemEnsina);
  // A escolha que deixou de valer (outro app, outro aparelho) não vai no corpo: o backend a recusaria com 409.
  useEffect(() => {
    if (quemEnsina && !escolhaValida) setQuemEnsina('');
  }, [quemEnsina, escolhaValida]);

  // Cada entrada gravada chega como evento: a lista ao vivo acompanha sem consultar a cada segundo.
  useEffect(() => onLiveEvent((ev) => {
    if (ev.instance_id !== instance.id) return;
    if (ev.kind === 'training.input' || (isRecord(ev.data) && 'training_session_id' in ev.data)) void carregar();
  }), [instance.id, carregar]);

  async function iniciar() {
    if (!leaseId || !intencao.trim() || ocupado || (precisaEscolher && !escolhaValida) || (formularioAberto && leitura === 'lendo')) return;
    setOcupado('iniciar');
    try {
      setAtiva(await api.startTraining(instance.id, {
        intent: intencao.trim(), lease_id: leaseId, app_id: appId || null, ...(precisaEscolher && escolhaValida ? { profile_id: quemEnsina } : {}),
      }));
      toast({ tone: 'info', title: 'Gravando o treinamento', message: 'Faça a tarefa na tela. Cada toque lê a tela antes, então fica um pouco mais lento.' });
    } catch (e) {
      toastError('Não foi possível iniciar o treinamento', e);
    } finally {
      setOcupado(null);
    }
  }

  async function concluir(descartar = false) {
    if (!ativa || ocupado) return;
    if (descartar) {
      const entradas = (ativa.inputs ?? []).length;
      const { confirmed } = await confirm({
        title: 'Descartar a gravação?',
        danger: true,
        confirmLabel: 'Descartar gravação',
        cancelLabel: 'Cancelar',
        body: `“${ativa.intent}” e ${plural(entradas, 'entrada gravada', 'entradas gravadas')} se perdem; nada vira fluxo nem habilidade. O controle continua com você.`,
      });
      if (!confirmed) return;
    }
    setOcupado(descartar ? 'descartar' : 'concluir');
    try {
      const s = descartar ? await api.discardTraining(ativa.id, leaseId) : await api.stopTraining(ativa.id, leaseId);
      setAtiva(null);
      setIntencao('');
      await carregar();
      if (!descartar) setRevisando(s.id);
    } catch (e) {
      // 31.92 (v1.64): parar ou descartar a gravação viva exige o controle atual do aparelho. A recusa não muda nada:
      // a gravação segue, e a barra se relê para mostrar quem está com ela agora.
      if (toApiError(e).code === 'control_required') {
        toastError('A gravação continua', e);
        await carregar();
      } else toastError('Não foi possível encerrar o treinamento', e);
    } finally {
      setOcupado(null);
    }
  }

  // 31.119: descartar da lista "Para revisar" (a sessão já concluída), com confirmação; devolve o controle se ele é desta aba.
  async function descartarPendente(s: TrainingSession) {
    if (ocupado) return;
    if (await descartarSessaoConcluida(s)) await carregar();
  }

  // 31.90-D (v1.70): desfaz só a última entrada da gravação viva; o aparelho não volta, só a gravação. Leva o `seq` que a
  // tela mostra como último: se outra entrada chegou antes do pedido, o backend recusa (409 `entrada_mudou`) e nada se apaga.
  async function desfazerAUltima() {
    const ultima = (ativa?.inputs ?? []).at(-1);
    if (!ativa || !leaseId || !ultima || ocupado) return;
    setOcupado('desfazer');
    try {
      // A resposta já é a sessão sem a entrada: o número desfeito é reaproveitado pela próxima, então nada se guarda por seq.
      setAtiva(await api.undoTraining(ativa.id, leaseId, ultima.seq));
      toast({ tone: 'info', title: 'Entrada desfeita', message: `A entrada #${ultima.seq} saiu da gravação; o aparelho não voltou, só a gravação.` });
    } catch (e) {
      // A recusa não muda nada: a barra se relê para mostrar a última entrada de verdade (e quem está com o controle).
      toastError('A gravação continua como estava', e);
      await carregar();
    } finally {
      setOcupado(null);
    }
  }

  const botoesDaGravacao = () => (
    <div className={styles.actions}>
      <Button size="sm" variant="primary" icon={Square} loading={ocupado === 'concluir'}
              disabledReason={!(ativa?.inputs ?? []).length ? 'Nada gravado: faça a tarefa no aparelho; sem entradas só dá para descartar.'
                : ocupado === 'descartar' ? 'Descartando a gravação…' : ocupado === 'desfazer' ? 'Desfazendo a última entrada…' : null}
              onClick={() => void concluir(false)}>Concluir e revisar</Button>
      <Button size="sm" variant="dangerGhost" icon={Trash2} loading={ocupado === 'descartar'}
              disabledReason={ocupado === 'concluir' ? 'Concluindo a gravação…' : ocupado === 'desfazer' ? 'Desfazendo a última entrada…' : null}
              onClick={() => void concluir(true)}>Descartar</Button>
    </div>
  );

  const agora = useNow();
  const pendentes = sessoes.filter((s) => s.status === 'recorded' || s.status === 'proposed');
  // v1.58: a sessão salva some de "Para revisar", mas a etapa que ficou sem receita (aparelho fora do ar no salvar)
  // ainda pode ganhá-la; daqui a pessoa refaz quando o aparelho voltar, sem reabrir a revisão.
  const todasSalvas = sessoes.filter((s) => s.status === 'saved');
  const salvas = todasSalvas.slice(0, SALVAS_NA_BARRA);

  return (
    <section className={styles.bar} aria-label="Modo treinamento">
      <h3 className={styles.title}><GraduationCap size={15} aria-hidden /> Modo treinamento</h3>
      {ativa && !mine && instance.control === 'user' ? (
        // Gravação viva de quem tem o controle em outra aba (ou depois de um F5: o lease vive só em memória) ou de
        // outra pessoa: quem só olha não encerra nem descarta a gravação alheia.
        <p className={styles.hint} role="status">Há uma gravação em andamento neste aparelho por quem está com o controle. Se a gravação é sua
          (em outra aba ou antes de recarregar a página), clique em Retomar controle para continuar por aqui.</p>
      ) : ativa && !mine ? (
        // 31.80: sessão `recording` sem NINGUÉM com o controle é gravação órfã (ex.: o servidor reiniciou); nada mais
        // é gravado, então a barra não diz "Gravando".
        <div className={styles.recording}>
          <p className={styles.hint} role="status">Há uma gravação aberta neste aparelho que não está mais gravando: <strong>{ativa.intent}</strong>.</p>
          {botoesDaGravacao()}
        </div>
      ) : ativa ? (
        <div className={styles.recording}>
          <p className={styles.recLine}>
            <CircleDot size={14} className={styles.recDot} aria-hidden /> Gravando: <strong>{ativa.intent}</strong>
            <Badge size="sm">{plural((ativa.inputs ?? []).length, 'entrada', 'entradas')}</Badge>
          </p>
          {!(ativa.inputs ?? []).length ? <p className={styles.hint} role="status">Nada gravado ainda: faça a tarefa no aparelho. Sem entradas só dá para descartar.</p> : null}
          {/* 31.111 F5: o treino aberto a partir de uma etapa que falhou mostra de onde veio e o que a execução fez. */}
          {ativa.origin ? <OrigemDoTreino origin={ativa.origin} /> : null}
          <ol className={styles.liveList}>
            {(ativa.inputs ?? []).slice(-6).map((e) => (
              <li key={e.seq}>
                <span className={styles.muted}>#{e.seq}</span> {DESCRICAO[e.type] ?? e.type}
                {e.target?.text || e.target?.desc ? <> em <strong>{e.target.text || e.target.desc}</strong></> : null}
                {toqueSemAlvo(e) ? <span className={styles.muted}> {toqueSemAlvo(e)}</span> : null}
                {e.type === 'text' ? (e.text !== null ? <> “{e.text}”</> : <> ({e.text_len} caractere(s) sigilosos — não gravados)</>) : null}
                {e.type === 'open_app' ? <> {e.app_id}</> : null}
                {e.type === 'key' ? <> {e.key_name}</> : null}
              </li>
            ))}
          </ol>
          {/* A região viva nasce vazia com a gravação: o texto que entra depois é anunciado, o que já nasce com ele não. */}
          <p className={styles.hint} role="status" aria-live="polite">{recusadas ? `${plural(recusadas, 'entrada recusada', 'entradas recusadas')}: refaça` : ''}</p>
          <p className={styles.hint}>Para trocar um texto, marque Limpar o campo antes em vez de apertar Apagar.</p>
          {/* Só a gravação de quem tem o controle (este ramo): quem só olha, ou a gravação órfã, não desfaz a de ninguém. */}
          <Button size="sm" variant="ghost" icon={Undo2} loading={ocupado === 'desfazer'}
                  disabledReason={!leaseId ? 'Retome o controle para desfazer.' : !(ativa.inputs ?? []).length ? 'Ainda não há entrada para desfazer.'
                    : ocupado && ocupado !== 'desfazer' ? 'Espere a ação em andamento.' : null}
                  onClick={() => void desfazerAUltima()}>Desfazer a última</Button>
          {botoesDaGravacao()}
        </div>
      ) : somenteRevisao ? (
        <p className={styles.hint}>Aparelho fora do ar: dá para revisar e salvar o fluxo; as receitas das etapas só são gravadas com o aparelho online.</p>
      ) : mine ? (
        <div className={styles.start}>
          <Field label="O que você vai ensinar?">
            {({ id }) => (
              <TextInput id={id} value={intencao} maxLength={400} placeholder="Ex.: responder a DM de um cliente com uma saudação"
                         onChange={(e) => setIntencao(e.target.value)} />
            )}
          </Field>
          <Field label="App" unit="opcional">
            {({ id }) => (
              <Select id={id} value={appId} onChange={(e) => setAppId(e.target.value)}>
                <option value="">Descobrir pela tela</option>
                {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            )}
          </Field>
          {personas === null ? null : precisaEscolher ? (
            <Field label="De quem é o ensino?">
              {({ id }) => (
                <Select id={id} value={quemEnsina} onChange={(e) => setQuemEnsina(e.target.value)}>
                  <option value="">Escolha a persona</option>
                  {candidatas.map((p) => <option key={p.profile_id} value={p.profile_id}>{p.display_name || p.name}</option>)}
                </Select>
              )}
            </Field>
          ) : candidatas.length === 0 ? (
            <p className={styles.hint}>Nenhuma persona está vinculada a este aparelho para este app: o fluxo gravado fica sem persona e não vale em aparelho nenhum até uma prova real ou a sua confirmação.</p>
          ) : (
            <p className={styles.hint}>O ensino fica com {candidatas[0]?.display_name || candidatas[0]?.name}, a persona deste aparelho.</p>
          )}
          <Button size="sm" variant="primary" icon={CircleDot} loading={ocupado === 'iniciar'}
                  disabledReason={!intencao.trim() ? 'Diga o que vai ensinar.' : leitura === 'lendo' ? 'Lendo as personas deste aparelho.' : precisaEscolher && !escolhaValida ? 'Escolha de qual persona é o ensino.' : null}
                  onClick={() => void iniciar()}>
            Iniciar treinamento
          </Button>
          <p className={styles.hint}>Senhas e códigos digitados não são gravados. Devolver o controle encerra a gravação.</p>
          <p className={styles.hint}>Para trocar um texto, marque Limpar o campo antes em vez de apertar Apagar.</p>
        </div>
      ) : (
        <p className={styles.hint}>Assuma o controle para ensinar uma tarefa: você faz, a IA mapeia o processo e ele vira habilidade para os perfis que você escolher.</p>
      )}
      {pendentes.length ? (
        <div className={styles.pending}>
          <span className={styles.muted}>Para revisar:</span>
          <ul className={styles.pendentes}>
          {pendentes.map((s) => (
            <li key={s.id} className={styles.pendente}>
              {/* 29.142: o rótulo leva o nome inteiro; o estado, o tempo e as entradas ficam ao lado (uma linha por sessão, 31.119). */}
              <Button size="sm" variant="ghost" onClick={() => setRevisando(s.id)}
                      label={`Revisar “${s.intent}”, ${estadoDaPendente(s)}`}>
                {encurtar(s.intent)}
              </Button>
              <span className={styles.muted}>{metaDaPendente(s, agora)}</span>
              {s.origin ? <SeloDeOrigem origin={s.origin} /> : null}
              <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label={`Descartar “${s.intent}”`}
                      disabledReason={ocupado ? 'Espere a ação em andamento.' : null} onClick={() => void descartarPendente(s)} />
            </li>
          ))}
          </ul>
        </div>
      ) : null}
      {salvas.length ? (
        <Disclosure summary={todasSalvas.length > salvas.length ? `Salvas (as ${salvas.length} mais novas de ${todasSalvas.length})` : `Salvas (${salvas.length})`} bare>
          <ul className={styles.salvas}>
            {salvas.map((s) => (
              <li key={s.id}>
                <span>{s.intent}</span>
                <RefazerReceitas sessionId={s.id} intent={s.intent} />
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      {revisando ? (
        <TrainingReview sessionId={revisando} onClose={() => { setRevisando(null); void carregar(); }} />
      ) : null}
    </section>
  );
}
