/**
 * Operações em lote na lista de Personas (v0.34, pedido do dono de 28/09: "gerar mais fotos e outras operações que
 * posso fazer nas personas"). A barra aparece com a seleção e cada operação abre um diálogo com o que ela faz, o
 * custo (quando é paga) e a confirmação; ao confirmar, o painel chama a MESMA rota que a tela de cada persona usa,
 * uma vez por pessoa, três de cada vez, e mostra o resultado por pessoa — ok, ou falhou com o motivo do servidor.
 *
 * Não há rota de lote no servidor para estas operações de propósito: cada uma já tem as suas travas por pessoa
 * (menor de idade não é fotografada, `DELETE` recusa quem está vinculada ou em execução, teto de gasto), e o lote
 * só as repete. Nenhuma operação paga sai sem a pessoa ver o custo ou o aviso e confirmar.
 */
import { Ban, CheckCheck, ImagePlus, Layers, RotateCcw, ShieldCheck, Sparkles, Trash2, TriangleAlert, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from '../../api/client';
import type { PersonaDTO, PolicyGroup } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { ProgressBar } from '../../components/ProgressBar';
import { plural } from '../../lib/format';
import { CUSTO_ESTIMADO_POR_PERSONA } from './NovaPersona';
import { custoDasFotos, faixaUsd, CUSTO_POR_PERSONA_USD, imagemPaga, personaSimulado } from './custos';
import { RecusaLocal, executarEmLote, type ResultadoDoItem } from './emLote';
import { nomeDe } from './pessoa';
import styles from './Profiles.module.css';
import { useAiStatus } from './useAiStatus';

export type OperacaoDeLote = 'fotos' | 'completar' | 'grupo' | 'bloquear' | 'reativar' | 'apagar';

/** Três por vez: o bastante para andar, pouco para disputar as vagas de IA e o banco com o resto do painel. */
export const CONCORRENCIA_DO_LOTE = 3;
const MAX_FOTOS = 3;
const MAX_INSTRUCOES = 500;

/** O motivo que vira "falhou" sem requisição: o grupo de acesso governa o que a CONTA faz (o editor de grupos nem
 *  lista quem não tem @), e atribuir a quem não tem conta deixaria um membro que o editor apagaria sem avisar. */
export const SEM_CONTA_NO_GRUPO = 'Sem conta de cadastro: o grupo de acesso governa o que a conta faz. '
  + 'Crie a conta na guia Contas e acesso antes de pôr a persona num grupo.';

/**
 * Grupo, bloquear e reativar decidem pelo estado da lista na tela ("já estava…": ok e sem PATCH). Com a lista velha,
 * relatariam sucesso sem mudar nada, o defeito do 29.114; apagar, fotos e completar não têm esse atalho (29.118).
 */
export const LISTA_VELHA = 'A lista não se releu: use “Tentar de novo”.';

/** A releitura passou do prazo e segue em voo: até ela assentar, a lista na tela ainda é a velha (29.118, C1). */
export const LISTA_RELENDO = 'A lista ainda está se relendo: aguarde.';

/** Quanto o lote espera a releitura antes de mostrar o resumo dizendo que a lista não se releu (29.118). */
export const PRAZO_DA_RELEITURA_MS = 45_000;

export function BarraDeLote({
  selecionadas, grupos, onLimpar, onConcluido, listaVelha = false, prazoDaReleituraMs = PRAZO_DA_RELEITURA_MS,
}: {
  selecionadas: PersonaDTO[];
  grupos: PolicyGroup[];
  onLimpar: () => void;
  /** Relê a lista depois de uma operação (apagadas saem, status e grupo mudam); `false` quando ela não se releu. */
  onConcluido: () => Promise<boolean>;
  /** A última leitura da lista falhou: o que está na tela pode estar velho. */
  listaVelha?: boolean;
  prazoDaReleituraMs?: number;
}) {
  const [operacao, setOperacao] = useState<OperacaoDeLote | null>(null);
  // A releitura que perdeu a corrida para o prazo ainda está em voo: o erro da página segue nulo, mas a lista é a velha.
  const [relendo, setRelendo] = useState(false);
  const aguardarReleitura = useCallback((releitura: Promise<boolean>) => {
    setRelendo(true);
    void releitura.finally(() => setRelendo(false));
  }, []);
  const decidePelaLista = listaVelha ? LISTA_VELHA : relendo ? LISTA_RELENDO : null;
  const n = selecionadas.length;
  if (n === 0 && operacao === null) return null;
  return (
    <>
      {n > 0 ? (
        <div className={styles.loteDock}>
          <div className={styles.loteBarra} role="toolbar" aria-label={`Ações em ${plural(n, 'persona selecionada', 'personas selecionadas')}`}>
            <span className={styles.loteBarraRotulo}>
              <CheckCheck size={15} aria-hidden /> {plural(n, 'selecionada', 'selecionadas')}
            </span>
            <Button size="sm" icon={ImagePlus} onClick={() => setOperacao('fotos')}>Gerar mais fotos</Button>
            <Button size="sm" icon={Sparkles} onClick={() => setOperacao('completar')}>Completar com IA</Button>
            <Button size="sm" icon={ShieldCheck} disabledReason={decidePelaLista} onClick={() => setOperacao('grupo')}>Grupo de acesso</Button>
            <Button size="sm" icon={Ban} disabledReason={decidePelaLista} onClick={() => setOperacao('bloquear')}>Bloquear</Button>
            <Button size="sm" icon={RotateCcw} disabledReason={decidePelaLista} onClick={() => setOperacao('reativar')}>Reativar</Button>
            <span className={styles.loteSep} aria-hidden />
            <Button size="sm" variant="dangerGhost" icon={Trash2} onClick={() => setOperacao('apagar')}>Apagar…</Button>
            <Button size="sm" variant="ghost" icon={X} iconOnly label="Limpar seleção" onClick={onLimpar} />
          </div>
        </div>
      ) : null}
      {operacao ? (
        <DialogoDeLote key={operacao} operacao={operacao} pessoas={selecionadas} grupos={grupos}
                       onFechar={() => setOperacao(null)} onConcluido={onConcluido} prazoDaReleituraMs={prazoDaReleituraMs}
                       onReleituraPendente={aguardarReleitura} />
      ) : null}
    </>
  );
}

type Fase = 'parametros' | 'executando' | 'resumo';

function DialogoDeLote({ operacao, pessoas: selecao, grupos, onFechar, onConcluido, prazoDaReleituraMs, onReleituraPendente }: {
  operacao: OperacaoDeLote;
  pessoas: PersonaDTO[];
  grupos: PolicyGroup[];
  onFechar: () => void;
  onConcluido: () => Promise<boolean>;
  prazoDaReleituraMs: number;
  /** A releitura passou do prazo: a barra trava o que decide pela lista até ela assentar. */
  onReleituraPendente: (releitura: Promise<boolean>) => void;
}) {
  const { ai, falhou: aiFalhou } = useAiStatus();
  // A seleção é FOTOGRAFADA ao abrir: a lista se relê no fim (apagadas somem) e o resumo precisa continuar dizendo
  // quem era quem.
  const [pessoas] = useState(selecao);
  const [fase, setFase] = useState<Fase>('parametros');
  const [listaVelha, setListaVelha] = useState(false);
  // Na troca para o resumo, os botões que tinham o foco somem: ele vai para o Fechar, não para o corpo da página.
  const fecharRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (fase === 'resumo') fecharRef.current?.focus();
  }, [fase]);
  const [resultados, setResultados] = useState<(ResultadoDoItem | undefined)[]>([]);
  const [fotos, setFotos] = useState('1');
  const [instrucoes, setInstrucoes] = useState('');
  const [grupoId, setGrupoId] = useState(grupos[0]?.id ?? '');
  const [confirmacao, setConfirmacao] = useState('');
  const n = pessoas.length;
  const k = Number(fotos);
  const nomeDoGrupo = grupos.find((g) => g.id === grupoId)?.name ?? null;
  const semConta = pessoas.filter((p) => !p.username).length;

  // ------------------------------------------------ o que cada operação faz com UMA pessoa
  async function executar(p: PersonaDTO): Promise<string | void> {
    switch (operacao) {
      case 'fotos': {
        const r = await api.generatePersonaImages(p.id, k);
        return `${plural(r.count, 'foto', 'fotos')} em geração${r.simulated ? ' (simulado)' : ''}`;
      }
      case 'completar': {
        const r = await api.enrichPersona(p.id, instrucoes.trim() || undefined);
        // Sem lacuna o servidor devolve a persona sem chamar o modelo: dizer isso evita achar que nada aconteceu.
        return r.updated_at !== p.updated_at ? 'completada' : 'nada faltava: o modelo não foi chamado';
      }
      case 'grupo': {
        if (grupoId && !p.username) throw new RecusaLocal(SEM_CONTA_NO_GRUPO);
        if ((p.policy_group_id ?? '') === grupoId) return grupoId ? 'já estava no grupo' : 'já estava sem grupo';
        await api.patchProfile(p.id, { policy_group_id: grupoId || null });
        return grupoId ? `no grupo ${nomeDoGrupo ?? grupoId}` : 'fora do grupo';
      }
      case 'bloquear':
      case 'reativar': {
        const status = operacao === 'bloquear' ? 'blocked' : 'active';
        if (p.status === status) return operacao === 'bloquear' ? 'já estava bloqueada' : 'já estava ativa';
        await api.patchProfile(p.id, { status });
        return operacao === 'bloquear' ? 'bloqueada' : 'reativada';
      }
      case 'apagar':
        await api.deletePersona(p.id);
        return 'apagada';
    }
  }

  async function confirmar() {
    if (motivo || fase !== 'parametros') return;
    setFase('executando');
    setResultados([]);
    await executarEmLote(pessoas, CONCORRENCIA_DO_LOTE, executar, (i, r) => {
      setResultados((atual) => {
        const copia = [...atual];
        copia[i] = r;
        return copia;
      });
    });
    // O resumo só depois de a lista se reler: com ele na tela a pessoa fecha e reabre o lote, e a operação seguinte
    // decidia pela lista velha ("já estava sem grupo", ok e sem PATCH, com a persona ainda no grupo) (29.114).
    // As ações já foram feitas: a releitura que falha ou rejeita não as desfaz nem prende o diálogo, mas o resumo diz
    // que a lista na tela ficou velha, para ninguém decidir o próximo lote por ela.
    // O prazo cobre a rajada de releituras (cada uma mais nova espera a seguinte): o diálogo nunca fica preso em
    // "executando"; passado o prazo, conta como não relida (29.118).
    // A releitura que perde para o prazo segue em voo: vai à barra, que trava grupo, bloquear e reativar até ela
    // assentar (C1). Sem isso, o resumo dizia "não se releu" e os três ficavam livres sobre a lista velha.
    const releitura = Promise.resolve().then(onConcluido).catch(() => false);
    let estourou = false;
    let prazo: ReturnType<typeof setTimeout> | undefined;
    let relida = false;
    try {
      relida = await Promise.race([
        releitura,
        new Promise<boolean>((r) => { prazo = setTimeout(() => { estourou = true; r(false); }, prazoDaReleituraMs); }),
      ]);
    } finally {
      clearTimeout(prazo);
    }
    if (estourou) onReleituraPendente(releitura);
    setListaVelha(!relida);
    setFase('resumo');
  }

  // ------------------------------------------------ custo, aviso e confirmação por operação
  const img = ai?.image ?? null;
  const fotosPagas = imagemPaga(ai);
  const custoFotos = custoDasFotos(ai, n * (Number.isInteger(k) ? k : 0));
  const completarPago = !personaSimulado(ai);
  const esperado = `apagar ${n}`;

  let titulo: string;
  let icone = Layers;
  let rotulo: string;
  let motivo: string | null = null;
  let corpo: ReactNode;
  switch (operacao) {
    case 'fotos':
      titulo = `Gerar mais fotos de ${plural(n, 'persona', 'personas')}`;
      icone = ImagePlus;
      rotulo = `Gerar ${plural(n * k, 'foto', 'fotos')}`;
      if (img && !img.simulated && !img.configured) motivo = 'O gerador de imagem não tem chave configurada.';
      corpo = (
        <>
          <Field label="Fotos por persona" unit={`1 a ${MAX_FOTOS}`}>
            {({ id }) => (
              <Select id={id} value={fotos} onChange={(e) => setFotos(e.target.value)}>
                {Array.from({ length: MAX_FOTOS }, (_, i) => String(i + 1)).map((v) => <option key={v} value={v}>{v}</option>)}
              </Select>
            )}
          </Field>
          {fotosPagas ? (
            <Banner tone="warning" icon={TriangleAlert} role="status" title="É uma geração paga de imagem">
              Custo estimado: {custoFotos?.texto ?? '—'}. Cada foto sai da receita da persona (aparência, estilo,
              cenário); o nome nunca vai ao provedor. Persona menor de idade é recusada.
            </Banner>
          ) : (
            <Banner tone="info" icon={Sparkles} role="status" title="Gerador simulado: sem custo">
              As fotos saem do gerador simulado desta máquina.
            </Banner>
          )}
          <p className={styles.detail}>As fotos são geradas em segundo plano; cada uma aparece na guia Imagens quando fica pronta.</p>
        </>
      );
      break;
    case 'completar':
      titulo = `Completar ${plural(n, 'persona', 'personas')} com IA`;
      icone = Sparkles;
      rotulo = `Completar ${n}`;
      if (instrucoes.length > MAX_INSTRUCOES) motivo = `As instruções passam de ${MAX_INSTRUCOES} caracteres.`;
      corpo = (
        <>
          {completarPago ? (
            <Banner tone="warning" icon={TriangleAlert} role="status" title="É uma chamada paga de IA por persona">
              Cada persona com algo faltando chama o papel <strong>persona</strong>: até{' '}
              {faixaUsd(n * CUSTO_POR_PERSONA_USD.min, n * CUSTO_POR_PERSONA_USD.max)} no total
              ({CUSTO_ESTIMADO_POR_PERSONA}){aiFalhou ? ' — não foi possível ler o provedor agora, então conte com custo' : ''}.
              As completas não chamam o modelo. Só o vazio é preenchido; o que já existe não muda.
            </Banner>
          ) : (
            <Banner tone="info" icon={Sparkles} role="status" title="Provedor simulado: sem custo">
              O complemento sai do gerador simulado desta máquina. Só o vazio é preenchido.
            </Banner>
          )}
          <Field label="Instruções para o que falta" unit="opcional"
                 hint="Vale para todas as selecionadas. Ex.: “moram no interior de Minas”. Sem senha nem código.">
            {({ id, describedBy }) => (
              <TextArea id={id} rows={2} maxLength={MAX_INSTRUCOES} aria-describedby={describedBy} value={instrucoes}
                        onChange={(e) => setInstrucoes(e.target.value)} />
            )}
          </Field>
        </>
      );
      break;
    case 'grupo':
      titulo = `Grupo de acesso de ${plural(n, 'persona', 'personas')}`;
      icone = ShieldCheck;
      rotulo = grupoId ? `Pôr no grupo ${nomeDoGrupo ?? ''}`.trim() : 'Tirar do grupo';
      corpo = (
        <>
          <Field label="Grupo" hint="O grupo decide o que a conta pode fazer sozinha, com aprovação ou só à mão.">
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={grupoId} onChange={(e) => setGrupoId(e.target.value)}>
                {grupos.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                <option value="">Nenhum — tirar do grupo (volta ao padrão do catálogo)</option>
              </Select>
            )}
          </Field>
          {grupoId && semConta > 0 ? (
            <Banner tone="info" icon={TriangleAlert} role="status" title={`${plural(semConta, 'selecionada não tem', 'selecionadas não têm')} conta`}>
              {SEM_CONTA_NO_GRUPO} Elas aparecem como falha no resumo; as outras entram no grupo.
            </Banner>
          ) : null}
        </>
      );
      break;
    case 'bloquear':
      titulo = `Bloquear ${plural(n, 'persona', 'personas')}`;
      icone = Ban;
      rotulo = `Bloquear ${n}`;
      corpo = (
        <p className={styles.detail}>
          Marca a conta como bloqueada pela plataforma: nenhuma tarefa é despachada para estas personas até alguém
          reativá-las. As que já estão bloqueadas ficam como estão.
        </p>
      );
      break;
    case 'reativar':
      titulo = `Reativar ${plural(n, 'persona', 'personas')}`;
      icone = RotateCcw;
      rotulo = `Reativar ${n}`;
      corpo = (
        <p className={styles.detail}>
          Volta a despachar tarefas para estas personas. Reative só depois de a conta voltar de verdade na plataforma.
        </p>
      );
      break;
    case 'apagar':
      titulo = `Apagar ${plural(n, 'persona', 'personas')}`;
      icone = Trash2;
      rotulo = `Apagar ${n}`;
      if (confirmacao.trim().toLowerCase() !== esperado) motivo = `Digite “${esperado}” para confirmar.`;
      corpo = (
        <>
          <Banner tone="danger" icon={TriangleAlert} role="status" title="Não tem volta">
            Apagar é apagar a PESSOA: contas e senhas guardadas no cofre, memória, fotos e histórico vão junto. Persona
            vinculada a aparelho ou com execução em andamento não sai — ela aparece como falha, com o motivo.
          </Banner>
          <Field label={`Para confirmar, digite “${esperado}”`}>
            {({ id }) => (
              <TextInput id={id} value={confirmacao} autoComplete="off" onChange={(e) => setConfirmacao(e.target.value)} />
            )}
          </Field>
        </>
      );
      break;
  }

  const feitos = resultados.filter(Boolean).length;
  const oks = resultados.filter((r) => r?.ok).length;
  const falhas = resultados.filter((r) => r && !r.ok).length;
  const perigoso = operacao === 'apagar';

  return (
    <Dialog
      open
      onClose={onFechar}
      closeBlockedReason={fase === 'executando' ? 'Aguarde terminar.' : null}
      title={titulo}
      icon={icone}
      tone={perigoso ? 'danger' : undefined}
      size="md"
      footer={fase === 'resumo' ? (
        <Button ref={fecharRef} variant="primary" onClick={onFechar}>Fechar</Button>
      ) : (
        <>
          <Button variant="secondary" onClick={onFechar} disabledReason={fase === 'executando' ? 'Aguarde terminar.' : null}>
            Cancelar
          </Button>
          <Button variant={perigoso ? 'danger' : 'primary'} icon={icone} loading={fase === 'executando'}
                  disabledReason={motivo} onClick={() => void confirmar()}>
            {rotulo}
          </Button>
        </>
      )}
    >
      <div className={styles.form}>
        {fase === 'parametros' ? (
          <>
            <p className={styles.detail}>
              {plural(n, 'persona selecionada', 'personas selecionadas')}: {pessoas.slice(0, 6).map(nomeDe).join(', ')}
              {n > 6 ? ` e mais ${n - 6}` : ''}.
            </p>
            {corpo}
          </>
        ) : (
          <>
            <ProgressBar value={n ? feitos / n : 0} label="Progresso da operação" text={`${feitos}/${n}`}
                         tone={fase === 'resumo' ? (falhas ? 'warning' : 'success') : 'accent'} />
            <p className={styles.detail} role="status">
              {fase === 'resumo' ? `Terminado: ${oks} ok · ${falhas} ${falhas === 1 ? 'falhou' : 'falharam'}.`
                : feitos === n ? 'Relendo a lista de personas…' : `Executando, ${CONCORRENCIA_DO_LOTE} por vez…`}
            </p>
            {fase === 'resumo' && listaVelha ? (
              <p className={styles.detail} role="alert">
                A lista de personas não se releu: a que está na tela pode estar desatualizada. Use “Tentar de novo” na
                página antes da próxima ação em lote.
              </p>
            ) : null}
            <ul className={styles.loteLista} aria-label="Resultado por persona" aria-busy={fase === 'executando' || undefined}>
              {pessoas.map((p, i) => {
                const r = resultados[i];
                return (
                  <li key={p.id} className={styles.loteItem} data-status={!r ? 'pending' : r.ok ? 'ok' : 'failed'}>
                    <div className={styles.loteInfo}>
                      <p className={styles.loteNome}>
                        <span className={styles.loteNomeTexto}>{nomeDe(p)}</span>
                        {!r ? <Badge size="sm" tone="neutral">na fila</Badge>
                          : r.ok ? <Badge size="sm" tone="success">ok</Badge>
                            : <Badge size="sm" tone="danger">falhou</Badge>}
                      </p>
                      {r?.ok && r.detalhe ? <p className={styles.loteDetalhe}>{r.detalhe}</p> : null}
                      {r && !r.ok ? <p className={styles.loteErro}>{r.motivo}</p> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </Dialog>
  );
}
