import { Hand, PenLine, SearchX, ShieldAlert, Smartphone, UserRound, Wand2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type { Instance, InstagramProfile, PersonaDTO, PolicyGroup, Worker } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Banner } from '../../components/Banner';
import { Badge } from '../../components/Badge';
import { BarraListagem } from '../../components/BarraListagem';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { Checkbox } from '../../components/Field';
import { AutoGrid, Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { conteudoAoTopo } from '../../lib/scroll';
import { tempoRelativo, useNow } from '../../lib/time';
import { lembrarVisao, visaoPreferida } from '../../lib/visao';
import { metaDaSessao } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { useUiStore } from '../../store/ui';
// Estados de sessão que só uma pessoa resolve: o mesmo conjunto da caixa de Pendências (fila e caixa não divergem).
import { desdeDaSessao, precisaDePessoa } from '../pendencias/modelo';
import { BarraDeLote } from './AcoesEmLote';
import { NovaPersonaManual, NovaPersonaPorPrompt } from './NovaPersona';
import { PolicyGroupsSection } from './PolicyGroups';
import { abaDoPedido, type Aba } from './abas';
import {
  appsDe, CHAVE_VISAO, contagemPorSituacao, filtrarPersonas, filtroAtivo, lerFiltroPersonas, LIMPAR_FILTROS, nomeDoApp,
  ordenarPersonas, queryDoFiltro, ROTULO_SITUACAO, SITUACOES, textoSemResultado, VISOES,
  type FiltroPersonas, type OrdemPersona, type Situacao,
} from './filtroPersonas';
import { PersonaCard, TabelaPersonas } from './ListaDePersonas';
import { ProfileDetail } from './ProfileDetail';
import styles from './Profiles.module.css';
import { homonimosDoSegmento, resolverPersona, slugsDasPersonas } from './slugPersona';


/** Quem tem conta de cadastro, no formato que a fila e os grupos de acesso sempre leram (`username` presente). */
function comConta(pessoas: PersonaDTO[]): InstagramProfile[] {
  return pessoas.flatMap((p) => (p.username ? [{ ...p, username: p.username }] : []));
}

/**
 * Personas (`#/personas`, com `#/personas/<persona>/<guia>` para uma pessoa, onde `<persona>` é o nome legível
 * (`lucas-almeida`) ou o id antigo; `#/perfis` antigo redireciona): as PESSOAS, com e sem conta (`GET /personas`). Cada uma tem identidade,
 * voz, biografia, fotos e as contas dela em cada app; conta, senha e aparelho se ajustam DENTRO da persona.
 */
export function ProfilesPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  // Achado #106: `session.needs_person` não traz o perfil inteiro (só existe por REST) — a batida basta para
  // saber que a fila "Aguardando intervenção" pode ter mudado e recarregar.
  const needsPersonEpoch = useAppStore((s) => s.needsPersonEpoch);
  const instancesMap = useAppStore((s) => s.instances);
  const liveWorkers = useAppStore((s) => s.workers);
  // A persona aberta e a guia vêm do link (`#/personas/<persona>/<guia>`): Voltar do navegador fecha, recarregar
  // reabre. `<persona>` é o slug legível ou o id antigo; quem chega por id é levado ao slug (mesmo lugar, sem histórico).
  const rota = useUiStore((s) => s.rota);
  const navegar = useUiStore((s) => s.navegar);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const voltarPara = useUiStore((s) => s.voltarPara);
  const segmentoAberto = rota.tela === 'personas' ? rota.segmentos[0] ?? null : null;
  const abaAberta = abaDoPedido(rota.segmentos[1]);
  const [pessoas, setPessoas] = useState<PersonaDTO[] | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [grupos, setGrupos] = useState<PolicyGroup[]>([]);
  const [criando, setCriando] = useState<'prompt' | 'manual' | null>(null);
  // Seleção para as operações em lote (v0.34): por id, e só de quem ainda está na lista.
  const [selecionadas, setSelecionadas] = useState<ReadonlySet<string>>(() => new Set());
  // A visão que vale quando o link não traz `visao` (D3): a última escolhida neste navegador.
  const [preferida, setPreferida] = useState(() => visaoPreferida(CHAVE_VISAO, VISOES, 'cards'));
  const token = useRef(0);

  const load = useCallback(async () => {
    const mine = ++token.current;
    const [p, grp] = await Promise.allSettled([api.listPersonas(), api.listPolicyGroups()]);
    if (mine !== token.current) return;
    if (grp.status === 'fulfilled') setGrupos(grp.value);
    if (p.status === 'fulfilled') {
      setPessoas(p.value);
      setErro(null);
    } else {
      // O erro fica na tela, com "Tentar de novo": `[]` aqui dizia "Nenhuma persona" com a API caída, e só um
      // toast passageiro contava a verdade (P1.3).
      setErro(toLoadError(p.reason));
    }
  }, []);

  // Recarrega a cada novo snapshot (reconexão) e a cada mudança na fila "Aguardando intervenção" — perfis não
  // vêm no snapshot, e o evento dedicado só carrega o bastante para saber que algo mudou (achado #106).
  useEffect(() => {
    void load();
  }, [load, hydrateCount, needsPersonEpoch]);

  // O nome legível de cada persona na URL (colisão de nomes resolvida pelo id); sai só do que já foi carregado.
  const slugs = useMemo(() => (pessoas ? slugsDasPersonas(pessoas) : null), [pessoas]);
  const slugDe = useCallback((id: string) => slugs?.get(id) ?? id, [slugs]);

  // Quem o link nomeia. Renomear a persona (guia Persona) muda o slug embaixo do link aberto: enquanto o segmento
  // não muda, vale a última pessoa que ele resolveu (senão a tela mostraria "não encontrada" por um instante).
  const resolvida = useRef<{ segmento: string; id: string } | null>(null);
  let emFoco = segmentoAberto && pessoas ? resolverPersona(segmentoAberto, pessoas) ?? undefined : undefined;
  if (!emFoco && segmentoAberto && pessoas && resolvida.current?.segmento === segmentoAberto) {
    emFoco = pessoas.find((p) => p.id === resolvida.current?.id);
  }
  const idEmFoco = emFoco?.id ?? null;
  useEffect(() => {
    resolvida.current = segmentoAberto && idEmFoco ? { segmento: segmentoAberto, id: idEmFoco } : null;
  }, [segmentoAberto, idEmFoco]);

  // Abrir uma persona e voltar troca o conteúdo sem trocar de seção: sem voltar ao topo, a lista reaparecia rolada.
  useEffect(() => {
    conteudoAoTopo();
  }, [idEmFoco]);

  // O link canônico: slug legível + guia (a Visão geral é a guia sem segmento). Id antigo, caixa diferente, guia
  // inexistente e slug velho depois de um renome são reescritos SUBSTITUINDO a entrada (nada empilha). Só age quando
  // o link difere do canônico, então reler a lista não repete o replace.
  const abaDoLink = rota.segmentos[1];
  useEffect(() => {
    if (!idEmFoco || !slugs || rota.tela !== 'personas') return;
    const aba = abaDoPedido(abaDoLink);
    const esperado = aba === 'visao' ? [slugDe(idEmFoco)] : [slugDe(idEmFoco), aba];
    if (rota.segmentos.length === esperado.length && esperado.every((seg, i) => rota.segmentos[i] === seg)) return;
    navegar({ tela: 'personas', segmentos: esperado, query: rota.query }, 'replace');
  }, [idEmFoco, slugs, slugDe, rota, abaDoLink, navegar]);

  /** Abrir empilha (Voltar do navegador volta à lista); a Visão geral é a guia sem segmento. */
  const abrir = useCallback((id: string, aba: Aba = 'visao') => {
    const alvo = slugDe(id);
    navegar({ tela: 'personas', segmentos: aba === 'visao' ? [alvo] : [alvo, aba] });
  }, [navegar, slugDe]);

  // A lista se releu (apagadas saem, outra tela removeu alguém): a seleção fica só com quem ainda existe.
  useEffect(() => {
    if (!pessoas) return;
    setSelecionadas((atual) => {
      const ids = new Set(pessoas.map((p) => p.id));
      const resta = [...atual].filter((id) => ids.has(id));
      return resta.length === atual.size ? atual : new Set(resta);
    });
  }, [pessoas]);

  const alternarSelecao = useCallback((id: string) => {
    setSelecionadas((atual) => {
      const nova = new Set(atual);
      if (nova.has(id)) nova.delete(id);
      else nova.add(id);
      return nova;
    });
  }, []);

  if (segmentoAberto && emFoco) {
    // Trocar de guia substitui o link (não empilha); "← Personas" volta à lista pelo histórico quando veio dela.
    return <ProfileDetail key={emFoco.id} profile={emFoco} aba={abaAberta}
                          onAbaChange={(a) => navegar({ tela: 'personas', segmentos: a === 'visao' ? [slugDe(emFoco.id)] : [slugDe(emFoco.id), a] }, 'replace')}
                          onBack={() => voltarPara({ tela: 'personas' }, 'push', (de) => de.tela === 'personas' && de.segmentos.length === 0)}
                          onChanged={load} />;
  }

  if (pessoas === null && erro) {
    return <LoadErrorState what="as personas" error={erro} onRetry={() => void load()} />;
  }
  if (!hydrated || pessoas === null) {
    return (
      <LoadingRegion label="Carregando personas…">
        <Skeleton height={140} />
      </LoadingRegion>
    );
  }

  const contas = comConta(pessoas);
  const botoesDeCadastro = (
    <>
      <Button icon={Wand2} variant="primary" onClick={() => setCriando('prompt')}>Nova persona a partir de uma descrição</Button>
      <Button icon={PenLine} onClick={() => setCriando('manual')}>Nova persona manual</Button>
    </>
  );

  async function criada(p: PersonaDTO) {
    setCriando(null);
    await load();
    abrir(p.id);
  }

  // "Abrir" numa pessoa criada pelo lote: a lista se relê antes, senão a recém-criada ainda não estaria nela.
  async function abrirDoLote(id: string) {
    setCriando(null);
    await load();
    abrir(id);
  }

  // Busca, filtros, ordem e visão vêm do link (`#/personas?situacao=bloqueada&q=ana&visao=tabela`): recarregar e
  // colar o link mostram o mesmo recorte. Gravar substitui a entrada (digitar não empilha uma entrada por tecla).
  // Sem `visao` no link (o menu leva à tela limpa), vale a última escolhida neste navegador (D3, `lib/visao.ts`).
  const filtro = lerFiltroPersonas(rota.query, preferida);
  const mudarFiltro = (parcial: Partial<FiltroPersonas>) => {
    if (parcial.visao) {
      lembrarVisao(CHAVE_VISAO, parcial.visao);
      setPreferida(parcial.visao);
    }
    trocarQuery(queryDoFiltro(parcial), 'replace');
  };
  const visiveis = ordenarPersonas(filtrarPersonas(pessoas, filtro), filtro.ordem);
  const contagem = contagemPorSituacao(pessoas, filtro);
  const escondeAlguem = filtroAtivo(filtro);
  const appsConhecidos = [...new Set(pessoas.flatMap(appsDe))].sort();
  // Selecionadas que o filtro escondeu continuam no lote: a barra diz quantas, para ninguém agir sem ver.
  const selecionadasForaDoFiltro = [...selecionadas].filter((id) => !visiveis.some((p) => p.id === id)).length;

  // "Selecionar todas" vale para o que está à vista, não para quem o filtro escondeu.
  const todas = visiveis.length > 0 && visiveis.every((p) => selecionadas.has(p.id));
  const algumas = !todas && visiveis.some((p) => selecionadas.has(p.id));
  function alternarTodas() {
    setSelecionadas((atual) => {
      const nova = new Set(atual);
      for (const p of visiveis) {
        if (todas) nova.delete(p.id);
        else nova.add(p.id);
      }
      return nova;
    });
  }

  return (
    <Page
      title="Personas"
      lead={'Cada persona é uma pessoa: identidade, voz, biografia, fotos e as contas dela em cada app (Instagram, '
        + 'Outlook, TikTok…). A senha de cada conta é digitada dentro da persona e nunca volta: o painel só mostra '
        + 'que existe.'}
      actions={botoesDeCadastro}
    >
      {erro ? <LoadErrorBanner error={erro} onRetry={() => void load()} /> : null}
      {/* Link para uma persona que não está na lista (removida, ou de outro servidor). */}
      {segmentoAberto && !emFoco ? (
        homonimosDoSegmento(segmentoAberto, pessoas).length > 1 ? (
          <Banner tone="warning" icon={ShieldAlert} title="Mais de uma persona com esse nome"
                  actions={<Button size="sm" onClick={() => navegar({ tela: 'personas', query: rota.query }, 'replace')}>Ver todas</Button>}>
            O link não diz qual delas. Escolha uma na lista.
          </Banner>
        ) : (
          <Banner tone="warning" icon={ShieldAlert} title="Persona não encontrada"
                  actions={<Button size="sm" onClick={() => navegar({ tela: 'personas', query: rota.query }, 'replace')}>Ver todas</Button>}>
            O link aponta para uma persona que não está na lista.
          </Banner>
        )
      ) : null}

      <InterventionQueue profiles={contas} instances={instancesMap} workers={liveWorkers} />

      <PolicyGroupsSection grupos={grupos} profiles={contas} onChanged={load} />

      {pessoas.length === 0 ? (
        <EmptyState
          icon={UserRound}
          title="Nenhuma persona cadastrada"
          hint="Descreva a pessoa em poucas palavras (a IA propõe um rascunho para você revisar) ou crie à mão. Contas e aparelho vêm depois."
          actions={botoesDeCadastro}
        >
          Nenhuma persona foi cadastrada ainda.
        </EmptyState>
      ) : (
        <>
          <BarraListagem
            nome="personas"
            busca={{ valor: filtro.q, onChange: (q) => mudarFiltro({ q }), placeholder: 'Buscar por nome ou @' }}
            filtros={[
              { chave: 'situacao', rotulo: 'Situação', tipo: 'chips', rotuloTodos: 'Todas', contagemTodos: contagem.todas,
                valor: filtro.situacao ?? '', onChange: (v) => mudarFiltro({ situacao: (v || null) as Situacao | null }),
                opcoes: SITUACOES.map((s) => ({ valor: s, rotulo: ROTULO_SITUACAO[s], contagem: contagem[s] })) },
              { chave: 'vinculo', rotulo: 'Aparelho vinculado', tipo: 'lista', rotuloTodos: 'Com e sem aparelho',
                valor: filtro.vinculo ?? '', onChange: (v) => mudarFiltro({ vinculo: (v || null) as 'com' | 'sem' | null }),
                opcoes: [{ valor: 'com', rotulo: 'Com aparelho' }, { valor: 'sem', rotulo: 'Sem aparelho' }] },
              { chave: 'grupo', rotulo: 'Grupo de acesso', tipo: 'lista', rotuloTodos: 'Todos os grupos',
                valor: filtro.grupo ?? '', onChange: (v) => mudarFiltro({ grupo: v || null }),
                opcoes: [{ valor: 'nenhum', rotulo: 'Sem grupo' }, ...grupos.map((g) => ({ valor: g.id, rotulo: g.name }))] },
              ...(appsConhecidos.length > 0 ? [{
                chave: 'app', rotulo: 'Aplicativo', tipo: 'lista' as const, rotuloTodos: 'Todos os aplicativos',
                valor: filtro.app ?? '', onChange: (v: string) => mudarFiltro({ app: v || null }),
                opcoes: appsConhecidos.map((a) => ({ valor: a, rotulo: nomeDoApp(a) })),
              }] : []),
            ]}
            ordem={{ valor: filtro.ordem, onChange: (v) => mudarFiltro({ ordem: v as OrdemPersona }),
                     opcoes: [{ valor: 'nome', rotulo: 'Ordem: nome' }, { valor: 'situacao', rotulo: 'Ordem: situação' },
                              { valor: 'atividade', rotulo: 'Ordem: última atividade' }] }}
            visao={{ valor: filtro.visao, onChange: (visao) => mudarFiltro({ visao }) }}
            resumo={escondeAlguem ? `${visiveis.length} de ${pessoas.length} personas` : `${pessoas.length} personas`}
            onLimpar={escondeAlguem ? () => trocarQuery(LIMPAR_FILTROS, 'replace') : undefined}
          />
          <div className={styles.selecaoTopo}>
            <Checkbox label={`Selecionar todas (${visiveis.length})`} aria-label={`Selecionar todas as ${visiveis.length} personas`}
                      checked={todas} indeterminate={algumas} onChange={alternarTodas} disabled={visiveis.length === 0} />
            {selecionadas.size > 0 ? (
              <span className={styles.muted}>
                {selecionadas.size} de {pessoas.length} para as ações em lote
                {selecionadasForaDoFiltro > 0 ? ` (${selecionadasForaDoFiltro} fora do filtro atual)` : ''}
              </span>
            ) : null}
          </div>
          {visiveis.length === 0 ? (
            <EmptyState icon={SearchX} compact title={textoSemResultado(filtro)}
                        hint="Os filtros escondem todas as personas. Limpe os filtros para ver a lista inteira."
                        actions={<Button variant="outline" onClick={() => trocarQuery(LIMPAR_FILTROS, 'replace')}>Limpar filtros</Button>} />
          ) : filtro.visao === 'tabela' ? (
            <TabelaPersonas pessoas={visiveis} selecionadas={selecionadas} onSelecionar={alternarSelecao}
                            onOpen={abrir} onChanged={load} />
          ) : (
            <AutoGrid min="320px">
              {visiveis.map((p) => (
                <PersonaCard key={p.id} pessoa={p} onChanged={load} selecionada={selecionadas.has(p.id)}
                             onSelecionar={() => alternarSelecao(p.id)}
                             onOpen={(aba) => abrir(p.id, aba)} />
              ))}
            </AutoGrid>
          )}
        </>
      )}

      <BarraDeLote selecionadas={pessoas.filter((p) => selecionadas.has(p.id))} grupos={grupos}
                   onLimpar={() => setSelecionadas(new Set())} onConcluido={load} />

      {criando === 'prompt' ? (
        <NovaPersonaPorPrompt onClose={() => setCriando(null)} onCriada={criada} onLote={load}
                              onAbrir={(id) => void abrirDoLote(id)} />
      ) : null}
      {criando === 'manual' ? <NovaPersonaManual onClose={() => setCriando(null)} onCriada={criada} /> : null}
    </Page>
  );
}

/**
 * Fila "Aguardando intervenção" (achado #106): perfil + aparelho + motivo + idade, com um botão que assume o
 * controle e abre a tela certa do aparelho — local ou remoto, pelo mesmo painel de Foco de sempre. Sem isto, a
 * pessoa precisava descobrir sozinha qual perfil estava preso, achar o aparelho e lembrar de assumir o controle.
 */
function InterventionQueue({ profiles, instances, workers }: {
  profiles: InstagramProfile[];
  instances: Record<string, Instance>;
  workers: Readonly<Record<string, Worker>>;
}) {
  const now = useNow();
  const take = useControlStore((s) => s.take);
  const controlBusy = useControlStore((s) => s.busy);
  const openFocus = useUiStore((s) => s.openFocus);

  const itens = useMemo(
    () => profiles
      .filter((p) => precisaDePessoa(p.session))
      // Mais velho primeiro: quem está esperando há mais tempo aparece no topo.
      .sort((a, b) => (desdeDaSessao(a.session) ?? '').localeCompare(desdeDaSessao(b.session) ?? '')),
    [profiles],
  );

  if (itens.length === 0) return null;

  async function assumirEAbrir(instanceId: string) {
    await take(instanceId);
    openFocus(instanceId);
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className={styles.filaTitulo}>
            <ShieldAlert size={18} aria-hidden /> Aguardando intervenção
            <Badge tone="warning">{itens.length}</Badge>
          </span>
        }
        subtitle="Login, desafio de segurança, conta errada ou tela que a automação não reconheceu — só uma pessoa resolve. Assuma o controle e resolva na tela do aparelho; devolver o controle relê a tela sozinho."
      />
      <CardBody>
        <ul className={styles.filaLista}>
          {itens.map((p) => {
            const inst = p.instance_id ? instances[p.instance_id] : undefined;
            const server = inst ? serverHintOf(inst, workers) : null;
            const sess = metaDaSessao(p.session);
            return (
              <li key={p.id} className={styles.filaItem}>
                <Avatar src={profileAvatarUrl(p.id, p.has_avatar)} name={p.display_name || p.username} size={32} />
                <div className={styles.filaInfo}>
                  <p className={styles.filaPerfil}>
                    <span className={styles.filaUsuario}>@{p.username}</span>
                    <StatusBadge meta={sess} />
                  </p>
                  <p className={styles.filaDetalhe}>
                    <Smartphone size={13} aria-hidden />
                    {p.instance_id ?? <span className={styles.muted}>sem aparelho vinculado</span>}
                    {server ? <ServerBadge server={server} size="sm" estatico /> : null}
                    <span className={styles.muted}>· {tempoRelativo(desdeDaSessao(p.session), now)}</span>
                  </p>
                  {p.session.detail ? <p className={styles.filaMotivo}>{p.session.detail}</p> : null}
                </div>
                <Button
                  size="sm"
                  variant="primary"
                  icon={Hand}
                  loading={!!p.instance_id && !!controlBusy[p.instance_id]}
                  disabledReason={!p.instance_id ? 'Sem aparelho vinculado a esta persona.' : null}
                  onClick={() => p.instance_id && void assumirEAbrir(p.instance_id)}
                >
                  Assumir controle
                </Button>
              </li>
            );
          })}
        </ul>
      </CardBody>
    </Card>
  );
}

