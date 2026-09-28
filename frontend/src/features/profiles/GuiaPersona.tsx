/**
 * Guia "Persona": quem a pessoa é (biografia por seção) e como ela escreve (a voz). Desde a 047 a persona É a
 * linha do perfil — nada de procurar "a persona do perfil" numa lista: `GET /personas/{id}` devolve a pessoa.
 *
 * Cada seção da biografia salva com um PATCH só dela (o servidor mescla; `null` apaga a chave). O que da biografia
 * vai ao modelo (`PERSONA_BIO_FIELDS`) leva a marca "vai ao modelo"; Crenças são guardadas e NÃO vão.
 */
import { ChevronRight, Settings2, Sparkles, TriangleAlert } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { PersonaBiography, PersonaDTO, PersonaPatchRequest, PersonaTraits, SocialDraft } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { cx } from '../../lib/format';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { toast, toastError } from '../../store/toasts';
import { Carregando } from './detalheComum';
import {
  CompletenessGauge, EMOJI_OPTIONS, ExampleBubbles, FORMALITY_OPTIONS, LENGTH_OPTIONS, PairColumns,
  PhraseColumns, ROTULO_DE_VOZ, Ruler, TagList,
} from './PersonaVisual';
import { PERSONA_BIO_FIELDS, type Pessoa } from './pessoa';
import { SecaoEditavel, comoTexto, paraLista, paraNumero, paraTexto, type CampoDef } from './SecaoEditavel';
import styles from './Profiles.module.css';

const DATA_ISO = /^\d{4}-\d{2}-\d{2}$/;
const VAI_AO_MODELO = 'vai ao modelo';

type Bloco = Exclude<keyof PersonaBiography, 'schema_version' | 'approx_age'>;

/** Campo da biografia: `bloco.campo`, com a marca de "vai ao modelo" tirada da MESMA lista do backend. */
function bio(chave: `${Bloco}.${string}`, rotulo: string, extra: Partial<CampoDef> = {}): CampoDef {
  return { chave, rotulo, ...(PERSONA_BIO_FIELDS.has(chave) ? { unidade: VAI_AO_MODELO } : {}), ...extra };
}

interface DefSecao {
  titulo: string;
  subtitulo?: string;
  campos: CampoDef[];
  naoVaiAoModelo?: boolean;
}

const SECOES_DA_BIOGRAFIA: DefSecao[] = [
  {
    titulo: 'Origem e casa',
    campos: [
      bio('origin.birthplace', 'Nasceu em'), bio('origin.hometown', 'Cidade onde cresceu'),
      bio('origin.nationality', 'Nacionalidade'), bio('home.city', 'Cidade onde mora'), bio('home.state', 'Estado'),
      bio('home.country', 'País'), bio('home.residence', 'Moradia', { dica: 'Ex.: apartamento com a irmã.' }),
    ],
  },
  {
    titulo: 'Trabalho',
    campos: [
      bio('work.profession', 'Profissão'), bio('work.employer', 'Onde trabalha'),
      bio('work.education', 'Formação', { tipo: 'lista', dica: 'Uma por linha.' }),
    ],
  },
  {
    titulo: 'Vida',
    campos: [
      bio('life.marital_status', 'Estado civil'), bio('life.children', 'Filhos', { tipo: 'numero' }),
      bio('life.history', 'Marcos da vida', { tipo: 'lista', dica: 'Um por linha.' }),
    ],
  },
  {
    titulo: 'Crenças',
    subtitulo: 'Ficam guardadas e não são enviadas a nenhum modelo até decisão do dono.',
    naoVaiAoModelo: true,
    campos: [bio('beliefs.religion', 'Religião'), bio('beliefs.politics', 'Política')],
  },
  {
    titulo: 'Gostos',
    subtitulo: 'Os interesses continuam na voz (abaixo): é de lá que o modelo os lê.',
    campos: [
      bio('tastes.hobbies', 'Hobbies', { tipo: 'lista', dica: 'Um por linha.' }),
      bio('tastes.preferences', 'Preferências', { tipo: 'lista', dica: 'Uma por linha.' }),
      bio('tastes.dislikes', 'Não gosta de', { tipo: 'lista', dica: 'Um por linha.' }),
    ],
  },
];

const CAMPOS_DE_IDENTIDADE: CampoDef[] = [
  { chave: 'name', rotulo: 'Nome', unidade: VAI_AO_MODELO },
  { chave: 'first_name', rotulo: 'Primeiro nome' },
  { chave: 'last_name', rotulo: 'Sobrenome' },
  { chave: 'birth_date', rotulo: 'Nascimento', dica: 'AAAA-MM-DD. Ao modelo vai só a idade.' },
  { chave: 'gender', rotulo: 'Gênero' },
  { chave: 'locale', rotulo: 'Idioma', dica: 'Ex.: pt-BR.' },
];

function valorDaBio(p: PersonaDTO, chave: string): unknown {
  const [bloco, campo] = chave.split('.') as [Bloco, string];
  const b = (p.biography?.[bloco] ?? {}) as Record<string, unknown>;
  return b[campo];
}

/** O PATCH de uma seção: só os blocos e campos DELA, convertidos do texto do formulário. */
function patchDaSecao(campos: CampoDef[], v: Record<string, string>): PersonaPatchRequest {
  const biografia: Record<string, Record<string, unknown>> = {};
  for (const c of campos) {
    const [bloco, campo] = c.chave.split('.') as [string, string];
    const valor = c.tipo === 'lista' ? paraLista(v[c.chave]) : c.tipo === 'numero' ? paraNumero(v[c.chave]) : paraTexto(v[c.chave]);
    biografia[bloco] = { ...(biografia[bloco] ?? {}), [campo]: valor };
  }
  return { biography: biografia as PersonaBiography };
}

export function AbaPersona({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const [persona, setPersona] = useState<PersonaDTO | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  // "Tentar de novo" só refaz a leitura: incrementar aqui é o jeito de reexecutar o efeito sem duplicar a lógica.
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => {
    let vivo = true;
    setErro(null);
    api.getPersona(profile.id)
      .then((p) => { if (vivo) setPersona(p); })
      // O erro fica na guia, com "Tentar de novo": antes ia para um toast e o esqueleto ficava para sempre.
      .catch((e) => { if (vivo) setErro(toLoadError(e)); });
    return () => { vivo = false; };
  }, [profile.id, tentativa]);

  async function salvar(patch: PersonaPatchRequest, ok: string, falha: string): Promise<void> {
    if (!persona) return;
    try {
      setPersona(await api.updatePersona(persona.id, patch));
      toast({ tone: 'success', title: ok });
      await onChanged();
    } catch (e) {
      toastError(falha, e);
    }
  }

  if (erro && !persona) return <LoadErrorState what="a persona" error={erro} onRetry={() => setTentativa((t) => t + 1)} />;
  if (!persona) return <Carregando />;

  return (
    <div className={styles.stack}>
      <p className={styles.detail}>
        Cada seção salva sozinha. Da biografia, só o que tem a marca <Badge size="sm" tone="info">{VAI_AO_MODELO}</Badge>{' '}
        entra no texto que o modelo recebe (cidade, profissão, formação e hobbies); o resto fica guardado.
      </p>
      <div className={styles.secoes}>
        <SecaoEditavel
          titulo="Identidade"
          campos={CAMPOS_DE_IDENTIDADE}
          iniciais={{
            name: persona.name ?? '', first_name: persona.first_name ?? '', last_name: persona.last_name ?? '',
            birth_date: persona.birth_date ?? '', gender: persona.gender ?? '', locale: persona.locale ?? '',
          }}
          invalido={(v) => (!v.name?.trim() ? 'O nome não pode ficar vazio.'
            : v.birth_date?.trim() && !DATA_ISO.test(v.birth_date.trim()) ? 'Nascimento no formato AAAA-MM-DD.' : null)}
          onSalvar={(v) => salvar({
            name: v.name?.trim(), first_name: paraTexto(v.first_name), last_name: paraTexto(v.last_name),
            birth_date: paraTexto(v.birth_date), gender: paraTexto(v.gender), locale: paraTexto(v.locale),
          }, 'Identidade salva', 'Não foi possível salvar a identidade')}
        />
        {SECOES_DA_BIOGRAFIA.map((s) => (
          <SecaoEditavel
            key={s.titulo}
            titulo={s.titulo}
            subtitulo={s.subtitulo}
            marca={s.naoVaiAoModelo ? <Badge size="sm" tone="warning">guardadas, não vão ao modelo</Badge> : undefined}
            campos={s.campos}
            iniciais={Object.fromEntries(s.campos.map((c) => [c.chave, comoTexto(valorDaBio(persona, c.chave))]))}
            onSalvar={(v) => salvar(patchDaSecao(s.campos, v), `${s.titulo}: salvo`, `Não foi possível salvar ${s.titulo.toLowerCase()}`)}
          />
        ))}
      </div>
      <VozAtual persona={persona} onSalvar={(patch) => salvar(patch, 'Voz atualizada', 'Não foi possível salvar a voz')} />
    </div>
  );
}

/** A voz: o que o modelo recebe para escrever como esta pessoa. Editar um traço manda SÓ aquele traço. */
function VozAtual({ persona, onSalvar }: { persona: PersonaDTO; onSalvar: (patch: PersonaPatchRequest) => Promise<void> }) {
  const [editando, setEditando] = useState(false);
  const t: PersonaTraits = persona.traits ?? {};
  const faltando = persona.voice_gaps ?? [];
  const traco = (patch: PersonaTraits) => void onSalvar({ traits: patch });

  return (
    <div className={styles.personaLayout}>
      <Card>
        <CardHeader title="Quem é · voz atual" subtitle={persona.name}
                    actions={
                      <Button size="sm" variant="ghost" icon={Settings2} onClick={() => setEditando((v) => !v)}>
                        {editando ? 'Fechar edição' : 'Editar a voz'}
                      </Button>
                    } />
        <CardBody className={styles.identityCard}>
          {faltando.length > 0 ? (
            <Banner tone="warning" icon={TriangleAlert} role="status"
                    title={`Faltam ${faltando.length} campo(s) de voz nesta persona`}>
              Sem eles o modelo só tem tom, formalidade, tamanho e emoji para diferenciar esta conta das outras —
              e contas diferentes acabam escrevendo parecido. Faltam:{' '}
              {faltando.map((c) => ROTULO_DE_VOZ[c] ?? c).join(', ')}.
            </Banner>
          ) : null}
          {persona.summary ? <p className={styles.lead}>{persona.summary}</p> : null}
          {t.personality ? <p className={styles.personality}>“{t.personality}”</p> : null}
          <div className={styles.pair}>
            <Ruler label="Formalidade" options={FORMALITY_OPTIONS} value={t.formality} />
            <Ruler label="Tamanho típico" options={LENGTH_OPTIONS} value={t.typical_length} />
          </div>
          <Ruler label="Emojis" options={EMOJI_OPTIONS} value={t.emojis} />
          <TagList items={t.interests ?? []} empty="Sem interesses registrados." />
          <PhraseColumns common={t.common_phrases ?? []} forbidden={t.forbidden_phrases ?? []} />
          <div>
            <h4>Exemplos</h4>
            <ExampleBubbles examples={t.examples ?? []} />
          </div>
          <PairColumns leftLabel="Com quem já conhece" left={t.with_known} rightLabel="Com desconhecidos" right={t.with_strangers} />
          <PairColumns leftLabel="Em mensagem direta" left={t.dm_style} rightLabel="Em comentário" right={t.comment_style} />
          <CompletenessGauge total={Object.keys(ROTULO_DE_VOZ).length} missing={faltando} />

          {editando ? (
            <div className={cx(styles.form, styles.editablePanel)}>
              <Field label="Resumo">
                {({ id }) => (
                  <TextInput id={id} defaultValue={persona.summary ?? ''}
                             onBlur={(e) => void onSalvar({ summary: e.target.value || null })} />
                )}
              </Field>
              <Field label="Tom" hint="Ex.: animado, direto, acolhedor.">
                {({ id }) => <TextInput id={id} defaultValue={t.tone ?? ''} onBlur={(e) => traco({ tone: e.target.value || null })} />}
              </Field>
              <Field label="Formalidade">
                {({ id }) => (
                  <Select id={id} defaultValue={t.formality ?? ''}
                          onChange={(e) => traco({ formality: (e.target.value || null) as PersonaTraits['formality'] })}>
                    <option value="">—</option>
                    <option value="informal">Informal</option>
                    <option value="neutro">Neutro</option>
                    <option value="formal">Formal</option>
                  </Select>
                )}
              </Field>
              <Field label="Tamanho típico">
                {({ id }) => (
                  <Select id={id} defaultValue={t.typical_length ?? ''}
                          onChange={(e) => traco({ typical_length: (e.target.value || null) as PersonaTraits['typical_length'] })}>
                    <option value="">—</option>
                    <option value="curta">Curta</option>
                    <option value="media">Média</option>
                    <option value="longa">Longa</option>
                  </Select>
                )}
              </Field>
              <Field label="Emojis">
                {({ id }) => (
                  <Select id={id} defaultValue={t.emojis ?? ''}
                          onChange={(e) => traco({ emojis: (e.target.value || null) as PersonaTraits['emojis'] })}>
                    <option value="">—</option>
                    <option value="nunca">Nunca</option>
                    <option value="raro">Raro</option>
                    <option value="moderado">Moderado</option>
                    <option value="muito">Muito</option>
                  </Select>
                )}
              </Field>
              <Field label="Personalidade" hint="Quem é esta pessoa em uma frase.">
                {({ id }) => <TextArea id={id} rows={2} defaultValue={t.personality ?? ''} onBlur={(e) => traco({ personality: e.target.value || null })} />}
              </Field>
              <Field label="Gírias" hint="Como ela fala no dia a dia. Ex.: usa “mano”, “top”.">
                {({ id }) => <TextInput id={id} defaultValue={t.slang ?? ''} onBlur={(e) => traco({ slang: e.target.value || null })} />}
              </Field>
              <Field label="Humor" hint="Ex.: irônica, brincalhona, séria.">
                {({ id }) => <TextInput id={id} defaultValue={t.humor ?? ''} onBlur={(e) => traco({ humor: e.target.value || null })} />}
              </Field>
              <Field label="Interesses" hint="Um por linha.">
                {({ id }) => <TextArea id={id} rows={2} defaultValue={(t.interests ?? []).join('\n')} onBlur={(e) => traco({ interests: paraLista(e.target.value) })} />}
              </Field>
              <Field label="Estilo em mensagem direta" hint="Como ela escreve numa DM: abertura, tamanho, jeito.">
                {({ id }) => <TextArea id={id} rows={2} defaultValue={t.dm_style ?? ''} onBlur={(e) => traco({ dm_style: e.target.value || null })} />}
              </Field>
              <Field label="Estilo em comentário" hint="Como ela comenta uma publicação.">
                {({ id }) => <TextArea id={id} rows={2} defaultValue={t.comment_style ?? ''} onBlur={(e) => traco({ comment_style: e.target.value || null })} />}
              </Field>
              <Field label="Com quem já conhece">
                {({ id }) => <TextArea id={id} rows={2} defaultValue={t.with_known ?? ''} onBlur={(e) => traco({ with_known: e.target.value || null })} />}
              </Field>
              <Field label="Com desconhecidos">
                {({ id }) => <TextArea id={id} rows={2} defaultValue={t.with_strangers ?? ''} onBlur={(e) => traco({ with_strangers: e.target.value || null })} />}
              </Field>
              <Field label="Expressões comuns" hint="Uma por linha. São as que ela usa de verdade.">
                {({ id }) => <TextArea id={id} rows={3} defaultValue={(t.common_phrases ?? []).join('\n')} onBlur={(e) => traco({ common_phrases: paraLista(e.target.value) })} />}
              </Field>
              <Field label="Expressões proibidas" hint="Uma por linha. O que esta persona NUNCA escreveria.">
                {({ id }) => <TextArea id={id} rows={3} defaultValue={(t.forbidden_phrases ?? []).join('\n')} onBlur={(e) => traco({ forbidden_phrases: paraLista(e.target.value) })} />}
              </Field>
              <Field label="Exemplos" hint="Uma mensagem por linha, escrita como ela escreveria.">
                {({ id }) => <TextArea id={id} rows={4} defaultValue={(t.examples ?? []).join('\n')} onBlur={(e) => traco({ examples: paraLista(e.target.value) })} />}
              </Field>
              <Field label="Instruções da persona" hint="Vai direto ao modelo, junto com a memória da persona.">
                {({ id }) => (
                  <TextArea id={id} rows={4} defaultValue={persona.persona_prompt ?? ''}
                            onBlur={(e) => void onSalvar({ persona_prompt: e.target.value })} />
                )}
              </Field>
              <p className={styles.detail}>A identidade visual (aparência, estilo, cenário) fica na guia Imagens: ela não vai ao modelo que escreve.</p>
            </div>
          ) : null}
        </CardBody>
      </Card>
      <TestarPersona persona={persona} />
    </div>
  );
}

/** Prévia de escrita, como sempre foi (`POST /personas/{id}/preview`): nada é publicado. */
function TestarPersona({ persona }: { persona: PersonaDTO }) {
  const [rascunho, setRascunho] = useState<SocialDraft | null>(null);
  const [recebido, setRecebido] = useState('oi! tudo bem?');
  const [intencao, setIntencao] = useState('');
  const [tipo, setTipo] = useState<'dm_reply' | 'dm_initiate' | 'post_comment'>('dm_reply');
  const [testando, setTestando] = useState(false);

  async function testar() {
    setTestando(true);
    try {
      setRascunho(await api.previewPersona(persona.id, {
        kind: tipo,
        // Puxar conversa/comentar NÃO tem mensagem recebida: mandar o campo mesmo assim faria a prévia conferir
        // um prompt que a execução nunca monta.
        incoming: tipo === 'dm_reply' ? recebido : '',
        brief: tipo === 'dm_reply' ? '' : intencao,
        profile_id: persona.id,
      }));
    } catch (e) {
      toastError('Não foi possível testar a persona', e);
    } finally {
      setTestando(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Testar persona" subtitle="Mostra como ela escreveria. Nada é publicado." />
      <CardBody>
        {/* Painel lateral/recolhível: aberto por padrão, mas pode ser fechado sem perder o cartão de identidade
            acima. `<details>` mantém o conteúdo acessível a teclado e leitor de tela sem JS extra. */}
        <details open>
          <summary className={styles.disclosure}>
            <ChevronRight size={14} aria-hidden />
            Prévia de escrita
          </summary>
          <div className={styles.form} style={{ marginTop: 'var(--sp-3)' }}>
            <Field label="O que testar">
              {({ id }) => (
                <Select id={id} value={tipo} onChange={(e) => setTipo(e.target.value as typeof tipo)}>
                  <option value="dm_reply">Responder uma mensagem</option>
                  <option value="dm_initiate">Puxar conversa (mensagem direta)</option>
                  <option value="post_comment">Comentar uma publicação</option>
                </Select>
              )}
            </Field>
            {tipo === 'dm_reply' ? (
              <Field label="Mensagem recebida">
                {({ id }) => <TextArea id={id} rows={3} value={recebido} onChange={(e) => setRecebido(e.target.value)} />}
              </Field>
            ) : (
              <Field label="Intenção" hint="A mesma que o comando daria. Ex.: cumprimentar, dizer boa tarde.">
                {({ id }) => <TextArea id={id} rows={3} value={intencao} onChange={(e) => setIntencao(e.target.value)} />}
              </Field>
            )}
            <Button icon={Sparkles} loading={testando}
                    disabledReason={(tipo === 'dm_reply' ? recebido : intencao).trim() ? null
                      : tipo === 'dm_reply' ? 'Escreva a mensagem que a persona receberia.'
                        : 'Escreva a intenção deste texto.'}
                    onClick={() => void testar()}>
              Testar persona
            </Button>
            {rascunho ? (
              <div className={styles.draft}>
                {rascunho.refused ? (
                  <p className={styles.detail}>Recusou responder: {rascunho.refusal_reason}</p>
                ) : (
                  <>
                    <p className={styles.draftText}>{rascunho.content}</p>
                    <p className={styles.detail}>{rascunho.rationale}</p>
                  </>
                )}
              </div>
            ) : null}
          </div>
        </details>
      </CardBody>
    </Card>
  );
}
