/**
 * Revisão do treinamento (item 13.3) — é isto que faz o fluxo ser "inteligência assistida" e não macro:
 * a gravação aparece do lado esquerdo; a IA propõe, do lado direito, o COMANDO com parâmetros, as ETAPAS com
 * objetivo e verificação, o que foi DESCARTADO (erro, vai-e-volta) e as DÚVIDAS; a pessoa ajusta e escolhe quem
 * recebe. Salvar cria o fluxo + as receitas; o relatório diz, etapa a etapa, o que já roda sem IA.
 * 31.91 (caminho único de ensino, decisão do dono de 05/10): o fluxo salvo é o ponto de partida; com `features.skills`,
 * a habilidade versionada nasce dele ("Gerar habilidade deste fluxo", a conversão da fase J). O ensino v2 que a gravação
 * já tinha saiu da tela (31.91 T1, ADR-078: o caminho único de ensino é o Modo treinamento).
 */
import { GraduationCap, RefreshCw, ServerCrash, Sparkles, Trash2, WandSparkles } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type {
  Capability, FlowConversion, InstagramProfile, PolicyGroup, PosCondicaoQueJaVale, TrainingAnswer, TrainingInput, TrainingPreview, TrainingProposal,
  TrainingRecipesResult, TrainingSaveResult, TrainingSession, TrainingStep,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { SeloEmProva } from '../../components/SeloEmProva';
import { explicacaoEmProva, quemUsaEmProva } from '../../lib/emProva';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextInput } from '../../components/Field';
import { PacotesAceitos } from '../../components/PacotesAceitos';
import { AvisoDaPosCondicao, lerPosCondicoes, motivoDaPosCondicao } from './PosCondicaoQueJaVale';
import { EditorDaPosCondicao, EditorDosParametros } from './EdicaoDaProposta';
import { descartarSessaoConcluida } from './descartarSessao';
import { temMarcadorDaPersona, textoComMarcadores } from '../../lib/marcadores';
import { FluxoNoLivro } from './FluxoNoLivro';
import { OrigemDoTreino, SeloDeOrigem } from './OrigemDoTreino';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { useAppStore } from '../../store/app';
import { plural } from '../../lib/format';
import { toast, toastError } from '../../store/toasts';
import styles from './Training.module.css';
import { ATE_A_PROVA, corpoDoEscopo, SeletorValePara, textoDoEscopo, type ModoDoEscopo } from './ValePara';

const TIPO: Record<string, string> = { tap: 'toque', long_press: 'toque longo', swipe: 'deslize', text: 'texto', key: 'tecla', open_app: 'abrir app' };

/** O motivo que vai no `discarded[].why` quando é a pessoa, e não a IA, que tira a entrada da etapa. */
export const MOTIVO_DE_QUEM_ENSINOU = 'descartada por quem ensinou';

// 31.90-A: cada entrada tem um lugar só, numa etapa OU no descarte. O save recusa entrada sem destino
// (`entradas_sem_etapa`) e entrada em dois lugares (`entrada_duplicada`); por isso as duas ações tiram a entrada de
// todo outro lugar antes de pô-la no novo.
function descartarEntrada(p: TrainingProposal, seq: number): TrainingProposal {
  return {
    ...p,
    steps: p.steps.map((s) => (s.inputs.includes(seq) ? { ...s, inputs: s.inputs.filter((n) => n !== seq) } : s)),
    discarded: p.discarded.some((d) => d.seq === seq)
      ? p.discarded
      : [...p.discarded, { seq, why: MOTIVO_DE_QUEM_ENSINOU }].sort((a, b) => a.seq - b.seq),
  };
}

function devolverEntrada(p: TrainingProposal, seq: number, etapa: number): TrainingProposal {
  return {
    ...p,
    steps: p.steps.map((s, k) => {
      const sem = s.inputs.filter((n) => n !== seq);
      // A ordem das entradas na etapa é a ordem em que foram feitas: a devolvida entra no lugar dela.
      if (k === etapa) return { ...s, inputs: [...sem, seq].sort((a, b) => a - b) };
      return sem.length === s.inputs.length ? s : { ...s, inputs: sem };
    }),
    discarded: p.discarded.filter((d) => d.seq !== seq),
  };
}

const listaDeSeqs = (seqs: number[]) => seqs.map((n) => `#${n}`).join(', ');

/** Limites do adendo v1.63 que a tela aplica antes de mandar: resposta de até 500 caracteres, até 8 por pedido. */
const MAX_RESPOSTA = 500;
const MAX_RESPOSTAS_POR_PEDIDO = 8;

/** Espera depois da última edição antes de pedir a prévia (v1.58); exportada para o teste esperar sem número mágico. */
export const ESPERA_DA_PREVIA_MS = 500;

/** Recusas que falam do comando: vão no campo "Comando" (aria-invalid + a mensagem do backend), no salvar e na prévia. */
const CODIGOS_DO_COMANDO = new Set([
  'comando_generico', 'parametro_invalido', 'parametro_fora_do_comando', 'parametro_nao_declarado', 'parametro_reservado',
  'duplicate_command', 'invalid_command', 'ambiguous_command',
]);

/** A recusa do salvar ou da prévia que trava o "Salvar" até a próxima edição: as 400 da conferência e as 409 de antes de gravar. */
interface Recusa {
  code: string;
  message: string;
}

function recusaDe(e: unknown): Recusa | null {
  const err = toApiError(e);
  return err.status === 400 || (err.status === 409 && (err.code === 'duplicate_command' || err.code === 'closed'))
    ? { code: err.code, message: err.message } : null;
}

/** Toque sem alvo, nas mesmas palavras de `linha_da_entrada` (backend, #440): é o que a IA leu ao propor. */
export function toqueSemAlvo(e: TrainingInput): string | null {
  if ((e.type !== 'tap' && e.type !== 'long_press') || (e.target && Object.keys(e.target).length)) return null;
  return e.sensitive && e.x === null ? 'em teclado ou tela sensível (não gravado)' : 'sem elemento identificado';
}

/** Como o seletor `unique` da gravação se lê (`recorder._CAMPOS_DO_SELETOR`): o que a receita compara no aparelho. */
const SELETOR: Record<string, string> = { 'rid+text': 'identificador e texto', 'rid+desc': 'identificador e descrição', rid: 'identificador', desc: 'descrição', text: 'texto' };

/**
 * 31.90-F: como a gravação RECONHECE o elemento tocado, para a pessoa conferir antes de salvar (a receita só vale se o
 * elemento se acha de novo). Só toque com alvo; o toque sem alvo já tem a sua frase em `toqueSemAlvo`. O id aparece sem o
 * pacote (`x:id/nome` vira `nome`): é o rótulo técnico do elemento, não dado da pessoa.
 */
export function alvoReconhecido(e: TrainingInput): string | null {
  const t = e.target;
  if ((e.type !== 'tap' && e.type !== 'long_press') || !t || !Object.keys(t).length) return null;
  const como = (t.unique ?? []).map((u) => SELETOR[u] ?? u);
  const id = t.resource_id ? t.resource_id.split('/').pop() : '';
  if (!como.length) {
    if (!t.filhos?.length) return 'sem identificador único: este toque não vira receita';
    // Contêiner sem identidade: a receita acha o elemento por um filho rotulado (até três gravados); a pessoa vê quais.
    const filhos = t.filhos.slice(0, 3).map((f) => {
      const rotulo = f.text || f.desc;
      const fid = f.resource_id ? f.resource_id.split('/').pop() : '';
      return [rotulo ? `“${rotulo}”` : '', fid ? `identificador ${fid}` : ''].filter(Boolean).join(', ');
    }).filter(Boolean);
    return `reconhecido pelo que o elemento contém${filhos.length ? `: ${filhos.join('; ')}` : ''}`;
  }
  return `reconhecido por ${como[0]}${como.length > 1 ? ` (ou ${como.slice(1).join(', ')})` : ''}${id ? `: ${id}` : ''}`;
}

/** O que a etapa confere, em palavras da pessoa; o tipo cru só aparece se o backend mandar um que a tela não conhece. */
export function textoDoConfere(pc: TrainingStep['postcondition']): string {
  const valor = pc.value ? `“${textoComMarcadores(pc.value)}”` : '';
  const frase = pc.kind === 'text_visible' ? `aparece o texto ${valor || 'esperado'}`
    : pc.kind === 'element_present' ? `existe o elemento ${valor || 'esperado'}`
      : pc.kind === 'app_foreground' ? `o app ${valor || 'certo'} está na frente`
        : pc.kind === 'model_judged' ? 'a IA julga pela tela'
          : `${pc.kind} ${valor}`.trim();
  return pc.description ? `${frase} (${textoComMarcadores(pc.description)})` : frase;
}

/**
 * Teclas iguais e seguidas viram uma linha só na coluna da gravação (apagar um campo grava uma "tecla delete" por
 * letra). Só na exibição: nas etapas cada entrada segue com o seu Descartar.
 */
export function agruparTeclas(entradas: TrainingInput[], descartadas: Set<number>): TrainingInput[][] {
  const grupos: TrainingInput[][] = [];
  for (const e of entradas) {
    const ultimo = grupos[grupos.length - 1];
    const a = ultimo?.[ultimo.length - 1];
    if (ultimo && a && e.type === 'key' && a.type === 'key' && a.key_name === e.key_name && a.seq + 1 === e.seq
        && descartadas.has(a.seq) === descartadas.has(e.seq)) ultimo.push(e);
    else grupos.push([e]);
  }
  return grupos;
}

/** O que a entrada foi. Texto não gravado (tela sensível, senha, cara de segredo) nunca tem valor na tela. */
export function DescricaoEntrada({ e }: { e: TrainingInput }) {
  const semAlvo = toqueSemAlvo(e);
  const alvo = alvoReconhecido(e);
  return (
    <>
      {TIPO[e.type] ?? e.type}
      {e.target?.text || e.target?.desc ? <> em <strong>{e.target.text || e.target.desc}</strong></> : null}
      {semAlvo ? <span className={styles.muted}> {semAlvo}</span> : null}
      {alvo ? <span className={styles.muted}> ({alvo})</span> : null}
      {e.type === 'text' ? (e.text !== null && !e.sensitive
        ? <> “{textoComMarcadores(e.text)}”{temMarcadorDaPersona(e.text) ? <span className={styles.muted}> (preenchido na hora com o dado da persona)</span> : null}</>
        : <span className={styles.muted}> (texto não gravado)</span>) : null}
      {e.type === 'open_app' ? <> {e.app_id}</> : null}
      {e.type === 'key' ? <> {e.key_name}</> : null}
    </>
  );
}

interface Falha {
  message: string;
  hint: string;
}

export function falhaDe(e: unknown): Falha {
  const err = toApiError(e);
  return { message: err.message, hint: hintForError(err) };
}

/**
 * Perfis e grupos do escopo. "Nada marcado = todos os perfis" só é verdade quando a lista carregou: com a carga
 * falha, a lista vazia mentiria, e a pessoa salvaria para todos sem saber. Por isso o erro fica guardado aqui e
 * bloqueia o "Salvar".
 */
interface Escopo {
  perfis: InstagramProfile[];
  grupos: PolicyGroup[];
  carregando: boolean;
  erro: Falha | null;
}

/**
 * "Refazer receitas" (v1.58): a etapa que ficou sem receita porque o aparelho estava fora do ar ganha a receita quando
 * ele volta. Quem decide a hora é a pessoa; a chamada só grava em etapa sem receita e é idempotente (`created: 0`).
 */
const DICA_DE_REFAZER_RECEITAS = 'Grava a receita das etapas que ficaram sem ela porque o aparelho estava fora do ar. Se nenhuma ficou sem receita, nada muda.';

export function RefazerReceitas({ sessionId, intent, dica = false, onFeito }: {
  sessionId: string;
  /** Na lista de sessões salvas, o nome no rótulo distingue um botão do outro. */
  intent?: string;
  /** 31.133: na lista o aviso fixo ("Etapa sem receita porque…") vira dica do botão, porque a lista não sabe se a sessão ficou com etapa sem receita. */
  dica?: boolean;
  onFeito?: (r: TrainingRecipesResult) => void;
}) {
  const [refazendo, setRefazendo] = useState(false);
  const [feito, setFeito] = useState<TrainingRecipesResult | null>(null);

  async function refazer() {
    setRefazendo(true);
    try {
      const r = await api.redoTrainingRecipes(sessionId);
      setFeito(r);
      onFeito?.(r);
    } catch (e) {
      toastError('Não foi possível refazer as receitas', e);
    } finally {
      setRefazendo(false);
    }
  }

  const semReceita = feito?.steps.filter((x) => !x.recipe) ?? [];
  return (
    <span className={styles.actions}>
      <Button size="sm" variant="outline" icon={RefreshCw} loading={refazendo}
              label={intent ? `Refazer receitas de “${intent}”` : undefined} title={dica ? DICA_DE_REFAZER_RECEITAS : undefined}
              onClick={() => void refazer()}>Refazer receitas</Button>
      <span className={styles.muted} role="status">
        {!feito ? (dica ? '' : 'Etapa sem receita porque o aparelho estava fora do ar? Com ele de volta, refaça.')
          : `${feito.created ? `${plural(feito.created, 'receita gravada', 'receitas gravadas')} agora.` : 'Nenhuma receita nova.'}`
            + (semReceita.length ? ` Sem receita: ${semReceita.map((x) => `${x.title} (${x.reason})`).join('; ')}.` : '')
            // 30.81: a receita da gravação espera junto do fluxo; só a persona que ensinou a usa até a prova.
            + (feito.ensinado_em_prova ? ` ${quemUsaEmProva(feito.ensinado_em_prova).receitas}` : '')}
      </span>
    </span>
  );
}

export function TrainingReview({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [sessao, setSessao] = useState<TrainingSession | null>(null);
  const [falhaSessao, setFalhaSessao] = useState<Falha | null>(null);
  const [tentativa, setTentativa] = useState(0);
  const [proposta, setProposta] = useState<TrainingProposal | null>(null);
  const [escopo, setEscopo] = useState<Escopo>({ perfis: [], grupos: [], carregando: true, erro: null });
  const [acoes, setAcoes] = useState<Capability[]>([]);
  const [escolhidosP, setEscolhidosP] = useState<Set<string>>(new Set());
  const [escolhidosG, setEscolhidosG] = useState<Set<string>>(new Set());
  // Edições da pessoa: na proposta (comando, etapas, destino das entradas, ações do catálogo) e no escopo. Outra proposta substitui só a
  // primeira; fechar perde as duas. Descartá-las sem perguntar é o que P2.6 proíbe. A da proposta compara com a que
  // chegou: descartar e devolver a mesma entrada volta ao que era, e aí não há o que perder.
  const [original, setOriginal] = useState<TrainingProposal | null>(null);
  const editado = proposta !== null && JSON.stringify(proposta) !== JSON.stringify(original);
  const [escopoMudou, setEscopoMudou] = useState(false);
  // 31.88 F2: "Vale para". O padrão é o do contrato: todos, depois de provado.
  const [vale, setVale] = useState<ModoDoEscopo>('todos');
  // R1/R2 (#436): a etapa escolhida para devolver cada entrada, o que a última ação fez (anunciado) e para onde o foco
  // vai depois dela; o controle de onde a entrada estava some no re-render.
  const [destino, setDestino] = useState<Record<number, string>>({});
  const [aviso, setAviso] = useState('');
  const alvos = useRef(new Map<string, HTMLElement>());
  const [focar, setFocar] = useState<string | null>(null);
  const alvo = (chave: string) => (el: HTMLElement | null) => {
    if (el) alvos.current.set(chave, el);
    else alvos.current.delete(chave);
  };
  const [pensando, setPensando] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [descartando, setDescartando] = useState(false);
  // v1.63: as respostas da pessoa às perguntas da proposta, por pergunta, e a recusa de resposta com cara de segredo.
  const [respostas, setRespostas] = useState<Record<string, string>>({});
  const [erroResposta, setErroResposta] = useState<{ message: string; question: string | null } | null>(null);
  // O que está respondido nas perguntas da proposta atual. Acima do limite do contrato (8 por chamada) o botão trava
  // com o motivo, em vez de cortar: o sucesso limpa os campos, e a 9ª resposta sumiria sem ir.
  const respondidas: TrainingAnswer[] = (proposta?.questions ?? [])
    .map((q) => ({ question: q, answer: (respostas[q] ?? '').trim() }))
    .filter((a) => a.answer);
  // "Gerar habilidade deste fluxo" (a conversão da fase J) depois de salvar, com `features.skills`.
  const [conversao, setConversao] = useState<FlowConversion | null>(null);
  const [convertendo, setConvertendo] = useState(false);
  const [resultado, setResultado] = useState<TrainingSaveResult | null>(null);
  // Prévia do salvar (v1.58): o que cada etapa vira e a recusa, antes de clicar. `leitura` descarta a resposta de uma
  // prévia que outra edição já tornou velha (a primeira pode esperar a leitura do aparelho e chegar depois da segunda).
  const [previa, setPrevia] = useState<TrainingPreview | null>(null);
  const [recusa, setRecusa] = useState<Recusa | null>(null);
  // 31.128 (v1.86): as etapas cuja conferência já vale na tela de partida, da prévia ou da recusa do salvar.
  const [jaValem, setJaValem] = useState<PosCondicaoQueJaVale[]>([]);
  const [previaFora, setPreviaFora] = useState<string | null>(null);
  const leitura = useRef(0);
  // O relatório do salvar oferece "Refazer receitas" se alguma etapa saiu sem receita; fica depois de refazer, com o resultado.
  const [semReceitaAoSalvar, setSemReceitaAoSalvar] = useState(false);

  useEffect(() => {
    let vivo = true;
    setFalhaSessao(null);
    void api.getTraining(sessionId).then((s) => {
      if (!vivo) return;
      setSessao(s);
      setProposta(s.proposal);
      setOriginal(s.proposal);
      if (s.profile_id) setEscolhidosP(new Set([s.profile_id]));
    }).catch((e) => { if (vivo) setFalhaSessao(falhaDe(e)); });
    return () => { vivo = false; };
  }, [sessionId, tentativa]);

  const carregarEscopo = useCallback(async () => {
    setEscopo((x) => ({ ...x, carregando: true, erro: null }));
    const [p, g] = await Promise.allSettled([api.listProfiles(), api.listPolicyGroups()]);
    const falha = [p, g].find((r): r is PromiseRejectedResult => r.status === 'rejected');
    setEscopo({
      perfis: p.status === 'fulfilled' ? p.value : [],
      grupos: g.status === 'fulfilled' ? g.value : [],
      carregando: false,
      erro: falha ? falhaDe(falha.reason) : null,
    });
  }, []);

  useEffect(() => {
    void carregarEscopo();
  }, [carregarEscopo]);

  // Com `features.skills` ligado, o relatório do salvar oferece gerar a habilidade do fluxo. Desligado (ou backend sem o
  // campo), isso não aparece.
  const habilidades = useAppStore((st) => st.health?.features?.skills === true);

  // Catálogo do app (se houver): etapa com efeito num app com catálogo precisa dizer QUAL ação ela é.
  const appsDoStore = useAppStore((st) => st.apps);
  const appDaProposta = proposta?.app_id ?? sessao?.app_id ?? null;
  const pacote = appsDoStore.find((a) => a.id === appDaProposta)?.package ?? null;
  useEffect(() => {
    if (!pacote) return;
    void api.listAppCatalog().then(async (cat) => {
      const app = cat.find((a) => a.package === pacote);
      setAcoes(app && app.has_catalog ? await api.listCapabilities(pacote) : []);
    }).catch(() => setAcoes([]));
  }, [pacote]);

  const porSeq = useMemo(() => new Map((sessao?.inputs ?? []).map((e) => [e.seq, e])), [sessao]);
  const descartadas = new Set((proposta?.discarded ?? []).map((d) => d.seq));
  // Quantos lugares cada entrada ocupa na proposta: zero é "sem destino", mais de um é duplicada.
  // O descarte conta uma vez só: repetida dentro de `discarded` o save aceita e fica a primeira (#442).
  const lugares = useMemo(() => {
    const m = new Map<number, number>();
    for (const s of proposta?.steps ?? []) for (const n of s.inputs) m.set(n, (m.get(n) ?? 0) + 1);
    for (const seq of new Set((proposta?.discarded ?? []).map((d) => d.seq))) m.set(seq, (m.get(seq) ?? 0) + 1);
    return m;
  }, [proposta]);
  const descarteUnico = (proposta?.discarded ?? []).filter((d, k, todas) => todas.findIndex((x) => x.seq === d.seq) === k);
  const semDestino = proposta ? (sessao?.inputs ?? []).filter((e) => !lugares.has(e.seq)) : [];
  const duplicadas = [...lugares].filter(([, n]) => n > 1).map(([seq]) => seq).sort((a, b) => a - b);

  useEffect(() => {
    if (!focar) return;
    alvos.current.get(focar)?.focus();
    setFocar(null);
  }, [focar, proposta]);

  // A prévia só roda quando a tela não tem o que dizer antes (sem destino, duplicada, escopo): essas o save recusaria
  // com a mesma mensagem, e a tela já as explica. Sessão que a prévia achou salva (409 `closed`) não se pergunta mais.
  const bloqueioLocal = motivoLocal();
  const [fechada, setFechada] = useState(false);
  const corpoDoSalvar = useMemo(
    () => (proposta ? { proposal: proposta, ...corpoDoEscopo(vale, escolhidosP, escolhidosG) } : null),
    [proposta, escolhidosP, escolhidosG, vale],
  );
  useEffect(() => {
    if (fechada) return;
    const minha = ++leitura.current;
    setRecusa(null);
    setJaValem([]);
    setPreviaFora(null);
    if (!corpoDoSalvar || bloqueioLocal || resultado) {
      setPrevia(null);
      return;
    }
    const espera = setTimeout(() => {
      api.previewTraining(sessionId, corpoDoSalvar).then((r) => {
        if (minha !== leitura.current) return;
        setPrevia(r);
        setJaValem(lerPosCondicoes(r.pos_condicoes_ja_valem));
        // 31.142 (v1.91): o comando repetido volta como 200 com `code`/`message`; trava o Salvar como a recusa 409 do salvar.
        if (r.code) setRecusa({ code: r.code, message: r.message || 'O comando já existe.' });
      }).catch((e) => {
        if (minha !== leitura.current) return;
        setPrevia(null);
        const r = recusaDe(e);
        if (r?.code === 'closed') setFechada(true);
        if (r) setRecusa(r);
        else setPreviaFora(toApiError(e).message);
      });
    }, ESPERA_DA_PREVIA_MS);
    return () => clearTimeout(espera);
  }, [corpoDoSalvar, bloqueioLocal, resultado, fechada, sessionId]);

  async function pedirProposta(answers: TrainingAnswer[] = []) {
    if (editado) {
      const { confirmed } = await confirm({
        title: answers.length ? 'Pedir nova proposta com as respostas?' : 'Pedir outra proposta?',
        confirmLabel: answers.length ? 'Pedir nova proposta' : 'Pedir outra proposta',
        cancelLabel: 'Voltar',
        body: 'A proposta atual e o que você mudou nela (comando, etapas, destino das entradas, ações do catálogo) são substituídos pela nova. O que você escolheu em Vale para continua.',
      });
      if (!confirmed) return;
    }
    setPensando(true);
    try {
      const s = await api.proposeTraining(sessionId, answers);
      // A origem não muda numa sessão e o contexto completo só vem da leitura (`GET /training/{id}`): a resposta da proposta não a apaga.
      setSessao((ant) => ({ ...s, origin: ant?.origin ?? s.origin }));
      setRespostas({});
      setErroResposta(null);
      setProposta(s.proposal);
      setOriginal(s.proposal);
      setDestino({});
      setAviso('');
    } catch (e) {
      // v1.63: resposta com cara de senha ou código fica no campo dela, sem toast (o texto não pode ir adiante).
      const err = toApiError(e);
      // O backend não diz qual resposta (o corpo é só code e message): marca todas as que foram nesta chamada.
      if (err.code === 'resposta_sensivel') {
        setErroResposta({ message: err.message, question: null });
      } else if (err.code === 'proposta_concorrente') {
        // Outro pedido de proposta desta gravação terminou antes: a tela relê a sessão para mostrar a que valeu.
        toastError('A proposta mudou enquanto você pedia', e);
        try {
          const s = await api.getTraining(sessionId);
          setSessao(s);
          setProposta(s.proposal);
          setOriginal(s.proposal);
        } catch {
          /* a releitura falhou: a tela fica com a proposta que tinha, e o próximo pedido confere de novo */
        }
      } else toastError('A IA não conseguiu propor o fluxo', e);
    } finally {
      setPensando(false);
    }
  }

  function mudarProposta(parcial: Partial<TrainingProposal>) {
    setProposta((p) => (p ? { ...p, ...parcial } : p));
  }

  function mudarEtapa(i: number, parcial: Partial<TrainingStep>) {
    setProposta((p) => (p ? { ...p, steps: p.steps.map((s, k) => (k === i ? { ...s, ...parcial } : s)) } : p));
  }

  // Depois de mover, o foco vai para o controle da entrada no lugar novo e a região de status diz para onde ela foi.
  function descartar(seq: number, frase = `#${seq} foi para Descartadas; para desfazer, devolva a uma etapa.`) {
    setProposta((p) => (p ? descartarEntrada(p, seq) : p));
    setAviso(frase);
    setFocar(`descartada-${seq}`);
  }

  function devolver(seq: number, etapa: number) {
    setProposta((p) => (p ? devolverEntrada(p, seq, etapa) : p));
    setDestino((d) => {
      const n = { ...d };
      delete n[seq];
      return n;
    });
    setAviso(`#${seq} voltou à etapa ${etapa + 1}${proposta?.steps[etapa] ? ` (${proposta.steps[etapa].title})` : ''}.`);
    setFocar(`etapa-${seq}`);
  }

  async function salvar() {
    if (!proposta) return;
    setSalvando(true);
    try {
      const r = await api.saveTraining(sessionId, { proposal: proposta, ...corpoDoEscopo(vale, escolhidosP, escolhidosG) });
      setResultado(r);
      setSemReceitaAoSalvar(r.steps.some((x) => !x.recipe));
      const avisos = r.warnings ?? [];
      toast({ tone: 'success', title: 'Fluxo salvo',
              message: `${r.steps.filter((x) => x.recipe).length} de ${r.steps.length} etapas já rodam sem IA.${avisos.length ? ` Avisos: ${avisos.join(' · ')}` : ''}` });
    } catch (e) {
      // A recusa da conferência trava o "Salvar" com o motivo até a próxima edição; a do comando fica só no campo.
      const r = recusaDe(e);
      if (r?.code === 'closed') setFechada(true);
      if (r) setRecusa(r);
      // 31.128: a recusa da pós-condição que já vale traz a lista; o aviso fica dentro das etapas, sem toast repetindo a frase.
      const itens = r?.code === 'pos_condicao_ja_vale' ? lerPosCondicoes(toApiError(e).detail?.pos_condicoes_ja_valem) : [];
      if (itens.length) setJaValem(itens);
      if ((!r || !CODIGOS_DO_COMANDO.has(r.code)) && !itens.length) toastError('Não foi possível salvar o fluxo', e);
    } finally {
      setSalvando(false);
    }
  }


  async function gerarHabilidade() {
    if (!resultado || convertendo) return;
    const { confirmed } = await confirm({
      title: 'Gerar a habilidade deste fluxo?',
      confirmLabel: 'Gerar habilidade',
      cancelLabel: 'Agora não',
      body: 'A versão 1 da habilidade é o plano deste fluxo, sem mudança: as execuções seguem iguais e as receitas continuam valendo. '
        + 'A versão 2 fica em rascunho, para dar tipo aos parâmetros, anotar riscos e versionar. O fluxo é desligado na mesma '
        + 'operação, e dá para desfazer em Configuração → Fluxos e receitas.',
    });
    if (!confirmed) return;
    setConvertendo(true);
    try {
      setConversao(await api.adoptFlow(resultado.flow_id));
    } catch (e) {
      toastError('Não foi possível gerar a habilidade', e);
    } finally {
      setConvertendo(false);
    }
  }

  // 31.119: descartar a sessão inteira (a gravação já está concluída). A confirmação e o controle ficam no helper; descartada, a revisão fecha.
  async function descartarSessao() {
    if (!sessao || descartando || salvando) return;
    setDescartando(true);
    try {
      if (await descartarSessaoConcluida(sessao)) onClose();
    } finally {
      setDescartando(false);
    }
  }

  // "Depois", Esc e o clique no fundo passam por aqui: com edição pendente, a pessoa confirma antes de perder.
  async function fechar(): Promise<boolean> {
    if ((editado || escopoMudou) && proposta && !resultado) {
      const { confirmed } = await confirm({
        title: 'Sair sem salvar o fluxo?',
        danger: true,
        confirmLabel: 'Sair sem salvar',
        cancelLabel: 'Voltar',
        body: 'O que você mudou na proposta (comando, etapas, destino das entradas, quem recebe) se perde. A gravação continua na lista "Para revisar".',
      });
      if (!confirmed) return false;
    }
    onClose();
    return true;
  }

  // 31.119: o selo "corrige uma falha" abre a execução de origem; a revisão sai primeiro (com a confirmação de sempre se há edição).
  async function abrirExecucao(href: string) {
    if (await fechar()) window.location.hash = href;
  }

  const alterna = (set: Set<string>, id: string) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  };

  function motivoNaoSalvar(): string | null {
    const local = motivoLocal();
    if (local) return local;
    const daPosCondicao = motivoDaPosCondicao(jaValem, (k) => proposta?.steps.find((s) => s.key === k)?.title ?? null);
    if (daPosCondicao) return daPosCondicao;
    if (recusa) return CODIGOS_DO_COMANDO.has(recusa.code) ? 'Corrija o comando: o motivo está no campo.' : recusa.message;
    return null;
  }

  function motivoLocal(): string | null {
    if (!proposta) return 'Peça a proposta da IA primeiro.';
    // O save recusaria as duas (`entradas_sem_etapa`, `entrada_duplicada`): a tela diz antes e diz como resolver.
    if (semDestino.length) return `Falta destino para ${listaDeSeqs(semDestino.map((e) => e.seq))}: descarte ou devolva a uma etapa.`;
    if (duplicadas.length) return `${listaDeSeqs(duplicadas)} está em mais de um lugar: descarte ou devolva a uma etapa só.`;
    // As listas só importam em "Escolher": nas outras duas o corpo não leva perfil nem grupo.
    if (vale === 'quem_ensinou' && !sessao?.profile_id) return 'Este treino não teve persona: não há “quem ensinou”. Escolha outra opção em Vale para.';
    if (vale === 'escolher' && escopo.carregando) return 'Aguarde a lista de perfis e grupos.';
    if (vale === 'escolher' && escopo.erro) return 'A lista de perfis e grupos não carregou: sem ela, "nada marcado" não quer dizer "todos". Tente de novo.';
    return null;
  }

  const duplicada = new Set(duplicadas);
  const previaPorEtapa = new Map((previa?.steps ?? []).map((x) => [x.key, x]));
  // A linha de `warnings` que a etapa já mostra por dentro (31.128) não se repete na lista de avisos da prévia.
  const avisosDaPrevia = (previa?.warnings ?? []).filter((w) => !(previa?.code && w === previa.message)).filter((w) => !jaValem.some((j) => j.message && j.message === w && proposta?.steps.some((s) => s.key === j.etapa)));
  const linhaDaEntrada = (seq: number) => {
    const e = porSeq.get(seq);
    return (
      <>
        {e ? <DescricaoEntrada e={e} /> : <span className={styles.muted}>entrada que a gravação não tem</span>}
        {duplicada.has(seq) ? <> <Badge size="sm" tone="warning">em mais de um lugar</Badge></> : null}
      </>
    );
  };

  // `onde` é o lugar desta linha (`etapa`, `descartada`, `sem-destino`): é a chave do foco depois de mover a entrada.
  const acoesDaEntrada = (seq: number, onde: 'etapa' | 'descartada' | 'sem-destino') => (
    <span className={styles.entryActions}>
      {onde !== 'descartada' ? (
        <Button ref={onde === 'etapa' ? alvo(`etapa-${seq}`) : undefined} size="sm" variant="ghost"
                label={`Descartar a entrada #${seq}`} onClick={() => descartar(seq)}>Descartar</Button>
      ) : duplicada.has(seq) ? (
        <Button size="sm" variant="ghost" label={`Manter a entrada #${seq} só em Descartadas`}
                onClick={() => descartar(seq, `#${seq} ficou só em Descartadas.`)}>Manter descartada</Button>
      ) : null}
      {onde !== 'etapa' && proposta?.steps.length ? (
        <>
          {/* R1: escolher a etapa não move nada; quem move é o botão (antes, o select movia na troca de opção). */}
          <Select ref={onde === 'descartada' ? alvo(`descartada-${seq}`) : undefined} small
                  aria-label={`Devolver a entrada #${seq} à etapa`} value={destino[seq] ?? ''}
                  onChange={(ev) => { const v = ev.target.value; setDestino((d) => ({ ...d, [seq]: v })); }}>
            <option value="">Escolha a etapa…</option>
            {proposta.steps.map((s, i) => <option key={s.key} value={i}>{`${i + 1}. ${s.title}`}</option>)}
          </Select>
          <Button size="sm" variant="ghost" label={`Devolver a entrada #${seq}`}
                  disabledReason={destino[seq] ? null : 'Escolha a etapa primeiro.'}
                  onClick={() => devolver(seq, Number(destino[seq]))}>Devolver</Button>
        </>
      ) : null}
    </span>
  );

  return (
    <Dialog open onClose={() => void fechar()} title={sessao ? `Treinamento: ${sessao.intent}` : 'Treinamento'} icon={WandSparkles} size="lg"
            footer={resultado ? <Button onClick={onClose}>Fechar</Button> : (
              <>
                {sessao && (sessao.status === 'recorded' || sessao.status === 'proposed') ? (
                  <Button variant="dangerGhost" icon={Trash2} loading={descartando}
                          disabledReason={salvando ? 'Salvando o fluxo…' : null} onClick={() => void descartarSessao()}>Descartar</Button>
                ) : null}
                <Button variant="ghost" title="Fecha a revisão sem salvar: a gravação continua na lista “Para revisar”." onClick={() => void fechar()}>Depois</Button>
                <Button variant={proposta ? 'primary' : 'secondary'} icon={Sparkles} loading={salvando}
                        disabledReason={motivoNaoSalvar()} onClick={() => void salvar()}>
                  Salvar como fluxo
                </Button>
              </>
            )}>
      {!sessao ? (
        falhaSessao ? (
          <EmptyState icon={ServerCrash} tone="danger" compact title="Não foi possível abrir o treinamento" hint={falhaSessao.hint}
                      actions={<Button variant="outline" icon={RefreshCw} onClick={() => setTentativa((t) => t + 1)}>Tentar de novo</Button>}>
            {falhaSessao.message}
          </EmptyState>
        ) : (
          <LoadingRegion label="Carregando o treinamento…">
            <Skeleton height={120} radius={8} />
          </LoadingRegion>
        )
      ) : resultado ? (
        <div className={styles.result}>
          <p>
            Fluxo <strong>{resultado.flow_id}</strong> salvo
            {resultado.ensinado_em_prova ? <> <SeloEmProva ensinado={resultado.ensinado_em_prova} /></> : null}
            . {resultado.ensinado_em_prova ? quemUsaEmProva(resultado.ensinado_em_prova).comando : 'Quem estiver no escopo pode pedir pelo comando:'}
          </p>
          {resultado.ensinado_em_prova ? (
            <p className={styles.muted} role="status">{explicacaoEmProva(resultado.ensinado_em_prova)}</p>
          ) : null}
          {/* 31.132: o estado do fluxo no Livro agora, o próximo passo e o link para o item. */}
          <FluxoNoLivro flowId={resultado.flow_id} />
          {/* 31.111 F5: o fluxo candidato que nasceu da correção de uma etapa que falhou diz de onde veio. */}
          {sessao.origin ? (
            <p className={styles.muted}>
              <SeloDeOrigem origin={sessao.origin} /> Este fluxo nasceu da correção da etapa <span className="mono">{sessao.origin.step_key}</span> da
              execução <span className="mono">{sessao.origin.run_id}</span>.
            </p>
          ) : null}
          {resultado.scope ? (
            <p className={styles.muted}>Vale para {textoDoEscopo(resultado.scope, escopo.perfis, escopo.grupos)}.{resultado.ensinado_em_prova ? '' : ` ${ATE_A_PROVA}`}</p>
          ) : null}
          <code className={styles.command}>{proposta?.command_template}</code>
          <ul className={styles.stepReport}>
            {resultado.steps.map((s) => (
              <li key={s.key}>
                <Badge size="sm" tone={s.recipe ? 'success' : 'neutral'}>{s.recipe ? 'sem IA' : 'com IA'}</Badge> {s.title}
                <span className={styles.muted}> — {s.reason}</span>
                <PacotesAceitos pacotes={s.pacotes_aceitos} />
              </li>
            ))}
          </ul>
          {resultado.warnings?.length ? (
            <ul className={styles.questions} aria-label="Avisos do salvar">{resultado.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          ) : null}
          {semReceitaAoSalvar ? (
            <RefazerReceitas sessionId={sessionId} onFeito={(r) => setResultado((x) => (x ? { ...x, steps: r.steps, ensinado_em_prova: r.ensinado_em_prova } : x))} />
          ) : null}
          {habilidades ? (
            conversao ? (
              <p role="status">
                Habilidade <strong>{conversao.skill_id}</strong>: {conversao.published.ref} publicada (o plano deste fluxo) e
                {' '}{conversao.draft.ref} em rascunho, em Configuração → Fluxos e receitas → Habilidades.
              </p>
            ) : (
              <span className={styles.actions}>
                <Button size="sm" variant="outline" icon={GraduationCap} loading={convertendo} onClick={() => void gerarHabilidade()}>
                  Gerar habilidade deste fluxo
                </Button>
                <span className={styles.muted}>Parâmetros com tipo, riscos e versões só existem na habilidade.</span>
              </span>
            )
          ) : null}
        </div>
      ) : (
        <div className={styles.review}>
          {sessao.origin ? <div className={styles.origemLinha}><OrigemDoTreino origin={sessao.origin} aoAbrirExecucao={(href) => void abrirExecucao(href)} /></div> : null}
          <div className={styles.recordingCol}>
            <h4 className={styles.sub}>O que você fez ({plural(sessao.inputs?.length ?? 0, 'entrada', 'entradas')})</h4>
            <ol className={styles.inputList}>
              {agruparTeclas(sessao.inputs ?? [], descartadas).map((g) => {
                const e = g[0]!;
                const fim = g[g.length - 1]!;
                return (
                  <li key={e.seq} className={descartadas.has(e.seq) ? styles.discarded : undefined}>
                    <span className={styles.seq}>{g.length > 1 ? `#${e.seq}–#${fim.seq}` : `#${e.seq}`}</span>
                    <span>
                      <DescricaoEntrada e={e} />
                      {g.length > 1 ? <> ×{g.length}</> : null}
                      {e.screen_title ? <span className={styles.muted}> · tela {e.screen_title}</span> : null}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>

          <div className={styles.proposalCol}>
            {/* Nasce vazia: só o que entra depois (a última entrada movida) é anunciado. */}
            <p className={styles.muted} role="status" aria-live="polite">{aviso}</p>
            {!proposta ? (
              <div className={styles.ask}>
                <p>A IA vai ler a gravação e propor o fluxo: o comando com o que varia, as etapas com o objetivo de cada
                  uma e o que foi engano. Uma chamada do modelo do planejador (poucos centavos).</p>
                <Button variant="primary" icon={WandSparkles} loading={pensando}
                        disabledReason={(sessao.inputs ?? []).length ? null : 'Nada foi gravado: só dá para descartar.'} onClick={() => void pedirProposta()}>Pedir proposta à IA</Button>
              </div>
            ) : (
              <>
                {semDestino.length ? (
                  <section className={styles.semDestino} aria-label="Sem destino">
                    <h4 className={styles.sub}>Sem destino ({semDestino.length})</h4>
                    <p className={styles.muted}>Entradas gravadas que não estão em nenhuma etapa nem no descarte. Dê um destino a cada uma para salvar.</p>
                    <ul className={styles.entryList}>
                      {semDestino.map((e) => (
                        <li key={e.seq}>
                          <span className={styles.seq}>#{e.seq}</span>
                          <span className={styles.entryText}><DescricaoEntrada e={e} /></span>
                          {acoesDaEntrada(e.seq, 'sem-destino')}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
                <Field label="Comando (o que varia fica entre chaves)"
                       error={recusa && CODIGOS_DO_COMANDO.has(recusa.code) ? recusa.message : null}>
                  {({ id, describedBy, invalid }) => (
                    <TextInput id={id} value={proposta.command_template} aria-describedby={describedBy} invalid={invalid}
                               onChange={(e) => mudarProposta({ command_template: e.target.value })} />
                  )}
                </Field>
                {proposta.parameters.length ? (
                  <>
                    <p className={styles.params}>
                      {proposta.parameters.map((p) => <Badge key={p.name} size="sm" tone="accent">{`{${p.name}}`} = {p.example}</Badge>)}
                    </p>
                    <EditorDosParametros parametros={proposta.parameters} onChange={(parameters) => mudarProposta({ parameters })} />
                  </>
                ) : null}
                {proposta.questions.length ? (
                  <section className={styles.questions} aria-label="Perguntas da IA">
                    <p className={styles.muted}>Responda o que souber; a resposta vai para a IA. Não escreva senha nem código.</p>
                    {proposta.questions.map((q) => (
                      <Field key={q} label={q} error={erroResposta && (erroResposta.question === null || erroResposta.question === q) && (respostas[q] ?? '').trim()
                        ? erroResposta.message : null}>
                        {({ id, describedBy, invalid }) => (
                          <TextInput id={id} aria-describedby={describedBy} invalid={invalid} maxLength={MAX_RESPOSTA} value={respostas[q] ?? ''}
                                     onChange={(e) => { setRespostas((r) => ({ ...r, [q]: e.target.value })); setErroResposta(null); }} />
                        )}
                      </Field>
                    ))}
                  </section>
                ) : null}
                {proposta.answers?.length ? (
                  <ul className={styles.questions} aria-label="Respostas já dadas">
                    {proposta.answers.map((a) => <li key={a.question}>{a.question} <strong>{a.answer}</strong></li>)}
                  </ul>
                ) : null}
                <ol className={styles.steps}>
                  {proposta.steps.map((s, i) => (
                    <li key={s.key} className={styles.step}>
                      <div className={styles.stepHead}>
                        <TextInput aria-label={`Título da etapa ${i + 1}`} value={s.title} onChange={(e) => mudarEtapa(i, { title: e.target.value })} />
                        {s.side_effect ? <Badge size="sm" tone="warning">efeito externo</Badge> : null}
                      </div>
                      <TextInput aria-label={`Objetivo da etapa ${i + 1}`} value={s.goal} onChange={(e) => mudarEtapa(i, { goal: e.target.value })} />
                      {s.inputs.length ? (
                        <ul className={styles.entryList} aria-label={`Entradas da etapa ${i + 1}`}>
                          {s.inputs.map((n, k) => (
                            <li key={`${n}-${k}`}>
                              <span className={styles.seq}>#{n}</span>
                              <span className={styles.entryText}>{linhaDaEntrada(n)}</span>
                              {acoesDaEntrada(n, 'etapa')}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      <p className={styles.muted}>Confere: {textoDoConfere(s.postcondition)}
                        {s.inputs.map((n) => porSeq.get(n)).filter(Boolean).length ? '' : ' · sem entradas: a IA conduz esta etapa'}</p>
                      {/* 31.141: o que a IA propôs vale; sem isso, o que a prévia calcula para a etapa. */}
                      <PacotesAceitos pacotes={s.pacotes_aceitos?.length ? s.pacotes_aceitos : previaPorEtapa.get(s.key)?.pacotes_aceitos} />
                      {jaValem.filter((j) => j.etapa === s.key).map((j) => (
                        <AvisoDaPosCondicao key={j.valor} item={j}
                                            onUsar={(p) => mudarEtapa(i, { postcondition: { ...s.postcondition, kind: p.kind as typeof s.postcondition.kind, value: p.value } })} />
                      ))}
                      <EditorDaPosCondicao indice={i} etapa={s} onChange={(postcondition) => mudarEtapa(i, { postcondition })} />
                      {previaPorEtapa.get(s.key) ? (
                        <p className={styles.muted}>
                          <Badge size="sm" tone={previaPorEtapa.get(s.key)!.recipe ? 'success' : 'neutral'}>
                            {previaPorEtapa.get(s.key)!.recipe ? 'sem IA' : 'com IA'}
                          </Badge> Ao salvar: {previaPorEtapa.get(s.key)!.recipe
                            // 29.146: o motivo literal da prévia (v1.58) já diz "ao salvar"; com receita, a frase é do painel.
                            ? 'a receita desta etapa é gravada.'
                            : previaPorEtapa.get(s.key)!.reason}
                        </p>
                      ) : null}
                      {acoes.length && (s.side_effect || s.capability) ? (
                        <Select aria-label={`Ação do catálogo da etapa ${i + 1}`} value={s.capability ?? ''}
                                onChange={(e) => mudarEtapa(i, { capability: e.target.value || null })}>
                          <option value="">{s.side_effect ? 'Escolha a ação do catálogo…' : 'Sem ação do catálogo'}</option>
                          {acoes.map((c) => <option key={c.key} value={c.key}>{c.title}</option>)}
                        </Select>
                      ) : null}
                    </li>
                  ))}
                </ol>
                {avisosDaPrevia.length ? (
                  <ul className={styles.questions} aria-label="Avisos da prévia">{avisosDaPrevia.map((w) => <li key={w}>{w}</li>)}</ul>
                ) : null}
                {previaFora ? <p className={styles.muted}>Prévia indisponível: {previaFora} O salvar confere de novo.</p> : null}
                {descarteUnico.length ? (
                  <section aria-label="Descartadas">
                    <h4 className={styles.sub}>Descartadas ({descarteUnico.length})</h4>
                    <ul className={styles.entryList}>
                      {descarteUnico.map((d) => (
                        <li key={d.seq}>
                          <span className={styles.seq}>#{d.seq}</span>
                          <span className={styles.entryText}>{linhaDaEntrada(d.seq)} <span className={styles.muted}>— {d.why}</span></span>
                          {acoesDaEntrada(d.seq, 'descartada')}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
                <fieldset className={styles.scope}>
                  <legend>Vale para</legend>
                  <SeletorValePara modo={vale} onModo={(m) => { setVale(m); setEscopoMudou(true); }} temPersona={!!sessao?.profile_id}
                                   grupoDoNome={`vale-${sessionId}`}>
                  {previa?.scope ? (
                    <p className={styles.muted} role="status">Ao salvar vale para {textoDoEscopo(previa.scope, escopo.perfis, escopo.grupos)}. {ATE_A_PROVA}</p>
                  ) : null}
                  {vale !== 'escolher' ? null : escopo.erro ? (
                    <Banner tone="danger" icon={ServerCrash} compact role="alert" title="A lista de perfis e grupos não carregou"
                            actions={<Button size="sm" variant="outline" icon={RefreshCw} loading={escopo.carregando} onClick={() => void carregarEscopo()}>Tentar de novo</Button>}>
                      {escopo.erro.message} {escopo.erro.hint}
                    </Banner>
                  ) : escopo.carregando ? (
                    <LoadingRegion label="Carregando perfis e grupos…">
                      <Skeleton height={20} width="60%" />
                    </LoadingRegion>
                  ) : (
                    <>
                      <p className={styles.muted}>Nada marcado = todos os perfis.</p>
                      <div className={styles.scopeGrid}>
                        {escopo.grupos.map((g) => (
                          <label key={g.id}><input type="checkbox" aria-label={`Grupo ${g.name}`} checked={escolhidosG.has(g.id)}
                                                   onChange={() => { setEscolhidosG((x) => alterna(x, g.id)); setEscopoMudou(true); }} /> grupo {g.name}</label>
                        ))}
                        {escopo.perfis.map((p) => (
                          <label key={p.id}><input type="checkbox" aria-label={`@${p.username}`} checked={escolhidosP.has(p.id)}
                                                   onChange={() => { setEscolhidosP((x) => alterna(x, p.id)); setEscopoMudou(true); }} /> @{p.username}</label>
                        ))}
                      </div>
                    </>
                  )}
                  </SeletorValePara>
                </fieldset>
                <span className={styles.actions}>
                  <Button size="sm" variant={respondidas.length ? 'outline' : 'ghost'} icon={WandSparkles} loading={pensando}
                          disabledReason={respondidas.length > MAX_RESPOSTAS_POR_PEDIDO
                            ? `Mande até ${MAX_RESPOSTAS_POR_PEDIDO} respostas por vez: deixe as outras em branco e responda na próxima proposta.` : null}
                          onClick={() => void pedirProposta(respondidas)}>
                    {respondidas.length ? 'Pedir nova proposta com as respostas' : 'Pedir outra proposta'}
                  </Button>
                  <span className={styles.muted}>Uma chamada da IA (poucos centavos); a proposta inteira é trocada pela nova.</span>
                </span>
              </>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}
