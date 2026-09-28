/**
 * Os dois caminhos de cadastro de uma persona (evolução 2, onda E1; design §6 e §9.4). A persona nasce SEM conta e
 * sem aparelho: o @ do Instagram, a senha e o vínculo vêm depois, dentro dela (guia Contas e acesso, e Configuração
 * → Instâncias e contas). O formulário antigo "usuário + senha + aparelho" deixou de ser a porta de entrada.
 *
 * - Por prompt: `POST /personas/generate` é uma chamada PAGA ao papel `social` e NÃO grava nada; o rascunho volta
 *   para a pessoa revisar e só então `POST /personas` cria. O aviso de custo fica na cara, antes do botão.
 * - Manual: nome e o pouco que a pessoa souber; o resto se completa depois, seção por seção.
 */
import { Sparkles, TriangleAlert, UserRound, Wand2 } from 'lucide-react';
import { useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { AiStatus, PersonaCreateRequest, PersonaDTO } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Field, TextArea, TextInput } from '../../components/Field';
import { toast, toastError } from '../../store/toasts';
import { politicaDe, religiaoDe, resumoDaPolitica, resumoDaReligiao } from './CrencasPersona';
import styles from './Profiles.module.css';
import { useAiStatus } from './useAiStatus';

/** Estimativa do design §6.5 (papel social no Sonnet, ~4 mil tokens de entrada e ~1,5 mil de saída). O valor real
 *  sai de `ai_calls` depois; aqui é só para a pessoa decidir antes de gastar. */
export const CUSTO_ESTIMADO_POR_PERSONA = '≈ US$ 0,02–0,03 por persona';

const DATA_ISO = /^\d{4}-\d{2}-\d{2}$/;

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
}

function revisaoDe(r: PersonaCreateRequest): Revisao {
  return {
    name: r.name ?? '',
    summary: r.summary ?? '',
    birth_date: r.birth_date ?? '',
    gender: r.gender ?? '',
    city: r.biography?.home?.city ?? '',
    profession: r.biography?.work?.profession ?? '',
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
    biography: {
      ...bio,
      home: { ...(bio.home ?? {}), city: v.city.trim() || null },
      work: { ...(bio.work ?? {}), profession: v.profession.trim() || null },
    },
  };
}

export function NovaPersonaPorPrompt({ onClose, onCriada }: {
  onClose: () => void;
  onCriada: (p: PersonaDTO) => Promise<void>;
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

  const social = ai?.roles?.find((r) => r.role === 'social') ?? null;
  const simulado = !!ai && (social ? social.kind === 'simulated' || social.provider === 'simulated' : ai.simulated);
  const tamanho = pedido.trim().length;

  async function gerar() {
    if (tamanho < 3 || tamanho > 2000 || gerando) return;
    setGerando(true);
    setErro(null);
    try {
      const restricoes: Record<string, string> = {};
      if (genero.trim()) restricoes.gender = genero.trim();
      if (cidade.trim()) restricoes.city = cidade.trim();
      if (idade.trim()) restricoes.age = idade.trim();
      const r = await api.generatePersona({ prompt: pedido.trim(), constraints: restricoes });
      setRascunho(r);
      setRevisao(revisaoDe(r));
    } catch (e) {
      const x = toApiError(e);
      setErro({ code: x.code, message: x.message });
    } finally {
      setGerando(false);
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

  function editar<K extends keyof Revisao>(k: K, v: string) {
    setRevisao((r) => (r ? { ...r, [k]: v } : r));
  }

  const voz = rascunho?.traits ?? {};
  const idadeInvalida = !!revisao?.birth_date.trim() && !DATA_ISO.test(revisao.birth_date.trim());

  return (
    <Dialog
      open
      onClose={onClose}
      title="Nova persona a partir de um prompt"
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
      ) : (
        <>
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" icon={Sparkles} loading={gerando}
                  disabledReason={tamanho < 3 ? 'Descreva a pessoa (3 a 2000 caracteres).'
                    : tamanho > 2000 ? 'O pedido passa de 2000 caracteres.' : null}
                  onClick={() => void gerar()}>
            Gerar rascunho
          </Button>
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
            Gerar o rascunho chama o papel <strong>social</strong>
            {social ? <> (<span className="mono">{social.model}</span> em {social.provider})</> : null}
            {falhou ? ' — não foi possível ler o provedor agora, então conte com custo' : null}
            : custo estimado {CUSTO_ESTIMADO_POR_PERSONA}. O texto do pedido e as restrições saem desta máquina;
            tela, memória e senha, nunca. Nada é gravado até você revisar e clicar em “Criar persona”.
          </Banner>
        )}

        {erro ? (
          <Banner tone="danger" icon={TriangleAlert} role="alert" title={TITULO_DO_ERRO[erro.code] ?? 'Não foi possível gerar'}>
            {erro.message}
          </Banner>
        ) : null}

        {rascunho && revisao ? (
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
            <Field label="Faixa de idade" unit="opcional" hint="Ex.: 30-35. Sempre adulta: o gerador recusa menor de idade.">
              {({ id, describedBy }) => (
                <TextInput id={id} aria-describedby={describedBy} value={idade} placeholder="30-35"
                           onChange={(e) => setIdade(e.target.value)} />
              )}
            </Field>
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
                       placeholder="Mariana Costa" onChange={(e) => setNome(e.target.value)} />
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
        {avisoDaFoto(ai) ? <p className={styles.detail}>{avisoDaFoto(ai)}</p> : null}
      </div>
    </Dialog>
  );
}
