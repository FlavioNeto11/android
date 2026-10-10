/**
 * Os dois caminhos de cadastro de uma persona (evolução 2, onda E1; design §6 e §9.4). A persona nasce SEM conta e
 * sem aparelho: o @ do Instagram, a senha e o vínculo vêm depois, dentro dela (guia Contas e acesso, e Configuração
 * → Instâncias e contas). O formulário antigo "usuário + senha + aparelho" deixou de ser a porta de entrada.
 *
 * - Por prompt: `POST /personas/generate` é uma chamada PAGA ao papel `persona` e NÃO grava nada; o rascunho volta
 *   para a pessoa revisar e só então `POST /personas` cria. O aviso de custo fica na cara, antes do botão.
 * - Por prompt, em LOTE (v0.34, "Quantidade" > 1): `POST /personas/generate/batch` gera N pessoas diferentes em
 *   segundo plano; a pessoa escolhe antes se cria direto ou revisa os rascunhos, e vê o custo estimado do lote antes
 *   de confirmar. Quantidade 1 é o fluxo de sempre, sem mudança.
 * - Manual: nome e o pouco que a pessoa souber; o resto se completa depois, seção por seção.
 */
import { Layers, Sparkles, TriangleAlert, UserRound, Wand2 } from 'lucide-react';
import { useState } from 'react';
import { api, apiLote, toApiError } from '../../api/client';
import type { AiStatus, PersonaCreateRequest, PersonaDTO } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Checkbox, Field, TextArea, TextInput } from '../../components/Field';
import { toast, toastError } from '../../store/toasts';
import { politicaDe, religiaoDe, resumoDaPolitica, resumoDaReligiao } from './CrencasPersona';
import { type LinhaDeCusto, custoDaGeracao, custoDasFotos, fotosAutomaticas, papelDaPersona, personaSimulado } from './custos';
import { LoteDePersonas } from './LoteDePersonas';
import styles from './Profiles.module.css';
import { useAiStatus } from './useAiStatus';

/** Estimativa do design §6.5 (papel persona no Sonnet, ~4 mil tokens de entrada e ~1,5 mil de saída). O valor real
 *  sai de `ai_calls` depois; aqui é só para a pessoa decidir antes de gastar. */
export const CUSTO_ESTIMADO_POR_PERSONA = '≈ US$ 0,02–0,03 por persona';

const DATA_ISO = /^\d{4}-\d{2}-\d{2}$/;

/** O lote aceita de 1 a 10 pessoas por pedido: o mesmo teto de `PersonaBatchBody.count` no servidor. */
export const MAX_POR_LOTE = 10;
/** Revisar: os rascunhos voltam para a pessoa escolher; criar: cada rascunho válido já vira persona. */
type ModoDoLote = 'revisar' | 'criar';

/** A primeira foto nasce sozinha quando `ai.image.on_create` está ligado: dizer isso evita a pessoa gerar outra. */
function avisoDaFoto(ai: AiStatus | null): string | null {
  const img = ai?.image;
  if (!img?.on_create || !img.per_persona) return null;
  return img.simulated
    ? 'A primeira foto é gerada em segundo plano pelo gerador simulado (sem custo).'
    : `A primeira foto é gerada em segundo plano (${img.provider}${img.price_per_image_usd != null
      ? `, ≈ US$ ${img.price_per_image_usd.toFixed(3)} por imagem` : ''}).`;
}

const TITULO_DO_ERRO: Record<string, string> = {
  persona_draft_invalid: 'O rascunho veio fora das regras',
  ai_budget: 'Teto de gasto de IA atingido',
  ai_refusal: 'O modelo recusou o pedido',
  ai_error: 'O provedor de IA falhou',
  ai_unavailable: 'IA indisponível',
};

/** O rascunho, no que a pessoa revisa antes de criar. O resto (voz completa, visual, biografia) vai como veio. */
interface Revisao {
  name: string;
  summary: string;
  birth_date: string;
  gender: string;
  city: string;
  profession: string;
  /** 31.315: criar já marcada como persona de teste. */
  teste: boolean;
}

/** 31.315: a marca de persona de teste ao criar. Só vai no corpo quando marcada. */
function CampoDeTeste({ marcada, onChange }: { marcada: boolean; onChange: (v: boolean) => void }) {
  return (
    <Checkbox label="Persona de teste" aria-label="Persona de teste" checked={marcada} onChange={(e) => onChange(e.target.checked)}
              title="Serve só para provar o painel e as rotinas. Fica escondida da lista de Personas, a menos que você peça para mostrá-las." />
  );
}

function revisaoDe(r: PersonaCreateRequest): Revisao {
  return {
    name: r.name ?? '',
    summary: r.summary ?? '',
    birth_date: r.birth_date ?? '',
    gender: r.gender ?? '',
    city: r.biography?.home?.city ?? '',
    profession: r.biography?.work?.profession ?? '',
    teste: r.teste === true,
  };
}

/** Junta o que a pessoa editou ao rascunho, sem perder o que ela não viu (voz, visual, proveniência). */
function aplicarRevisao(r: PersonaCreateRequest, v: Revisao): PersonaCreateRequest {
  const bio = r.biography ?? {};
  return {
    ...r,
    name: v.name.trim(),
    summary: v.summary.trim() || null,
    birth_date: v.birth_date.trim() || null,
    gender: v.gender.trim() || null,
    ...(v.teste ? { teste: true } : {}),
    biography: {
      ...bio,
      home: { ...(bio.home ?? {}), city: v.city.trim() || null },
      work: { ...(bio.work ?? {}), profession: v.profession.trim() || null },
    },
  };
}

export function NovaPersonaPorPrompt({ onClose, onCriada, onLote, onAbrir }: {
  onClose: () => void;
  onCriada: (p: PersonaDTO) => Promise<void>;
  /** Lote: relê a lista (as criadas aparecem) sem abrir ninguém. */
  onLote?: () => Promise<void>;
  /** Lote: "Abrir" numa pessoa criada. */
  onAbrir?: (id: string) => void;
}) {
  const { ai, falhou } = useAiStatus();
  const [pedido, setPedido] = useState('');
  const [genero, setGenero] = useState('');
  const [cidade, setCidade] = useState('');
  const [idade, setIdade] = useState('');
  const [gerando, setGerando] = useState(false);
  const [erro, setErro] = useState<{ code: string; message: string } | null>(null);
  const [rascunho, setRascunho] = useState<PersonaCreateRequest | null>(null);
  const [revisao, setRevisao] = useState<Revisao | null>(null);
  const [criando, setCriando] = useState(false);
  const [quantidade, setQuantidade] = useState('1');
  const [modoLote, setModoLote] = useState<ModoDoLote>('revisar');
  const [confirmandoLote, setConfirmandoLote] = useState(false);
  const [loteId, setLoteId] = useState<string | null>(null);

  const papel = papelDaPersona(ai);
  const simulado = personaSimulado(ai);
  const tamanho = pedido.trim().length;
  const n = Number(quantidade);
  const quantidadeValida = quantidade.trim() !== '' && Number.isInteger(n) && n >= 1 && n <= MAX_POR_LOTE;
  const emLote = quantidadeValida && n > 1;
  // O custo do lote é mostrado ANTES de confirmar: N gerações pagas + as fotos automáticas das que forem criadas.
  const custos: LinhaDeCusto[] = emLote
    ? [custoDaGeracao(ai, n),
       custoDasFotos(ai, fotosAutomaticas(ai, n),
                     modoLote === 'criar' ? 'Fotos automáticas' : 'Fotos automáticas (só das que você criar)')]
      .filter((c): c is LinhaDeCusto => c !== null)
    : [];
  const lotePago = custos.some((c) => c.pago);
  const motivoDoPedido = tamanho < 3 ? 'Descreva a pessoa (3 a 2000 caracteres).'
    : tamanho > 2000 ? 'O pedido passa de 2000 caracteres.'
      : !quantidadeValida ? `Quantidade de 1 a ${MAX_POR_LOTE}.` : null;

  function restricoesDoPedido(): Record<string, string> {
    const restricoes: Record<string, string> = {};
    if (genero.trim()) restricoes.gender = genero.trim();
    if (cidade.trim()) restricoes.city = cidade.trim();
    if (idade.trim()) restricoes.age = idade.trim();
    return restricoes;
  }

  async function gerar() {
    if (tamanho < 3 || tamanho > 2000 || gerando) return;
    setGerando(true);
    setErro(null);
    try {
      const r = await api.generatePersona({ prompt: pedido.trim(), constraints: restricoesDoPedido() });
      setRascunho(r);
      setRevisao(revisaoDe(r));
    } catch (e) {
      const x = toApiError(e);
      setErro({ code: x.code, message: x.message });
    } finally {
      setGerando(false);
    }
  }

  /** Lote pago passa por uma confirmação com o custo; o simulado não custa e vai direto. */
  function pedirLote() {
    if (motivoDoPedido || !emLote || gerando) return;
    if (lotePago) setConfirmandoLote(true);
    else void gerarLote();
  }

  async function gerarLote() {
    if (motivoDoPedido || !emLote || gerando) return;
    setGerando(true);
    setErro(null);
    try {
      const r = await apiLote.generatePersonaBatch({
        prompt: pedido.trim(), constraints: restricoesDoPedido(), count: n, create: modoLote === 'criar',
      });
      setLoteId(r.batch_id);
    } catch (e) {
      const x = toApiError(e);
      setErro({ code: x.code, message: x.message });
    } finally {
      setGerando(false);
      setConfirmandoLote(false);
    }
  }

  async function criar() {
    if (!rascunho || !revisao || !revisao.name.trim() || criando) return;
    setCriando(true);
    try {
      const criada = await api.createPersona(aplicarRevisao(rascunho, revisao));
      toast({ tone: 'success', title: `${criada.name} criada`,
              message: avisoDaFoto(ai) ?? 'Contas, fotos e aparelho se ajustam dentro da persona.' });
      await onCriada(criada);
    } catch (e) {
      toastError('Não foi possível criar a persona', e);
    } finally {
      setCriando(false);
    }
  }

  function editar<K extends Exclude<keyof Revisao, 'teste'>>(k: K, v: string) {
    setRevisao((r) => (r ? { ...r, [k]: v } : r));
  }

  const voz = rascunho?.traits ?? {};
  const idadeInvalida = !!revisao?.birth_date.trim() && !DATA_ISO.test(revisao.birth_date.trim());

  // Lote aceito: o diálogo passa a ser o do progresso (o lote roda no servidor; fechar não o interrompe).
  if (loteId) return <LoteDePersonas batchId={loteId} onClose={onClose} onAbrir={onAbrir} onLote={onLote} />;

  const custoDoLote = (
    <div className={styles.loteCusto} role="group" aria-label="Custo estimado do lote">
      <p className={styles.loteCustoTitulo}>Custo estimado do lote</p>
      <ul className={styles.loteCustoLista}>
        {custos.map((c) => <li key={c.rotulo}><strong>{c.rotulo}:</strong> {c.texto}</li>)}
      </ul>
    </div>
  );

  return (
    <Dialog
      open
      onClose={onClose}
      title="Nova persona a partir de uma descrição"
      icon={Wand2}
      size="md"
      footer={rascunho && revisao ? (
        <>
          <Button variant="secondary" onClick={() => { setRascunho(null); setRevisao(null); }}>Voltar ao pedido</Button>
          <Button variant="primary" icon={UserRound} loading={criando}
                  disabledReason={!revisao.name.trim() ? 'Dê um nome à persona.'
                    : idadeInvalida ? 'Nascimento no formato AAAA-MM-DD.' : null}
                  onClick={() => void criar()}>
            Criar persona
          </Button>
        </>
      ) : confirmandoLote ? (
        <>
          <Button variant="secondary" onClick={() => setConfirmandoLote(false)}>Voltar</Button>
          <Button variant="primary" icon={Layers} loading={gerando} onClick={() => void gerarLote()}>
            Confirmar e gerar {n}
          </Button>
        </>
      ) : (
        <>
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          {emLote ? (
            <Button variant="primary" icon={Layers} loading={gerando} disabledReason={motivoDoPedido} onClick={pedirLote}>
              Gerar {n} personas
            </Button>
          ) : (
            <Button variant="primary" icon={Sparkles} loading={gerando} disabledReason={motivoDoPedido}
                    onClick={() => void gerar()}>
              Gerar rascunho
            </Button>
          )}
        </>
      )}
    >
      <div className={styles.form}>
        {/* O aviso vem ANTES do botão, não depois do gasto: o saldo da API é pequeno e separado. */}
        {!ai && !falhou ? (
          <p className={styles.detail}>Lendo o provedor de IA…</p>
        ) : simulado ? (
          <Banner tone="info" icon={Sparkles} role="status" title="Provedor simulado: sem custo">
            O rascunho sai do gerador simulado desta máquina, determinístico e sem chamada externa.
          </Banner>
        ) : (
          <Banner tone="warning" icon={TriangleAlert} role="status" title="É uma chamada paga de IA">
            Gerar o rascunho chama o papel <strong>persona</strong>
            {papel ? <> (<span className="mono">{papel.model}</span> em {papel.provider})</> : null}
            {falhou ? ' — não foi possível ler o provedor agora, então conte com custo' : null}
            : custo estimado {CUSTO_ESTIMADO_POR_PERSONA}. O texto do pedido e as restrições saem desta máquina;
            tela, memória e senha, nunca. {emLote && modoLote === 'criar'
              ? 'Em “Criar direto”, cada rascunho válido do lote já vira persona.'
              : 'Nada é gravado até você revisar e clicar em “Criar persona”.'}
          </Banner>
        )}

        {erro ? (
          <Banner tone="danger" icon={TriangleAlert} role="alert" title={TITULO_DO_ERRO[erro.code] ?? 'Não foi possível gerar'}>
            {erro.message}
          </Banner>
        ) : null}

        {confirmandoLote ? (
          <>
            <Banner tone="warning" icon={TriangleAlert} role="status" title={`Confirmar ${n} gerações pagas`}>
              O lote roda no servidor, duas pessoas por vez, e continua mesmo se você fechar esta janela.
              {modoLote === 'criar' ? ' Cada rascunho válido vira persona sem revisão.'
                : ' Os rascunhos voltam para você escolher quais criar.'} Se o teto de gasto do dia for atingido,
              o lote para e os itens restantes aparecem como falha, sem nova tentativa.
            </Banner>
            <p className={styles.detail}>Pedido: “{pedido.trim()}”</p>
            {custoDoLote}
          </>
        ) : rascunho && revisao ? (
          <>
            <p className={styles.detail}>
              Rascunho gerado{rascunho.generation?.model ? ` por ${rascunho.generation.model}` : ''} — ainda não gravado.
              Revise o que quiser; voz, identidade visual e biografia completas se ajustam depois na guia Persona.
            </p>
            <Field label="Nome">
              {({ id }) => <TextInput id={id} value={revisao.name} onChange={(e) => editar('name', e.target.value)} />}
            </Field>
            <Field label="Resumo">
              {({ id }) => <TextArea id={id} rows={2} value={revisao.summary} onChange={(e) => editar('summary', e.target.value)} />}
            </Field>
            <div className={styles.pair}>
              <Field label="Nascimento" unit="AAAA-MM-DD" error={idadeInvalida ? 'Use o formato AAAA-MM-DD.' : null}
                     hint={!revisao.birth_date && rascunho.biography?.approx_age
                       ? `Sem data: idade aproximada ${rascunho.biography.approx_age} anos.` : undefined}>
                {({ id, invalid }) => (
                  <TextInput id={id} invalid={invalid} value={revisao.birth_date}
                             onChange={(e) => editar('birth_date', e.target.value)} />
                )}
              </Field>
              <Field label="Gênero">
                {({ id }) => <TextInput id={id} value={revisao.gender} onChange={(e) => editar('gender', e.target.value)} />}
              </Field>
            </div>
            <div className={styles.pair}>
              <Field label="Cidade">
                {({ id }) => <TextInput id={id} value={revisao.city} onChange={(e) => editar('city', e.target.value)} />}
              </Field>
              <Field label="Profissão">
                {({ id }) => <TextInput id={id} value={revisao.profession} onChange={(e) => editar('profession', e.target.value)} />}
              </Field>
            </div>
            <CampoDeTeste marcada={revisao.teste} onChange={(teste) => setRevisao((r) => (r ? { ...r, teste } : r))} />
            <div className={styles.draft}>
              <p className={styles.draftText}><strong>Voz</strong>{voz.tone ? ` · ${voz.tone}` : ''}{voz.formality ? ` · ${voz.formality}` : ''}</p>
              {voz.personality ? <p className={styles.detail}>“{voz.personality}”</p> : null}
              {(voz.interests ?? []).length ? (
                <p className={styles.detail}>Interesses: {(voz.interests ?? []).join(', ')}</p>
              ) : null}
              {/* Crenças (ADR-048) vêm no rascunho e vão ao modelo: uma linha cada, para a revisão não esconder. */}
              {resumoDaReligiao(religiaoDe(rascunho.biography)) ? (
                <p className={styles.detail}>Religião: {resumoDaReligiao(religiaoDe(rascunho.biography))}</p>
              ) : null}
              {resumoDaPolitica(politicaDe(rascunho.biography)) ? (
                <p className={styles.detail}>Política: {resumoDaPolitica(politicaDe(rascunho.biography))}</p>
              ) : null}
            </div>
          </>
        ) : (
          <>
            <Field label="Pedido" hint="Quem é esta pessoa: idade, cidade, trabalho, jeito de falar. De 3 a 2000 caracteres.">
              {({ id, describedBy }) => (
                <TextArea id={id} rows={4} aria-describedby={describedBy} value={pedido} maxLength={2000}
                          placeholder="Professora de biologia em Recife, 30 e poucos anos, fala de trilhas e plantas"
                          onChange={(e) => setPedido(e.target.value)} />
              )}
            </Field>
            <div className={styles.pair}>
              <Field label="Gênero" unit="opcional">
                {({ id }) => <TextInput id={id} value={genero} onChange={(e) => setGenero(e.target.value)} />}
              </Field>
              <Field label="Cidade" unit="opcional">
                {({ id }) => <TextInput id={id} value={cidade} onChange={(e) => setCidade(e.target.value)} />}
              </Field>
            </div>
            <div className={styles.pair}>
              <Field label="Faixa de idade" unit="opcional" hint="Ex.: 30-35. Sempre adulta: o gerador recusa menor de idade.">
                {({ id, describedBy }) => (
                  <TextInput id={id} aria-describedby={describedBy} value={idade} placeholder="30-35"
                             onChange={(e) => setIdade(e.target.value)} />
                )}
              </Field>
              <Field label="Quantidade" unit={`1 a ${MAX_POR_LOTE}`}
                     error={!quantidadeValida ? `Use um número inteiro de 1 a ${MAX_POR_LOTE}.` : null}
                     hint="Com mais de uma, as pessoas saem diferentes entre si e das que já existem.">
                {({ id, describedBy, invalid }) => (
                  <TextInput id={id} type="number" inputMode="numeric" min={1} max={MAX_POR_LOTE} step={1}
                             aria-describedby={describedBy} invalid={invalid} value={quantidade}
                             onChange={(e) => setQuantidade(e.target.value)} />
                )}
              </Field>
            </div>
            {emLote ? (
              <>
                <fieldset className={styles.loteModo}>
                  <legend className={styles.loteModoTitulo}>Depois de gerar as {n} pessoas</legend>
                  <label className={styles.loteOpcao}>
                    <input type="radio" name="modo-do-lote" value="revisar" checked={modoLote === 'revisar'}
                           onChange={() => setModoLote('revisar')} />
                    <span><strong>Revisar antes de criar</strong>
                      <span className={styles.muted}> — os rascunhos voltam com caixa de seleção e você cria as escolhidas.</span>
                    </span>
                  </label>
                  <label className={styles.loteOpcao}>
                    <input type="radio" name="modo-do-lote" value="criar" checked={modoLote === 'criar'}
                           onChange={() => setModoLote('criar')} />
                    <span><strong>Criar direto</strong>
                      <span className={styles.muted}> — cada rascunho válido já vira persona, sem revisão.</span>
                    </span>
                  </label>
                </fieldset>
                {custoDoLote}
              </>
            ) : null}
          </>
        )}
        {avisoDaFoto(ai) ? <p className={styles.detail}><Badge size="sm" tone="info">foto</Badge> {avisoDaFoto(ai)}</p> : null}
      </div>
    </Dialog>
  );
}

export function NovaPersonaManual({ onClose, onCriada }: {
  onClose: () => void;
  onCriada: (p: PersonaDTO) => Promise<void>;
}) {
  const { ai } = useAiStatus();
  const [nome, setNome] = useState('');
  const [nascimento, setNascimento] = useState('');
  const [genero, setGenero] = useState('');
  const [resumo, setResumo] = useState('');
  const [teste, setTeste] = useState(false);
  const [erros, setErros] = useState<Record<string, string>>({});
  const [salvando, setSalvando] = useState(false);

  async function salvar() {
    const e: Record<string, string> = {};
    if (!nome.trim()) e.nome = 'Dê um nome à persona.';
    if (nascimento.trim() && !DATA_ISO.test(nascimento.trim())) e.nascimento = 'Use o formato AAAA-MM-DD.';
    setErros(e);
    if (Object.keys(e).length || salvando) return;
    setSalvando(true);
    try {
      const criada = await api.createPersona({
        name: nome.trim(),
        birth_date: nascimento.trim() || null,
        gender: genero.trim() || null,
        summary: resumo.trim() || null,
        ...(teste ? { teste: true } : {}),
      });
      toast({ tone: 'success', title: `${criada.name} criada`,
              message: avisoDaFoto(ai) ?? 'Complete a biografia, a voz e as contas dentro da persona.' });
      await onCriada(criada);
    } catch (err) {
      toastError('Não foi possível criar a persona', err);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title="Nova persona manual"
      icon={UserRound}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" loading={salvando} onClick={() => void salvar()}>Criar persona</Button>
        </>
      }
    >
      <div className={styles.form}>
        <p className={styles.detail}>
          A persona nasce sem conta e sem aparelho. O @ do Instagram e a senha entram depois, na guia Contas e acesso.
        </p>
        <Field label="Nome" error={erros.nome}>
          {({ id, describedBy, invalid }) => (
            <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={nome}
                       placeholder="Rita Fagundes" onChange={(e) => setNome(e.target.value)} />
          )}
        </Field>
        <div className={styles.pair}>
          <Field label="Nascimento" unit="opcional" error={erros.nascimento}>
            {({ id, describedBy, invalid }) => (
              <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={nascimento}
                         placeholder="1991-08-22" onChange={(e) => setNascimento(e.target.value)} />
            )}
          </Field>
          <Field label="Gênero" unit="opcional">
            {({ id }) => <TextInput id={id} value={genero} onChange={(e) => setGenero(e.target.value)} />}
          </Field>
        </div>
        <Field label="Resumo" unit="opcional" hint="Quem é esta pessoa em uma ou duas frases.">
          {({ id, describedBy }) => (
            <TextArea id={id} rows={2} aria-describedby={describedBy} value={resumo}
                      onChange={(e) => setResumo(e.target.value)} />
          )}
        </Field>
        <CampoDeTeste marcada={teste} onChange={setTeste} />
        {avisoDaFoto(ai) ? <p className={styles.detail}>{avisoDaFoto(ai)}</p> : null}
      </div>
    </Dialog>
  );
}
